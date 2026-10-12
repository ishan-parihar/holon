"""work.db: the delegated-work state machine. The only writer of task/plan/run/result rows.

    inbox -> planning -> awaiting_approval -> approved -> executing -> verifying -> done
              |  \\-> needs_input -> inbox      ^  |                 |           \\-> failed
              |                                 |  \\-> stale -> inbox            (retry / replan)
              \\-> decompose approved: waiting_on_subtasks -> done when all sub-tasks close

Who may claim what follows the organization (see `Claimant`):
  plan     the assignee (with the plan duty) or the department's HOD; un-triaged work: the CEO
  execute  the plan's/task's assignee; else any member of the task's department
  verify   any member of the task's department (or the executive) except the executor
  review   the approver the plan requires (HOD / CEO), within their authority
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from holon.core.db import now, write_txn
from holon.core.ids import new_id
from holon.execution.staleness import fingerprint

TRANSITIONS: dict[str, set[str]] = {
    "inbox": {"planning", "cancelled"},
    "planning": {"awaiting_approval", "needs_input", "inbox", "cancelled"},
    "needs_input": {"inbox", "cancelled"},
    "awaiting_approval": {"approved", "waiting_on_subtasks", "inbox", "cancelled"},
    "approved": {"executing", "inbox", "cancelled"},
    "executing": {"verifying", "approved", "failed", "cancelled"},
    "verifying": {"done", "approved", "failed", "cancelled"},
    "failed": {"approved", "inbox", "needs_input", "cancelled"},
    "waiting_on_subtasks": {"done", "needs_input", "cancelled"},
    "done": set(),
    "cancelled": set(),
}
TERMINAL = {"done", "cancelled"}
DEFAULT_LEASE_S = {"plan": 1800, "execute": 3600, "verify": 1200, "review": 900}
SOURCES = ("chair", "agent", "audit", "schedule")


class TransitionError(RuntimeError):
    pass


class NotFound(LookupError):
    pass


@dataclass
class Claimant:
    """Who is claiming. With no id: an unaffiliated pull-mode worker that may claim anything."""
    id: str | None = None
    dept_id: str | None = None
    is_head: bool = False
    is_ceo: bool = False
    in_executive: bool = False

    @property
    def ref(self) -> str | None:
        return f"emp:{self.id}" if self.id else None


def _lease(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def _dumps(v: Any) -> str | None:
    if v is None:
        return None
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


class Work:
    def __init__(self, conn: sqlite3.Connection, actor: str = "chair"):
        self.conn = conn
        self.actor = actor
        # kernel-applied decisions: a proposal plan carries {"apply": {"kind": k, ...}}; approving it calls
        # appliers[k](payload) inside the approval transaction, so the decision and its effect commit together
        self.appliers: dict[str, Any] = {}

    # ── primitives ────────────────────────────────────────────────────────
    def _event(self, entity: str, entity_id: str, frm: str | None, to: str | None, note: str | None = None,
               actor: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events (ts, entity, entity_id, from_status, to_status, actor, note) VALUES (?,?,?,?,?,?,?)",
            (now(), entity, entity_id, frm, to, actor or self.actor, note))

    def _move(self, task_id: str, to: str, note: str | None = None, actor: str | None = None,
              expect: set[str] | None = None, blocked_reason: str | None = None) -> dict:
        task = self.get_task(task_id)
        frm = task["status"]
        if expect is not None and frm not in expect:
            raise TransitionError(f"task {task_id} is '{frm}', expected one of {sorted(expect)}")
        if to not in TRANSITIONS[frm]:
            raise TransitionError(f"illegal transition {frm} -> {to} for task {task_id}")
        self.conn.execute(
            "UPDATE tasks SET status=?, blocked_reason=?, updated_at=?, sync_dirty=1 WHERE id=? AND status=?",
            (to, blocked_reason, now(), task_id, frm))
        self._event("task", task_id, frm, to, note, actor)
        task.update(status=to, blocked_reason=blocked_reason)
        return task

    def _dept_id(self, ref: str | None) -> str | None:
        if not ref:
            return None
        r = self.conn.execute("SELECT id FROM departments WHERE id=? OR slug=?", (ref, ref)).fetchone()
        if r is None:
            raise NotFound(f"no department {ref}")
        return r["id"]

    # ── reads ─────────────────────────────────────────────────────────────
    def get_task(self, task_id: str) -> dict:
        t = _row(self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
        if t is None:
            raise NotFound(f"no task {task_id}")
        return t

    def get_plan(self, plan_id: str) -> dict:
        p = _row(self.conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone())
        if p is None:
            raise NotFound(f"no plan {plan_id}")
        return p

    def get_run(self, run_id: str) -> dict:
        r = _row(self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
        if r is None:
            raise NotFound(f"no run {run_id}")
        return r

    def latest_plan(self, task_id: str, status: str | None = None) -> dict | None:
        q = "SELECT * FROM plans WHERE task_id=?" + (" AND status=?" if status else "") + " ORDER BY version DESC LIMIT 1"
        args = (task_id, status) if status else (task_id,)
        return _row(self.conn.execute(q, args).fetchone())

    def plans_for(self, task_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM plans WHERE task_id=? ORDER BY version", (task_id,))]

    def result_for_run(self, run_id: str) -> dict | None:
        return _row(self.conn.execute("SELECT * FROM results WHERE run_id=?", (run_id,)).fetchone())

    def latest_result(self, task_id: str) -> dict | None:
        return _row(self.conn.execute(
            "SELECT r.* FROM results r JOIN runs u ON u.id=r.run_id WHERE u.task_id=? AND u.kind='execute' "
            "ORDER BY r.created_at DESC LIMIT 1", (task_id,)).fetchone())

    def executor_of(self, task_id: str) -> str | None:
        r = self.conn.execute("SELECT employee_id FROM runs WHERE task_id=? AND kind='execute' ORDER BY started_at DESC"
                              " LIMIT 1", (task_id,)).fetchone()
        return r[0] if r else None

    def list_tasks(self, status: str | None = None, project_ref: str | None = None, parent_id: str | None = None,
                   dept_id: str | None = None, assignee: str | None = None, limit: int = 50) -> list[dict]:
        q, args = "SELECT * FROM tasks WHERE 1=1", []
        if status:
            q += " AND status IN (%s)" % ",".join("?" * len(status.split(",")))
            args += status.split(",")
        for col, val in (("project_ref", project_ref), ("parent_id", parent_id), ("dept_id", dept_id),
                         ("assignee", assignee)):
            if val:
                q += f" AND {col}=?"
                args.append(val)
        q += " ORDER BY priority, created_at LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(q, args)]

    def awaiting_approval(self, approver: str | None = "chair") -> list[dict]:
        q = ("SELECT p.*, t.title AS task_title, t.priority, t.risk, t.task_class FROM plans p "
             "JOIN tasks t ON t.id=p.task_id WHERE p.status='awaiting_approval'")
        args: list = []
        if approver:
            q += " AND p.approver=?"
            args.append(approver)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY p.grade DESC, t.priority, p.created_at", args)]

    def events(self, entity_id: str | None = None, limit: int = 50) -> list[dict]:
        if entity_id:
            rows = self.conn.execute("SELECT * FROM events WHERE entity_id=? ORDER BY seq DESC LIMIT ?", (entity_id, limit))
        else:
            rows = self.conn.execute("SELECT * FROM events ORDER BY seq DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.conn.execute("SELECT status, count(*) FROM tasks GROUP BY status")}

    def ancestry(self, task_id: str) -> list[dict]:
        chain, cur = [], self.get_task(task_id)
        while cur.get("parent_id"):
            cur = self.get_task(cur["parent_id"])
            chain.append(cur)
        return chain

    # ── intake ────────────────────────────────────────────────────────────
    def add_task(self, title: str, intent: str, *, acceptance: str | None = None, project_ref: str | None = None,
                 parent_id: str | None = None, dept: str | None = None, assignee: str | None = None,
                 requested_by: str | None = None, priority: int = 3, source: str = "chair",
                 task_class: str | None = None, risk: str = "normal", workdir: str | None = None,
                 due: str | None = None, max_attempts: int = 2) -> dict:
        if not title.strip() or not intent.strip():
            raise ValueError("title and intent are required")
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
        tid, ts = new_id("task"), now()
        with write_txn(self.conn):
            if parent_id:
                self.get_task(parent_id)
            dept_id = self._dept_id(dept)
            if assignee:
                emp = self.conn.execute("SELECT dept_id, status FROM employees WHERE id=?", (assignee,)).fetchone()
                if emp is None or emp["status"] == "retired":
                    raise NotFound(f"no active employee {assignee}")
                dept_id = dept_id or emp["dept_id"]
            self.conn.execute(
                "INSERT INTO tasks (id, project_ref, parent_id, dept_id, assignee, requested_by, title, intent, acceptance,"
                " priority, source, task_class, risk, workdir, due, max_attempts, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, project_ref, parent_id, dept_id, assignee, requested_by or self.actor, title.strip(),
                 intent.strip(), acceptance, priority, source, task_class, risk, workdir, due, max_attempts, ts, ts))
            self._event("task", tid, None, "inbox", f"source={source}")
        return self.get_task(tid)

    # ── claiming ──────────────────────────────────────────────────────────
    def _open_run(self, task_id: str, plan_id: str | None, kind: str, who: Claimant, driver: str | None,
                  lease_s: int) -> dict:
        attempt = 1
        if kind == "execute":
            attempt = 1 + self.conn.execute(
                "SELECT count(*) FROM runs WHERE task_id=? AND kind='execute' AND plan_id=?", (task_id, plan_id)
            ).fetchone()[0]
        rid, ts = new_id("run"), now()
        self.conn.execute(
            "INSERT INTO runs (id, task_id, plan_id, kind, employee_id, driver, attempt, lease_until, heartbeat_at,"
            " started_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, task_id, plan_id, kind, who.id, driver, attempt, _lease(lease_s), ts, ts))
        self._event("run", rid, None, "running", f"{kind} for {task_id}", actor=who.ref or self.actor)
        return self.get_run(rid)

    @staticmethod
    def _planning_scope(who: Claimant) -> tuple[str, list]:
        if not who.id:
            return "1=1", []
        parts, args = ["assignee=?"], [who.id]
        if who.is_head and who.dept_id:
            parts.append("dept_id=?")  # an HOD plans any department work, assigned or not
            args.append(who.dept_id)
        if who.is_ceo:
            parts.append("(assignee IS NULL AND dept_id IS NULL)")
        return " OR ".join(parts), args

    def claim_for_planning(self, who: Claimant, driver: str | None = None, lease_s: int = DEFAULT_LEASE_S["plan"],
                           task_id: str | None = None) -> tuple[dict, dict] | None:
        cond, args = self._planning_scope(who)
        with write_txn(self.conn):
            q = f"SELECT id FROM tasks WHERE status='inbox' AND ({cond})"
            if task_id:
                q += " AND id=?"
                args = [*args, task_id]
            row = self.conn.execute(q + " ORDER BY priority, created_at LIMIT 1", args).fetchone()
            if row is None:
                return None
            task = self._move(row["id"], "planning", f"claimed by {who.ref or self.actor}", actor=who.ref)
            run = self._open_run(task["id"], None, "plan", who, driver, lease_s)
        return task, run

    def claim_for_execution(self, who: Claimant, driver: str | None = None, lease_s: int = DEFAULT_LEASE_S["execute"],
                            task_id: str | None = None) -> tuple[dict, dict, dict] | None:
        """Claim the next approved task this employee may execute. Plans whose written paths
        changed since approval are superseded and their tasks return to the inbox."""
        while True:
            with write_txn(self.conn):
                q = ("SELECT t.id FROM tasks t JOIN plans p ON p.task_id=t.id AND p.status='approved' "
                     "WHERE t.status='approved'")
                args: list = []
                if who.id:
                    q += (" AND (coalesce(p.assignee, t.assignee)=? OR (coalesce(p.assignee, t.assignee) IS NULL AND"
                          " (t.dept_id=? OR (t.dept_id IS NULL AND ?))))")
                    args += [who.id, who.dept_id, 1 if who.in_executive else 0]
                if task_id:
                    q += " AND t.id=?"
                    args.append(task_id)
                row = self.conn.execute(q + " ORDER BY t.priority, t.created_at LIMIT 1", args).fetchone()
                if row is None:
                    return None
                task = self.get_task(row["id"])
                plan = self.latest_plan(task["id"], "approved")
                if plan["writes_fingerprint"]:
                    current = fingerprint(task["workdir"], json.loads(plan["writes"] or "[]"))
                    if current != plan["writes_fingerprint"]:
                        self.conn.execute("UPDATE plans SET status='superseded' WHERE id=?", (plan["id"],))
                        self._event("plan", plan["id"], "approved", "superseded", "written paths changed since approval")
                        self._move(task["id"], "inbox", "stale plan: paths changed", blocked_reason="stale_plan")
                        if task_id:
                            return None
                        continue
                task = self._move(task["id"], "executing", f"claimed by {who.ref or self.actor}", actor=who.ref)
                run = self._open_run(task["id"], plan["id"], "execute", who, driver, lease_s)
            return task, plan, run

    def claim_for_verification(self, who: Claimant, driver: str | None = None,
                               lease_s: int = DEFAULT_LEASE_S["verify"]) -> tuple[dict, dict] | None:
        with write_txn(self.conn):
            q = ("SELECT t.id FROM tasks t WHERE t.status='verifying' AND NOT EXISTS ("
                 " SELECT 1 FROM runs r WHERE r.task_id=t.id AND r.kind='verify' AND r.status='running')")
            args: list = []
            if who.id:
                q += (" AND coalesce((SELECT employee_id FROM runs WHERE task_id=t.id AND kind='execute'"
                      " ORDER BY started_at DESC LIMIT 1), '') <> ?"
                      " AND (t.dept_id=? OR t.dept_id IS NULL OR ?)")
                args += [who.id, who.dept_id, 1 if who.in_executive else 0]
            row = self.conn.execute(q + " ORDER BY t.priority, t.updated_at LIMIT 1", args).fetchone()
            if row is None:
                return None
            task = self.get_task(row["id"])
            plan = self.latest_plan(task["id"], "approved")
            run = self._open_run(task["id"], plan["id"] if plan else None, "verify", who, driver, lease_s)
        return task, run

    def claim_for_review(self, who: Claimant, driver: str | None = None,
                         lease_s: int = DEFAULT_LEASE_S["review"]) -> tuple[dict, dict, dict] | None:
        if not who.id:
            return None
        with write_txn(self.conn):
            row = self.conn.execute(
                "SELECT p.id FROM plans p JOIN tasks t ON t.id=p.task_id WHERE p.status='awaiting_approval' "
                "AND p.approver=? AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.plan_id=p.id AND r.kind='review' "
                "AND r.status='running') ORDER BY t.priority, p.created_at LIMIT 1", (who.ref,)).fetchone()
            if row is None:
                return None
            plan = self.get_plan(row["id"])
            task = self.get_task(plan["task_id"])
            run = self._open_run(task["id"], plan["id"], "review", who, driver, lease_s)
        return task, plan, run

    def _close_run(self, run_id: str, status: str, exit_code: int | None = None, **metrics: Any) -> None:
        run = self.get_run(run_id)
        if run["status"] != "running":
            raise TransitionError(f"run {run_id} is already '{run['status']}'")
        self.conn.execute(
            "UPDATE runs SET status=?, ended_at=?, exit_code=?, tokens_in=?, tokens_out=?, cost=? WHERE id=?",
            (status, now(), exit_code, metrics.get("tokens_in"), metrics.get("tokens_out"), metrics.get("cost"), run_id))
        self._event("run", run_id, "running", status, actor=f"emp:{run['employee_id']}" if run["employee_id"] else None)

    def heartbeat(self, run_id: str, lease_s: int | None = None) -> dict:
        run = self.get_run(run_id)
        if run["status"] != "running":
            raise TransitionError(f"run {run_id} is '{run['status']}'")
        with write_txn(self.conn):
            self.conn.execute("UPDATE runs SET heartbeat_at=?, lease_until=? WHERE id=?",
                              (now(), _lease(lease_s or DEFAULT_LEASE_S[run["kind"]]), run_id))
        return self.get_run(run_id)

    def set_run_paths(self, run_id: str, brief_path: str, log_path: str) -> None:
        with write_txn(self.conn):
            self.conn.execute("UPDATE runs SET brief_path=?, log_path=? WHERE id=?", (brief_path, log_path, run_id))

    # ── planning ──────────────────────────────────────────────────────────
    def submit_plan(self, run_id: str, env: dict, grading: dict | None = None, approver: str = "chair",
                    exit_code: int | None = 0) -> dict:
        """Ingest a validated planner result (org references already resolved by the caller)."""
        run = self.get_run(run_id)
        if run["kind"] != "plan":
            raise TransitionError(f"run {run_id} is a {run['kind']} run, not a plan run")
        author = run["employee_id"]
        actor = f"emp:{author}" if author else None
        with write_txn(self.conn):
            task = self.get_task(run["task_id"])
            if task["status"] != "planning":
                raise TransitionError(f"task {task['id']} is '{task['status']}', not planning")
            self._close_run(run_id, "succeeded", exit_code)
            if env["kind"] == "needs_input":
                self._message(sender=actor or "planner", to_ref="chair", kind="question", task_id=task["id"],
                              body=env["question"], ack_required=1)
                task = self._move(task["id"], "needs_input", "planner needs input", actor=actor,
                                  blocked_reason=env["question"])
                return {"task": task, "question": env["question"]}
            version = 1 + self.conn.execute("SELECT coalesce(max(version),0) FROM plans WHERE task_id=?",
                                            (task["id"],)).fetchone()[0]
            self.conn.execute("UPDATE plans SET status='superseded' WHERE task_id=? AND status='awaiting_approval'",
                              (task["id"],))
            g = grading or {"proposed": None, "floor": 4, "grade": 4, "reasons": ["ungraded"]}
            pid = new_id("plan")
            if env["kind"] == "decompose":
                steps, findings, writes, fp = _dumps(env["children"]), env["rationale"], None, None
            else:
                steps = env["steps"] if isinstance(env["steps"], str) else _dumps(env["steps"])
                findings, writes = env["findings"], env.get("writes") or []
                fp = fingerprint(task["workdir"], writes)
            self.conn.execute(
                "INSERT INTO plans (id, task_id, version, kind, author, findings, writes_fingerprint, steps, writes,"
                " external_effects, assignee, risks, rollback, estimate, context_refs, grade_proposed, grade_floor,"
                " grade, grade_reasons, approver, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, task["id"], version, "decompose" if env["kind"] == "decompose" else "execute", author, findings,
                 fp, steps, _dumps(writes), _dumps(env.get("external_effects")), env.get("assignee"),
                 env.get("risks"), env.get("rollback"), env.get("estimate"), _dumps(env.get("context_refs")),
                 g["proposed"], g["floor"], g["grade"], _dumps(g["reasons"]), approver, now()))
            self.conn.execute("UPDATE runs SET plan_id=? WHERE id=?", (pid, run_id))
            if env.get("acceptance") and not task["acceptance"]:
                self.conn.execute("UPDATE tasks SET acceptance=? WHERE id=?", (env["acceptance"], task["id"]))
            self._event("plan", pid, None, "awaiting_approval", f"v{version} G{g['grade']} -> {approver}", actor=actor)
            task = self._move(task["id"], "awaiting_approval", f"plan {pid} v{version}", actor=actor)
        return {"task": task, "plan": self.get_plan(pid)}

    def set_grade(self, plan_id: str, grade: int, approver: str, note: str) -> dict:
        with write_txn(self.conn):
            plan = self.get_plan(plan_id)
            if plan["status"] != "awaiting_approval":
                raise TransitionError(f"plan {plan_id} is '{plan['status']}'")
            self.conn.execute("UPDATE plans SET grade=?, approver=? WHERE id=?", (grade, approver, plan_id))
            self._event("plan", plan_id, f"G{plan['grade']}", f"G{grade}", f"regraded -> {approver}: {note}")
        return self.get_plan(plan_id)

    # ── kernel proposals (decisions the organization applies itself once approved) ──
    def propose(self, *, title: str, intent: str, steps: str, apply: dict, approver: str, grade: int,
                task_class: str, dept: str | None = None, author: str | None = None) -> dict:
        """A decision for an approver that needs no executor: the kernel applies `apply` on approval.
        Idempotent by title while one is pending."""
        if apply.get("kind") not in self.appliers:
            raise ValueError(f"no applier for {apply.get('kind')!r}")
        with write_txn(self.conn):
            r = self.conn.execute(
                "SELECT p.id FROM plans p JOIN tasks t ON t.id=p.task_id WHERE t.title=? AND p.status='awaiting_approval'",
                (title,)).fetchone()
            if r:
                return self.get_plan(r["id"])
            tid, pid, ts = new_id("task"), new_id("plan"), now()
            self.conn.execute(
                "INSERT INTO tasks (id, dept_id, requested_by, title, intent, acceptance, priority, source, task_class,"
                " risk, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, self._dept_id(dept), author or self.actor, title, intent, "applied by the kernel on approval",
                 2, "audit", task_class, "normal", "awaiting_approval", ts, ts))
            self._event("task", tid, None, "awaiting_approval", "kernel proposal")
            self.conn.execute(
                "INSERT INTO plans (id, task_id, version, kind, findings, steps, context_refs, grade_proposed,"
                " grade_floor, grade, grade_reasons, approver, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, tid, 1, "execute", intent, steps, _dumps({"apply": apply}), grade, grade, grade,
                 _dumps([f"{task_class}: routed by the autonomy policy"]), approver, ts))
            self._event("plan", pid, None, "awaiting_approval", f"proposal -> {approver}")
        return self.get_plan(pid)

    @staticmethod
    def applied_payload(plan: dict) -> dict | None:
        try:
            refs = json.loads(plan.get("context_refs") or "null")
        except json.JSONDecodeError:
            return None
        return refs.get("apply") if isinstance(refs, dict) else None

    # ── approval ──────────────────────────────────────────────────────────
    def _decider(self, plan: dict, review_run: str | None) -> str:
        decider = self.actor
        if review_run:
            decider = f"emp:{self.get_run(review_run)['employee_id']}"
            self._close_run(review_run, "succeeded")
        if decider != "chair" and decider != plan["approver"]:
            raise TransitionError(f"{decider} may not decide plan {plan['id']} (requires {plan['approver']})")
        if plan["author"] and decider == f"emp:{plan['author']}":
            raise TransitionError("nobody decides their own plan")
        return decider

    def approve(self, plan_id: str, note: str | None = None, recurring: bool = False,
                review_run: str | None = None) -> dict:
        """The Chair may approve any plan. An employee may approve only plans routed to them."""
        with write_txn(self.conn):
            plan = self.get_plan(plan_id)
            if plan["status"] != "awaiting_approval":
                raise TransitionError(f"plan {plan_id} is '{plan['status']}'")
            decider = self._decider(plan, review_run)
            task = self.get_task(plan["task_id"])
            self.conn.execute(
                "UPDATE plans SET status='approved', review_note=?, decided_by=?, decided_at=?, recurring=? WHERE id=?",
                (note, decider, now(), 1 if recurring else 0, plan_id))
            self._event("plan", plan_id, "awaiting_approval", "approved", note, actor=decider)
            if plan["assignee"]:
                self.conn.execute("UPDATE tasks SET assignee=? WHERE id=?", (plan["assignee"], task["id"]))
            children = []
            if plan["kind"] == "decompose":
                self._move(task["id"], "waiting_on_subtasks", f"split by {plan_id}", actor=decider)
                for c in json.loads(plan["steps"]):
                    cid, ts = new_id("task"), now()
                    dept_id = self._dept_id(c.get("dept")) or task["dept_id"]
                    self.conn.execute(
                        "INSERT INTO tasks (id, project_ref, parent_id, dept_id, assignee, requested_by, title, intent,"
                        " acceptance, priority, source, task_class, risk, workdir, max_attempts, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, task["project_ref"], task["id"], dept_id, c.get("assignee"),
                         f"emp:{plan['author']}" if plan["author"] else decider, c["title"], c["intent"],
                         c.get("acceptance"), c.get("priority", task["priority"]), "agent",
                         c.get("task_class", task["task_class"]), c.get("risk", task["risk"]),
                         c.get("workdir", task["workdir"]), task["max_attempts"], ts, ts))
                    self._event("task", cid, None, "inbox", f"sub-task of {task['id']}", actor=decider)
                    children.append(cid)
            else:
                self._move(task["id"], "approved", f"plan {plan_id} approved", actor=decider)
                payload = self.applied_payload(plan)
                if payload:
                    self.appliers[payload["kind"]](payload)
                    for to in ("executing", "verifying", "done"):
                        self._move(task["id"], to, "applied by the kernel", actor=decider)
        return {"plan": self.get_plan(plan_id), "task": self.get_task(task["id"]), "children": children,
                "decider": decider}

    def reject(self, plan_id: str, note: str, review_run: str | None = None) -> dict:
        if not note or not note.strip():
            raise ValueError("a rejection needs a review note: it is what the planner learns from")
        with write_txn(self.conn):
            plan = self.get_plan(plan_id)
            if plan["status"] != "awaiting_approval":
                raise TransitionError(f"plan {plan_id} is '{plan['status']}'")
            decider = self._decider(plan, review_run)
            self.conn.execute("UPDATE plans SET status='rejected', review_note=?, decided_by=?, decided_at=? WHERE id=?",
                              (note, decider, now(), plan_id))
            self._event("plan", plan_id, "awaiting_approval", "rejected", note, actor=decider)
            if self.applied_payload(plan):  # a rejected proposal is closed, not replanned
                task = self._move(plan["task_id"], "cancelled", f"proposal {plan_id} rejected", actor=decider)
            else:
                task = self._move(plan["task_id"], "inbox", f"plan {plan_id} rejected: replan", actor=decider)
        return {"plan": self.get_plan(plan_id), "task": task, "decider": decider}

    def answer(self, task_id: str, answer: str) -> dict:
        with write_txn(self.conn):
            self._message(sender=self.actor, to_ref="planner", kind="answer", task_id=task_id, body=answer)
            self.conn.execute("UPDATE messages SET acked_at=? WHERE task_id=? AND ack_required=1 AND acked_at IS NULL",
                              (now(), task_id))
            task = self._move(task_id, "inbox", "input provided", expect={"needs_input"})
        return task

    # ── execution + verification ──────────────────────────────────────────
    def submit_result(self, run_id: str, env: dict, exit_code: int | None = 0, **metrics: Any) -> dict:
        run = self.get_run(run_id)
        if run["kind"] != "execute":
            raise TransitionError(f"run {run_id} is a {run['kind']} run, not an execute run")
        actor = f"emp:{run['employee_id']}" if run["employee_id"] else None
        with write_txn(self.conn):
            task = self.get_task(run["task_id"])
            failed = env["outcome"] == "failure"
            self._close_run(run_id, "failed" if failed else "succeeded", exit_code, **metrics)
            self.conn.execute(
                "INSERT INTO results (run_id, outcome, summary, documentation, artifacts, learnings, decisions, blockers,"
                " proposals, next_intent, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, env["outcome"], env["summary"], env["documentation"], _dumps(env.get("artifacts")),
                 _dumps(env.get("learnings")), _dumps(env.get("decisions")), _dumps(env.get("blockers")),
                 _dumps(env.get("proposals")), env.get("next_intent"), now()))
            if failed:
                task = self._fail_or_retry(task, run, f"execution failed: {env['summary'][:200]}")
            else:
                task = self._move(task["id"], "verifying", f"result {env['outcome']}", actor=actor)
        return {"task": task, "run": self.get_run(run_id), "result": self.result_for_run(run_id)}

    def _fail_or_retry(self, task: dict, run: dict, reason: str) -> dict:
        actor = f"emp:{run['employee_id']}" if run.get("employee_id") else None
        attempts = self.conn.execute(
            "SELECT count(*) FROM runs WHERE task_id=? AND kind='execute' AND plan_id=?", (task["id"], run["plan_id"])
        ).fetchone()[0]
        if attempts < task["max_attempts"]:
            return self._move(task["id"], "approved", f"retry {attempts + 1}/{task['max_attempts']}: {reason}", actor=actor)
        task = self._move(task["id"], "failed", reason, actor=actor, blocked_reason=reason)
        self._close_parent_if_settled(task)
        return task

    def verify(self, task_id: str, verdict: str, note: str, run_id: str | None = None) -> dict:
        if verdict not in ("pass", "fail"):
            raise ValueError("verdict must be pass or fail")
        with write_txn(self.conn):
            task = self.get_task(task_id)
            if task["status"] != "verifying":
                raise TransitionError(f"task {task_id} is '{task['status']}', not verifying")
            actor = self.actor
            if run_id:
                vrun = self.get_run(run_id)
                actor = f"emp:{vrun['employee_id']}" if vrun["employee_id"] else actor
                self._close_run(run_id, "succeeded")
            exec_run = self.conn.execute(
                "SELECT * FROM runs WHERE task_id=? AND kind='execute' ORDER BY started_at DESC LIMIT 1",
                (task_id,)).fetchone()
            if actor != "chair" and exec_run["employee_id"] and actor == f"emp:{exec_run['employee_id']}":
                raise TransitionError("an employee may not verify their own work")
            self.conn.execute("UPDATE results SET verified=?, verifier_note=?, verified_by=? WHERE run_id=?",
                              (1 if verdict == "pass" else 0, note, actor, exec_run["id"]))
            if verdict == "pass":
                task = self._move(task_id, "done", f"verified: {note}", actor=actor)
                self._close_parent_if_settled(task)
            else:
                task = self._fail_or_retry(task, dict(exec_run), f"verification failed: {note}")
        return task

    def executor_streak(self, employee_id: str, task_class: str | None) -> int:
        """Consecutive passed verifications of this executor's results in a task class, newest first."""
        streak = 0
        for r in self.conn.execute(
                "SELECT r.verified FROM results r JOIN runs u ON u.id=r.run_id JOIN tasks t ON t.id=u.task_id "
                "WHERE u.employee_id=? AND coalesce(t.task_class,'*')=? AND r.verified IS NOT NULL "
                "ORDER BY r.created_at DESC", (employee_id, task_class or "*")):
            if r[0] != 1:
                break
            streak += 1
        return streak

    def skip_verification(self, task_id: str, note: str) -> dict:
        """Close trusted work without a verifier (sampled out). The result stays unverified (verified IS NULL),
        so it never counts toward anyone's earned authority or trust."""
        with write_txn(self.conn):
            task = self.get_task(task_id)
            if task["status"] != "verifying":
                raise TransitionError(f"task {task_id} is '{task['status']}', not verifying")
            self.conn.execute(
                "UPDATE results SET verifier_note=?, verified_by='sampling' WHERE run_id=(SELECT id FROM runs "
                "WHERE task_id=? AND kind='execute' ORDER BY started_at DESC LIMIT 1)", (note, task_id))
            task = self._move(task_id, "done", note, actor="kernel")
            self._close_parent_if_settled(task)
        return task

    # ── Chair controls ────────────────────────────────────────────────────
    def cancel(self, task_id: str, note: str) -> dict:
        with write_txn(self.conn):
            task = self._move(task_id, "cancelled", note)
            self.conn.execute("UPDATE plans SET status='superseded' WHERE task_id=? AND status='awaiting_approval'",
                              (task_id,))
            for r in self.conn.execute("SELECT id FROM runs WHERE task_id=? AND status='running'", (task_id,)).fetchall():
                self._close_run(r["id"], "abandoned")
            self._close_parent_if_settled(task)
        return task

    def retry(self, task_id: str, note: str = "chair retry") -> dict:
        with write_txn(self.conn):
            if self.latest_plan(task_id, "approved") is None:
                raise TransitionError("no approved plan to retry; use replan")
            self.conn.execute("UPDATE tasks SET max_attempts=max_attempts+1 WHERE id=?", (task_id,))
            return self._move(task_id, "approved", note, expect={"failed"})

    def replan(self, task_id: str, note: str = "chair replan") -> dict:
        with write_txn(self.conn):
            self.conn.execute("UPDATE plans SET status='superseded' WHERE task_id=? AND status='approved'", (task_id,))
            return self._move(task_id, "inbox", note, expect={"failed", "approved"})

    def sweep_leases(self) -> list[dict]:
        """Self-healing: recover work held by employees whose runs died."""
        swept = []
        with write_txn(self.conn):
            for run in self.conn.execute("SELECT * FROM runs WHERE status='running' AND lease_until < ?",
                                         (now(),)).fetchall():
                run = dict(run)
                self._close_run(run["id"], "timeout")
                task = self.get_task(run["task_id"])
                if run["kind"] == "plan" and task["status"] == "planning":
                    self._move(task["id"], "inbox", f"planner lease expired ({run['id']})")
                elif run["kind"] == "execute" and task["status"] == "executing":
                    self._fail_or_retry(task, run, f"executor lease expired ({run['id']})")
                swept.append({"run": run["id"], "task": task["id"], "kind": run["kind"]})
        return swept

    def _close_parent_if_settled(self, task: dict) -> None:
        parent_id = task.get("parent_id")
        if not parent_id:
            return
        parent = self.get_task(parent_id)
        if parent["status"] != "waiting_on_subtasks":
            return
        statuses = [r[0] for r in self.conn.execute("SELECT status FROM tasks WHERE parent_id=?", (parent_id,))]
        if any(s not in TERMINAL | {"failed"} for s in statuses):
            return
        if "failed" in statuses:
            self._move(parent_id, "needs_input", "a sub-task failed", blocked_reason="sub-task failed")
        elif "done" in statuses:
            self._close_parent_if_settled(self._move(parent_id, "done", "all sub-tasks closed"))
        else:
            self._move(parent_id, "cancelled", "all sub-tasks cancelled")

    # ── messages ──────────────────────────────────────────────────────────
    def _message(self, sender: str, body: str, to_ref: str, kind: str = "dm", task_id: str | None = None,
                 thread_id: str | None = None, ack_required: int = 0) -> str:
        mid = new_id("message")
        self.conn.execute(
            "INSERT INTO messages (id, ts, sender, to_ref, kind, task_id, thread_id, body, ack_required)"
            " VALUES (?,?,?,?,?,?,?,?,?)", (mid, now(), sender, to_ref, kind, task_id, thread_id or mid, body, ack_required))
        return mid

    def post_message(self, sender: str, to_ref: str, body: str, kind: str = "dm", task_id: str | None = None,
                     thread_id: str | None = None) -> str:
        with write_txn(self.conn):
            return self._message(sender, body, to_ref, kind, task_id, thread_id)

    def messages(self, task_id: str | None = None, to_refs: list[str] | None = None, limit: int = 50) -> list[dict]:
        if task_id:
            rows = self.conn.execute("SELECT * FROM messages WHERE task_id=? ORDER BY ts LIMIT ?", (task_id, limit))
        elif to_refs:
            marks = ",".join("?" * len(to_refs))
            rows = self.conn.execute(f"SELECT * FROM messages WHERE to_ref IN ({marks}) OR sender IN ({marks}) "
                                     "ORDER BY ts DESC LIMIT ?", [*to_refs, *to_refs, limit])
        else:
            rows = self.conn.execute("SELECT * FROM messages ORDER BY ts DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
