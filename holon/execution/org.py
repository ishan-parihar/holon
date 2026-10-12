"""The organization: departments, employees, approval authority, and evidence-based reviews.

Every org term is bound to data here (INTENT.md, law 3):
  department  -> departments row (+ memory scope dept:<slug>)
  HOD         -> departments.head_id
  employee    -> employees row (+ memory scope emp:<id>), persistent across tasks and harnesses
  hire / fire -> create / retire with reason; a successor inherits the predecessor's memory scope
  role / slot -> employees.roles (the six standard roles) filling the slots of the department's genome profile
  authority   -> authority rows; earned and lost from verified outcomes, never from self-report
  review      -> computed from runs, plans, results, and audit samples (no stored counters to drift)
"""

from __future__ import annotations

import json
import sqlite3

from holon.core.db import now, write_txn
from holon.core.ids import new_id
from holon.execution import autonomy
from holon.execution import genome as gn

DUTIES = ("plan", "execute", "verify", "review")
CHAIR = "chair"
EXECUTIVE = "executive"


class OrgError(RuntimeError):
    pass


CHAIR_MAX = 4


def approver_ref(employee_id: str) -> str:
    return f"emp:{employee_id}"


class Org:
    def __init__(self, conn: sqlite3.Connection, actor: str = CHAIR, config: dict | None = None):
        self.conn = conn
        self.actor = actor
        self.config = config or {}

    def policy(self, dept: str | None = None) -> dict:
        """The autonomy policy for a department (slug or id), or the organization's when None."""
        return autonomy.policy(self.config, self.dept(dept)["slug"] if dept else None)

    def _event(self, entity: str, entity_id: str, frm: str | None, to: str | None, note: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events (ts, entity, entity_id, from_status, to_status, actor, note) VALUES (?,?,?,?,?,?,?)",
            (now(), entity, entity_id, frm, to, self.actor, note))

    # ── bootstrap ─────────────────────────────────────────────────────────
    def bootstrap(self) -> None:
        """Every organization has an executive department and a Chair with full authority."""
        with write_txn(self.conn):
            if self.conn.execute("SELECT 1 FROM departments WHERE slug=?", (EXECUTIVE,)).fetchone() is None:
                did = new_id("dept")
                g = gn.load_profile(EXECUTIVE)
                self.conn.execute(
                    "INSERT INTO departments (id, slug, name, mission, profile, genome, created_at) VALUES (?,?,?,?,?,?,?)",
                    (did, EXECUTIVE, "Executive Office",
                     "Turn the Chair's objectives into department mandates; arbitrate across departments.",
                     g["name"], json.dumps(g), now()))
                self._event("dept", did, None, "active", "bootstrap")
            self.conn.execute(
                "INSERT OR IGNORE INTO authority (approver, task_class, max_grade, updated_at, reason) VALUES (?,?,?,?,?)",
                (CHAIR, "*", CHAIR_MAX, now(), "the Chair holds full authority"))

    # ── departments ───────────────────────────────────────────────────────
    def create_dept(self, slug: str, name: str, mission: str | None = None, parent: str | None = None,
                    profile: dict | None = None) -> dict:
        """`profile` is a validated genome profile (genome.load_profile); default: light."""
        parent_id = self.dept(parent)["id"] if parent else self.dept(EXECUTIVE)["id"]
        g = profile or gn.load_profile("light")
        did = new_id("dept")
        with write_txn(self.conn):
            self.conn.execute(
                "INSERT INTO departments (id, slug, name, mission, parent_id, profile, genome, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)", (did, slug, name, mission, parent_id, g["name"], json.dumps(g), now()))
            self._event("dept", did, None, "active", f"created {slug} with profile {g['name']}")
        return self.dept(slug)

    def set_profile(self, dept: str, profile: dict, reason: str) -> dict:
        d = self.dept(dept)
        with write_txn(self.conn):
            self.conn.execute("UPDATE departments SET profile=?, genome=? WHERE id=?",
                              (profile["name"], json.dumps(profile), d["id"]))
            self._event("dept", d["id"], d.get("profile"), profile["name"], f"profile: {reason}")
        return self.dept(dept)

    def staffing(self, dept: str) -> dict:
        d = self.dept(dept)
        g = json.loads(d["genome"]) if d.get("genome") else gn.load_profile("light")
        return {"dept": d["slug"], **gn.staffing(g, self.employees(d["id"]))}

    def slot_roles(self, dept: str, slot: str) -> list[str]:
        st = self.staffing(dept)
        for s in st["slots"]:
            if s["slot"] == slot:
                return s["roles"]
        raise OrgError(f"department {st['dept']} (profile {st['profile']}) has no slot {slot!r}; "
                       f"slots: {[s['slot'] for s in st['slots']]}")

    def dept(self, ref: str) -> dict:
        r = self.conn.execute("SELECT * FROM departments WHERE id=? OR slug=?", (ref, ref)).fetchone()
        if r is None:
            raise LookupError(f"no department {ref}")
        return dict(r)

    def depts(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT d.*, e.name AS head_name FROM departments d LEFT JOIN employees e ON e.id=d.head_id "
            "WHERE d.status='active' ORDER BY d.slug='executive' DESC, d.slug")]

    # ── employees ─────────────────────────────────────────────────────────
    def hire(self, *, name: str, title: str, dept: str, job_description: str, driver: str, reason: str,
             duties: list[str] | None = None, roles: list[str] | None = None, slot: str | None = None,
             head: bool = False, reports_to: str | None = None, task_classes: list[str] | None = None,
             skills: list[str] | None = None, max_concurrency: int = 1, employee_id: str | None = None,
             status: str = "probation") -> dict:
        """Hire into a slot of the department's profile (`slot`), into explicit `roles`, or (legacy) with
        raw `duties`. Duties are derived from roles; a slot or role list containing 'head' makes the hire the head."""
        roles = sorted(set(roles or []) | set(self.slot_roles(dept, slot) if slot else []))
        try:
            duties = sorted(set(duties or []) | set(gn.duties_for(roles)))
        except gn.GenomeError as e:
            raise OrgError(str(e)) from None
        head = head or "head" in roles
        bad = set(duties) - set(DUTIES)
        if bad or not (duties or roles):
            raise OrgError(f"give roles (or a slot); duties must be a subset of {DUTIES}")
        if not job_description.strip():
            raise OrgError("an employee needs a job description")
        d = self.dept(dept)
        if reports_to is None:
            if head:
                parent = self.conn.execute("SELECT head_id FROM departments WHERE id=?", (d["parent_id"],)).fetchone() \
                    if d["parent_id"] else None
                reports_to = parent["head_id"] if parent else None
            else:
                reports_to = d["head_id"]
        eid = employee_id or new_id("employee")
        with write_txn(self.conn):
            if head and d["head_id"]:
                raise OrgError(f"department {d['slug']} already has a head ({d['head_id']}); transfer or fire first")
            self.conn.execute(
                "INSERT INTO employees (id, name, title, dept_id, reports_to, job_description, duties, roles,"
                " task_classes, skills, driver, max_concurrency, status, hired_at, hired_reason)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, name, title, d["id"], reports_to, job_description.strip(), json.dumps(duties),
                 json.dumps(roles) if roles else None,
                 json.dumps(task_classes) if task_classes else None, json.dumps(skills) if skills else None,
                 driver, max_concurrency, status, now(), reason))
            if head:
                self.conn.execute("UPDATE departments SET head_id=? WHERE id=?", (eid, d["id"]))
                if d["slug"] == EXECUTIVE:
                    initial = self.policy(EXECUTIVE)["ceo_initial_max"]
                    self.conn.execute(
                        "INSERT OR REPLACE INTO authority VALUES (?,?,?,?,?)",
                        (approver_ref(eid), "*", initial, now(), "initial CEO authority"))
                    self._event("authority", f"{approver_ref(eid)}/*", None, f"G{initial}",
                                "initial CEO authority (autonomy.ceo_initial_max)")
                # department members without a manager now report to the new head
                self.conn.execute("UPDATE employees SET reports_to=? WHERE dept_id=? AND id<>? AND reports_to IS NULL",
                                  (eid, d["id"], eid))
            self._event("employee", eid, None, status, f"hired as {title} in {d['slug']}: {reason}")
        return self.employee(eid)

    def employee(self, eid: str) -> dict:
        r = self.conn.execute(
            "SELECT e.*, d.slug AS dept, (d.head_id = e.id) AS is_head FROM employees e "
            "JOIN departments d ON d.id=e.dept_id WHERE e.id=?", (eid,)).fetchone()
        if r is None:
            raise LookupError(f"no employee {eid}")
        out = dict(r)
        out["duties"] = json.loads(out["duties"])
        out["task_classes"] = json.loads(out["task_classes"]) if out["task_classes"] else None
        out["skills"] = json.loads(out["skills"]) if out["skills"] else None
        out["is_head"] = bool(out["is_head"])
        out["roles"] = json.loads(out["roles"]) if out["roles"] else gn.infer_roles(out["duties"], out["is_head"])
        return out

    def employees(self, dept: str | None = None, include_retired: bool = False) -> list[dict]:
        q = "SELECT e.id FROM employees e JOIN departments d ON d.id=e.dept_id WHERE 1=1"
        args: list = []
        if dept:
            q += " AND (d.slug=? OR d.id=?)"
            args += [dept, dept]
        if not include_retired:
            q += " AND e.status<>'retired'"
        return [self.employee(r[0]) for r in self.conn.execute(q + " ORDER BY d.slug, e.hired_at", args)]

    def ceo(self) -> dict | None:
        r = self.conn.execute("SELECT head_id FROM departments WHERE slug=?", (EXECUTIVE,)).fetchone()
        return self.employee(r["head_id"]) if r and r["head_id"] else None

    def head_of(self, dept_id: str | None) -> dict | None:
        if not dept_id:
            return None
        r = self.conn.execute("SELECT head_id FROM departments WHERE id=?", (dept_id,)).fetchone()
        return self.employee(r["head_id"]) if r and r["head_id"] else None

    def set_status(self, eid: str, status: str, reason: str) -> dict:
        e = self.employee(eid)
        with write_txn(self.conn):
            self.conn.execute("UPDATE employees SET status=? WHERE id=?", (status, eid))
            self._event("employee", eid, e["status"], status, reason)
        return self.employee(eid)

    def fire(self, eid: str, reason: str, successor: str | None = None) -> dict:
        """Retire an employee. Their memory scope stays and passes to the successor."""
        e = self.employee(eid)
        if e["status"] == "retired":
            raise OrgError(f"{eid} is already retired")
        if successor:
            self.employee(successor)
        with write_txn(self.conn):
            self.conn.execute(
                "UPDATE employees SET status='retired', retired_at=?, retired_reason=?, successor_id=? WHERE id=?",
                (now(), reason, successor, eid))
            if e["is_head"]:
                self.conn.execute("UPDATE departments SET head_id=? WHERE id=?", (successor, e["dept_id"]))
            self.conn.execute("UPDATE employees SET reports_to=? WHERE reports_to=?", (successor, eid))
            self.conn.execute("UPDATE tasks SET assignee=? WHERE assignee=? AND status NOT IN ('done','cancelled')",
                              (successor, eid))
            self.conn.execute("DELETE FROM authority WHERE approver=?", (approver_ref(eid),))
            self._event("employee", eid, e["status"], "retired", f"{reason}" + (f"; successor {successor}" if successor else ""))
        return self.employee(eid)

    def predecessors(self, eid: str) -> list[str]:
        """Everyone whose role (and memory) this employee inherited, transitively."""
        out, frontier = [], [eid]
        while frontier:
            cur = frontier.pop()
            for r in self.conn.execute("SELECT id FROM employees WHERE successor_id=?", (cur,)):
                if r[0] not in out:
                    out.append(r[0])
                    frontier.append(r[0])
        return out

    # ── authority ─────────────────────────────────────────────────────────
    def authority_of(self, approver: str, task_class: str | None) -> int:
        rows = {r["task_class"]: r["max_grade"] for r in self.conn.execute(
            "SELECT task_class, max_grade FROM authority WHERE approver=? AND task_class IN (?, '*')",
            (approver, task_class or "*"))}
        if task_class and task_class in rows:
            return rows[task_class]
        return rows.get("*", -1)

    def set_authority(self, approver: str, task_class: str, max_grade: int, reason: str) -> None:
        """Write authority directly. Callers decide whether a widening needed approval (service.Kernel)."""
        with write_txn(self.conn):
            self.write_authority(approver, task_class, max_grade, reason)

    def write_authority(self, approver: str, task_class: str, max_grade: int, reason: str) -> None:
        """Inside the caller's transaction."""
        if not -1 <= max_grade <= 4:
            raise OrgError("max_grade must be between -1 and 4")
        before = self.authority_of(approver, None if task_class == "*" else task_class)
        self.conn.execute("INSERT OR REPLACE INTO authority VALUES (?,?,?,?,?)",
                          (approver, task_class, max_grade, now(), reason))
        self._event("authority", f"{approver}/{task_class}", f"G{before}", f"G{max_grade}", reason)

    def authority_table(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM authority ORDER BY approver, task_class")]

    def resolve_approver(self, task: dict, grade: int, author: str | None) -> str:
        """The lowest authority in the chain (HOD → CEO → Chair) whose ceiling covers the grade.
        Nobody approves their own plan."""
        chain = []
        hod = self.head_of(task.get("dept_id"))
        if hod and hod["status"] in ("active", "probation"):
            chain.append(hod)
        ceo = self.ceo()
        if ceo and ceo["status"] in ("active", "probation") and all(c["id"] != ceo["id"] for c in chain):
            chain.append(ceo)
        for emp in chain:
            if emp["id"] == author or "review" not in emp["duties"]:
                continue
            if self.authority_of(approver_ref(emp["id"]), task.get("task_class")) >= grade:
                return approver_ref(emp["id"])
        return CHAIR

    def should_sample(self, plan_id: str, dept: str | None = None) -> bool:
        """Holdout audit: a fixed share of employee approvals is re-judged by the Chair."""
        return autonomy.sampled(f"audit:{plan_id}", self.policy(dept)["audit_sample_permille"] / 1000)

    def approver_track_record(self, approver: str, task_class: str | None) -> dict:
        """Outcomes of the plans this approver signed off, in one task class, since their authority last changed."""
        key = task_class or "*"
        since = self.conn.execute(
            "SELECT coalesce(max(ts), '') FROM events WHERE entity='authority' AND entity_id IN (?, ?)",
            (f"{approver}/{key}", f"{approver}/*")).fetchone()[0]
        rows = self.conn.execute(
            "SELECT t.status, p.decided_at, (SELECT r.verified FROM runs u JOIN results r ON r.run_id=u.id "
            "  WHERE u.plan_id=p.id AND u.kind='execute' AND r.verified IS NOT NULL"
            "  ORDER BY r.created_at DESC LIMIT 1) AS verified "
            "FROM plans p JOIN tasks t ON t.id=p.task_id "
            "WHERE p.decided_by=? AND p.status='approved' AND coalesce(t.task_class,'*')=? AND p.decided_at>? "
            "ORDER BY p.decided_at", (approver, key, since)).fetchall()
        verified_ok = sum(1 for r in rows if r["verified"] == 1)
        failed = sum(1 for r in rows if r["verified"] == 0)
        disagreed = self.conn.execute(
            "SELECT count(*) FROM audit_samples a JOIN plans p ON p.id=a.plan_id JOIN tasks t ON t.id=p.task_id "
            "WHERE a.approver=? AND a.verdict='disagree' AND coalesce(t.task_class,'*')=? AND a.judged_at>?",
            (approver, key, since)).fetchone()[0]
        return {"approved": len(rows), "verified_ok": verified_ok, "verification_failed": failed,
                "audit_disagreements": disagreed, "since": since or None}

    def earned_authority_change(self, approver: str, task_class: str | None, promote_after: int,
                                cap: int) -> dict | None:
        """What the record says the approver's authority should become: down one on any incident since the
        last change, up one after `promote_after` clean verified successes (never above `cap`). The Chair is fixed.
        Returns the proposed change; the caller applies demotions at once and routes promotions per autonomy policy."""
        if approver == CHAIR:
            return None
        key = task_class or "*"
        current = self.authority_of(approver, task_class)
        rec = self.approver_track_record(approver, task_class)
        if rec["verification_failed"] or rec["audit_disagreements"]:
            new = max(-1, current - 1)
            reason = (f"demoted: {rec['verification_failed']} failed verification(s), "
                      f"{rec['audit_disagreements']} audit disagreement(s) since last change")
        elif rec["verified_ok"] >= promote_after and current < cap:
            new = current + 1
            reason = f"promoted: {rec['verified_ok']} verified successes with no incidents"
        else:
            return None
        return {"approver": approver, "task_class": key, "from": current, "to": new, "reason": reason}

    # ── reviews (evidence, not narrative) ─────────────────────────────────
    def review(self, eid: str) -> dict:
        e = self.employee(eid)
        runs = {r["kind"]: dict(r) for r in self.conn.execute(
            "SELECT kind, count(*) AS runs, sum(status='succeeded') AS succeeded, sum(status IN ('failed','timeout'))"
            " AS failed, sum(coalesce(tokens_in,0)+coalesce(tokens_out,0)) AS tokens, max(started_at) AS last_run "
            "FROM runs WHERE employee_id=? GROUP BY kind", (eid,))}
        executed = self.conn.execute(
            "SELECT count(*), sum(r.verified=1), sum(r.verified=0) FROM runs u JOIN results r ON r.run_id=u.id "
            "WHERE u.employee_id=? AND u.kind='execute'", (eid,)).fetchone()
        authored = self.conn.execute(
            "SELECT count(*), sum(version=1 AND status='approved'), sum(status='rejected') FROM plans WHERE author=?",
            (eid,)).fetchone()
        approved = self.approver_track_record(approver_ref(eid), None)
        return {
            "employee": {k: e[k] for k in ("id", "name", "title", "dept", "status", "is_head", "hired_at")},
            "runs_by_duty": runs,
            "execution": {"results": executed[0], "verified_ok": executed[1] or 0, "verified_failed": executed[2] or 0},
            "planning": {"plans": authored[0], "approved_first_try": authored[1] or 0, "rejected": authored[2] or 0},
            "approving": approved,
            "authority": [r for r in self.authority_table() if r["approver"] == approver_ref(eid)],
        }
