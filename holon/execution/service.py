"""The kernel service: wires the organization, the work graph, memory, skills, briefs and drivers together.

Two ways for any harness to take part, neither requiring an integration:
  push  `holon dispatch`/`holon tick` claim a stage for an employee, write its brief, invoke its driver.
  pull  an employee session running anywhere calls `holon claim --as <emp>` (with its employee key),
        does the work, writes result.json, and calls `holon submit <run_id>` (with its run token).

The kernel's Python API trusts its caller's `actor`; the CLI authenticates who that is (security.py).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from holon.capabilities.context import DEFAULT_BUDGET_CHARS, compile_context, identity_card
from holon.capabilities.memory import Memory
from holon.capabilities import skills as skills_mod
from holon.capabilities.skills import Library
from holon.core import contract, vocab
from holon.core.contract import EnvelopeError
from holon.core.db import connect, migrate, now, write_txn
from holon.core.paths import Home
from holon.core.vocab import CHAIR, EXECUTIVE
from holon.execution import autonomy, briefs, drivers, genome, grading, security
from holon.execution.briefs import kernel_env
from holon.execution.org import Org, approver_ref
from holon.execution.schedules import Schedules
from holon.execution import staleness
from holon.execution.work import Claimant, Rules, TransitionError, Work

DEFAULT_CONFIG = {
    "context": {"budget_chars": DEFAULT_BUDGET_CHARS},
    "planner": {"enforce_read_only": True, "trust_harness_read_only": False, "guard_ignore": []},
    "grading": {},
    "autonomy": {},
    "pipeline": {},
    "memory": {"promotion_threshold": 2},
    "healing": {"incident_dept": "it", "lease_expiries_for_incident": 2, "schedule_failures_for_incident": 3,
                "run_failures_for_incident": 3, "doctor_every_min": 30},
    "cadence": {"dept_retro_days": 7, "dept_retro_min_proposals": 1, "probation_days": 14},
    "budget": {"max_running_per_dept": 0, "max_runs_per_dept_per_day": 0},   # 0 = no cap
    "notify": {"command": ""},   # e.g. a chat CLI with {message}; empty = no notifications
    # what employees may reach beyond their work's workdir; [scope.dept.<slug>] adds to it per department
    "scope": {"roots": [], "commands": [], "act_commands": []},
    # machine checks the kernel runs in the workdir after every execute, before any verifier;
    # [checks.dept.<slug>] and [checks.class.<task_class>] add their own
    "checks": {"commands": [], "timeout_s": 600},
}
CONFIG_KEYS = {s: set(v) for s, v in DEFAULT_CONFIG.items() if s not in ("grading", "autonomy", "pipeline")}
CONFIG_KEYS["budget"].add("dept")  # [budget.dept.<slug>] overrides
CONFIG_KEYS["scope"].add("dept")   # [scope.dept.<slug>] additions
CONFIG_KEYS["checks"] |= {"dept", "class"}   # [checks.dept.<slug>], [checks.class.<task_class>]


def load_config(home: Home) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if home.config_file.exists():
        for section, values in tomllib.loads(home.config_file.read_text()).items():
            if isinstance(values, dict):
                cfg.setdefault(section, {}).update(values)
    return cfg


@dataclass
class Claimed:
    run: dict
    node: dict
    brief_dir: Path
    token: str
    employee: dict | None = None

    @property
    def task(self) -> dict:  # the claimed stage node
        return self.node


class KernelRules(Rules):
    """The department genome deciding which optional stages a piece of work goes through, and the
    feedback loops that fire when work goes wrong."""

    def __init__(self, k: "Kernel"):
        self.k = k

    def _pipeline(self, work: dict) -> dict:
        p = {**genome.PIPELINE_DEFAULTS, **self.k.config.get("pipeline", {})}
        if work.get("dept_id"):
            g = self.k.org.dept(work["dept_id"]).get("genome")
            if g:
                p.update(json.loads(g).get("pipeline") or {})
        return p

    def _has(self, dept_id: str | None, stage: str) -> bool:
        return any(stage in e["stages"] and e["status"] in ("active", "probation")
                   for e in (self.k.org.employees(dept_id) if dept_id else []))

    def _wants(self, work: dict, stage: str, auto: bool) -> bool:
        mode = self._pipeline(work)[stage]
        return mode == "always" or (mode == "auto" and auto and self._has(work.get("dept_id"), stage))

    def _done(self, work: dict, stage: str) -> bool:
        return bool(self.k.work.stages(work["id"], stage, "done"))

    def _needs_research(self, work: dict) -> bool:
        novel = self.k.work.verified_in_class(work.get("task_class")) < int(
            {**grading.DEFAULTS, **self.k.config.get("grading", {})}["novelty_min_verified"])
        return self._wants(work, "research", novel or work["risk"] in ("high", "irreversible"))

    def _needs_shape(self, work: dict) -> bool:
        return self._wants(work, "shape", work["source"] in (CHAIR, "agent") and not work.get("parent_id"))

    def first_stage(self, work: dict) -> str:
        if self._needs_research(work):
            return "research"
        return "shape" if self._needs_shape(work) else "plan"

    def after_research(self, work: dict) -> str:
        return "shape" if self._needs_shape(work) and not self._done(work, "shape") else "plan"

    def after_shape(self, work: dict, research_requested: bool) -> str:
        if research_requested and not self._done(work, "research") and self._has(work.get("dept_id"), "research"):
            return "research"
        return "plan"

    def _retro(self, work: dict, why: str) -> None:
        if self._wants(work, "retro", True):
            self.k.work.add_retro(work["id"], why)

    def on_rework(self, work: dict, stage: dict, why: str) -> None:
        if stage.get("stage") == "approve":
            if len([s for s in self.k.work.stages(work["id"], "approve") if s["outcome"] == "reject"]) < 2:
                return
        self._retro(work, why)

    def on_finished(self, work: dict) -> None:
        if work["status"] == "failed":
            self._retro(work, f"work failed: {work.get('blocked_reason') or ''}")


class Kernel:
    def __init__(self, home: Home, actor: str = CHAIR):
        self.home = home
        self.actor = actor
        home.ensure()
        self.wconn = connect(home.work_db)
        self.mconn = connect(home.memory_db)
        migrate(self.wconn, "work")
        migrate(self.mconn, "memory")
        self.config = load_config(home)
        autonomy.policy(self.config)  # fail fast on a bad [autonomy] section
        self.work = Work(self.wconn, actor, KernelRules(self))
        self.memory = Memory(self.mconn)
        self.org = Org(self.wconn, actor, self.config)
        self.work.appliers["authority"] = lambda a: self.org.write_authority(
            a["approver"], a["task_class"], a["max_grade"], a["reason"])
        self.work.appliers["employee_status"] = self._apply_status
        self.skills = Library(home)
        self.schedules = Schedules(self.wconn, actor)
        self.org.bootstrap()
        self.auth = security.Auth(self.wconn, home)

    def close(self) -> None:
        self.wconn.close()
        self.mconn.close()

    # ── helpers ───────────────────────────────────────────────────────────
    def _dept_scope(self, dept_id: str | None) -> str | None:
        return f"dept:{self.org.dept(dept_id)['slug']}" if dept_id else None

    def _dept_slug(self, dept_id: str | None) -> str | None:
        return self.org.dept(dept_id)["slug"] if dept_id else None

    def claimant(self, employee_id: str | None) -> tuple[Claimant, dict | None]:
        if not employee_id:
            return Claimant(), None
        e = self.org.employee(employee_id)
        if e["status"] not in ("active", "probation"):
            raise TransitionError(f"employee {employee_id} is {e['status']}")
        ceo = self.org.ceo()
        return Claimant(id=e["id"], dept_id=e["dept_id"], is_head=e["is_head"], stages=tuple(e["stages"]),
                        is_ceo=bool(ceo and ceo["id"] == e["id"]), in_executive=e["dept"] == EXECUTIVE), e

    def identity(self, employee_id: str) -> str:
        e = self.org.employee(employee_id)
        mgr = self.org.employee(e["reports_to"]) if e.get("reports_to") else None
        auth = [r for r in self.org.authority_table() if r["approver"] == approver_ref(e["id"])]
        return identity_card(e, mgr, auth, self.org.review(e["id"]), self.org.predecessors(e["id"]))

    # ── decisions (the Chair's gate, and any delegated approver's) ────────
    def decide(self, ref: str, decision: str, note: str | None = None, recurring: bool = False,
               review_run: str | None = None) -> dict:
        out = self.work.decide(ref, decision, note, recurring, review_run)
        work, approval = out["work"], out["approval"]
        author = (self.work.output(out["plan"]["id"]) or {"body": {}})["body"].get("author")
        self.memory.record_review(work, author, decision + "d", note, self._dept_scope(work["dept_id"]))
        if decision == "approve" and out["decider"] != CHAIR and out["decider"] == approval["approver"] \
                and self.org.should_sample(approval["id"], work["dept_id"]):
            with write_txn(self.wconn):
                self.wconn.execute("INSERT OR IGNORE INTO audit_samples (task_id, approver, sampled_at) VALUES (?,?,?)",
                                   (approval["id"], out["decider"], now()))
            out["audit_sampled"] = True
        return out

    def approve(self, ref: str, note: str | None = None, recurring: bool = False, review_run: str | None = None):
        return self.decide(ref, "approve", note, recurring, review_run)

    def reject(self, ref: str, note: str, review_run: str | None = None) -> dict:
        return self.decide(ref, "reject", note, review_run=review_run)

    def regrade(self, ref: str, grade: int, note: str) -> dict:
        if self.actor != CHAIR:
            raise TransitionError("only the Chair regrades plans")
        approval = self.work.resolve_approval(ref)
        work = self.work.get(approval["parent_id"])
        author = self.work.plan_for_approval(approval["id"])["output"]["body"].get("author")
        approver = self.org.resolve_approver(work, grade, author)
        return self.work.set_grade(approval["id"], grade, approver, note)

    def answer(self, ref: str, text: str) -> dict:
        work = self.work.work_of(ref)
        question = work.get("blocked_reason") or "planner question"
        work = self.work.answer(ref, text)
        scope = work.get("project_ref") or self._dept_scope(work["dept_id"]) or "org"
        self.memory.add_item(scope, "decision", f"Q: {question}\nA: {text}", self.actor, confidence="high")
        return work

    def verify(self, ref: str, verdict: str, note: str, run_id: str | None = None) -> dict:
        out = self.work.verify(ref, verdict, note, run_id)
        if out["work"]["status"] == "done":
            out["skill_changes"] = self.apply_skill_changes(out["work"])
        plan = self.work.approved_plan(out["work"]["id"])
        change = None
        decider = plan["approval"]["decided_by"] if plan else None
        if decider and decider.startswith("emp:"):
            change = self._earned_authority(decider, out["work"].get("task_class"))
        return {**out, "authority_change": change}

    # ── authority: narrowing applies at once, widening needs the approver the autonomy policy names ──
    def _approver_dept(self, approver: str) -> str | None:
        return self.org.employee(approver[4:])["dept"] if approver.startswith("emp:") else None

    def _earned_authority(self, approver: str, task_class: str | None) -> dict | None:
        pol = self.org.policy(self._approver_dept(approver))
        change = self.org.earned_authority_change(approver, task_class, pol["promote_after"], pol["auto_promote_cap"])
        if change:
            change.update(self.set_authority(change["approver"], change["task_class"], change["to"], change["reason"],
                                             requested_by="kernel"))
        return change

    def set_authority(self, approver: str, task_class: str, max_grade: int, reason: str,
                      requested_by: str | None = None) -> dict:
        """The one way authority changes. The Chair sets it directly; for anyone else (including the kernel's
        earned-authority loop) a widening becomes a proposal for the approver the autonomy policy names,
        unless that policy is 'auto'. Nobody approves their own widening."""
        requested_by = requested_by or self.actor
        current = self.org.authority_of(approver, None if task_class == "*" else task_class)
        widen_approver = self.org.policy(self._approver_dept(approver))["widen_approver"]
        if requested_by == CHAIR or max_grade <= current or widen_approver == "auto":
            self.org.set_authority(approver, task_class, max_grade, reason)
            return {"applied": True}
        decider = CHAIR
        ceo = self.org.ceo()
        if widen_approver == "ceo" and ceo and approver_ref(ceo["id"]) not in (approver, requested_by):
            decider = approver_ref(ceo["id"])
        approval = self.work.propose(
            title=f"Widen authority: {approver} on {task_class} G{current} -> G{max_grade}",
            intent=f"{reason}. Requested by {requested_by}.",
            steps=f"On approval the kernel sets {approver}'s approval authority for task class '{task_class}' "
                  f"from G{current} to G{max_grade}. Review: {approver}'s record (holon hr review), audit samples.",
            apply={"kind": "authority", "approver": approver, "task_class": task_class, "max_grade": max_grade,
                   "reason": f"{reason} (approved proposal)"},
            approver=decider, grade=max_grade, task_class="authority-change", dept=EXECUTIVE, author=requested_by)
        return {"applied": False, "proposal": approval["id"], "approver": decider}

    def _apply_status(self, a: dict) -> None:
        e = self.org.employee(a["employee_id"])
        self.wconn.execute("UPDATE employees SET status=? WHERE id=?", (a["status"], e["id"]))
        self.org._event("employee", e["id"], e["status"], a["status"], a.get("reason", "approved proposal"))

    def judge_sample(self, ref: str, verdict: str, note: str) -> dict:
        """The Chair re-judges a sampled employee approval; a disagreement costs the approver authority."""
        if verdict not in ("agree", "disagree"):
            raise ValueError("verdict must be agree or disagree")
        approval = self.work.get(ref)
        if approval["stage"] != "approve":
            approval = self.work.resolve_approval(ref)
        row = self.wconn.execute("SELECT * FROM audit_samples WHERE task_id=?", (approval["id"],)).fetchone()
        if row is None:
            raise LookupError(f"{ref} is not an audit sample")
        with write_txn(self.wconn):
            self.wconn.execute("UPDATE audit_samples SET verdict=?, note=?, judged_at=? WHERE task_id=?",
                               (verdict, note, now(), approval["id"]))
        change = self._earned_authority(row["approver"], approval.get("task_class")) if verdict == "disagree" else None
        return {"approval": approval["id"], "verdict": verdict, "authority_change": change}

    def audit_samples(self, pending_only: bool = True) -> list[dict]:
        q = ("SELECT a.*, t.grade, w.title FROM audit_samples a JOIN tasks t ON t.id=a.task_id "
             "JOIN tasks w ON w.id=t.parent_id" + (" WHERE a.verdict IS NULL" if pending_only else "")
             + " ORDER BY a.sampled_at")
        return [dict(r) for r in self.wconn.execute(q)]

    # ── communication ─────────────────────────────────────────────────────
    def request(self, sender: str, dept: str, title: str, intent: str, **kw) -> dict:
        """A cross-department ask: becomes work in that department (its head shapes it) and a message to it."""
        work = self.work.add_work(title, intent, dept=dept, source="agent" if sender != CHAIR else CHAIR,
                                  requested_by=sender, **kw)
        self.work.post_message(sender, f"dept:{self.org.dept(dept)['slug']}", f"Request: {title}\n\n{intent}",
                               kind="request", task_id=work["id"])
        return work

    def inbox(self, ref: str) -> list[dict]:
        refs = [ref]
        if ref.startswith("emp:"):
            e = self.org.employee(ref[4:])
            refs += [f"dept:{e['dept']}", "org"]
        return self.work.messages(to_refs=refs)

    # ── claiming ──────────────────────────────────────────────────────────
    def claim(self, stage: str | list[str] | None = None, employee_id: str | None = None,
              driver: str | None = None, task_id: str | None = None,
              max_running: int | None = None) -> Claimed | None:
        who, emp = self.claimant(employee_id)
        stages = [stage] if isinstance(stage, str) else (stage or list(who.stages))
        if emp:
            missing = [s for s in stages if s not in emp["stages"]]
            if isinstance(stage, str) and missing:
                raise TransitionError(f"{emp['name']} ({employee_id}) has no role that performs the '{stage}' stage")
        token, token_hash = security.new_token()
        got = self.work.claim(who, stages, driver or (emp["driver"] if emp else None), task_id, token_hash,
                              exclude_depts=self.over_budget(), max_running=max_running,
                              exclude_tasks=self.backed_off())
        if got is None:
            return None
        node, run = got
        md, _ = compile_context(self.home, self.work, self.memory, node["id"],
                                budget=int(self.config["context"]["budget_chars"]), run_id=run["id"],
                                employee=emp, predecessors=self.org.predecessors(emp["id"]) if emp else None)
        slug = self._dept_slug(node["dept_id"])
        bdir = briefs.build(self.home, run, node, md, self.skills, slug, emp,
                            self.identity(emp["id"]) if emp else None, token, scope=self.scope(slug))
        self.work.set_run_paths(run["id"], str(bdir), str(bdir / "driver.log"))
        return Claimed(run=self.work.get_run(run["id"]), node=node, brief_dir=bdir, token=token, employee=emp)

    # ── ingestion: the one place agent output enters the organization ─────
    def submit(self, run_id: str, envelope: dict | None = None, exit_code: int | None = 0,
               driver_output: dict | None = None) -> dict:
        run = self.work.get_run(run_id)
        if run["status"] != "running":
            raise TransitionError(f"run {run_id} is '{run['status']}' (late result after lease expiry is discarded)")
        if envelope is None:
            path = self.home.run_dir(run_id) / "result.json"
            if path.exists():
                try:
                    envelope = json.loads(path.read_text())
                except json.JSONDecodeError as e:
                    envelope = {"_invalid_json": str(e)}
        stage = run["kind"]
        try:
            if envelope is None:
                raise EnvelopeError(["no result.json and no JSON output"])
            env = contract.validate(stage, envelope)
            self._check_org_refs(env)
            if stage == "execute":
                self._check_skill_changes(self.work.work_of(run["task_id"]), env)
        except EnvelopeError as e:
            return self._contract_breach(run, e.problems, driver_output, exit_code)
        node = self.work.get(run["task_id"])
        work = self.work.get(node["parent_id"])
        emp = self.org.employee(run["employee_id"]) if run["employee_id"] else None
        if stage == "research":
            out = self.work.submit_research(run_id, env, exit_code)
        elif stage == "shape":
            out = self.work.submit_shape(run_id, env, exit_code)
        elif stage == "plan":
            if env["kind"] == "needs_input":
                out = self.work.submit_plan(run_id, env, exit_code=exit_code)
            else:
                g = grading.grade_plan(self.wconn, work, env, self.config,
                                       reach=self.scope(self._dept_slug(work["dept_id"]))["roots"])
                approver = self.org.resolve_approver(work, g["grade"], run["employee_id"])
                out = self.work.submit_plan(run_id, env, g, approver, exit_code)
        elif stage == "approve":
            out = self.decide(node["id"], env["decision"], env.get("note"), review_run=run_id)
        elif stage == "execute":
            out = self.work.submit_execute(run_id, env, exit_code)
            if env["outcome"] != "failure":
                checks = self.run_checks(self.work.get(work["id"]))
                out["checks"] = checks
                if checks and not all(c["ok"] for c in checks):
                    failed = [c for c in checks if not c["ok"]]
                    note = "Machine checks failed (run by the kernel; no verifier was spent):\n" + "\n".join(
                        f"- `{c['command']}` exited {c['exit_code']}:\n```\n{c['tail']}\n```" for c in failed)
                    out["work"] = self.verify(work["id"], "fail", note)["work"]
            if not out.get("checks") or all(c["ok"] for c in out["checks"]):
                skipped = self._sample_verification(self.work.get(work["id"]), run, env)
                out["verification_skipped"] = bool(skipped)
                if skipped:
                    out["work"] = skipped
                    if skipped["status"] == "done":
                        out["skill_changes"] = self.apply_skill_changes(skipped)
        elif stage == "verify":
            out = self.verify(node["id"], env["verdict"], env["note"], run_id=run_id)
        else:
            out = self.work.submit_retro(run_id, env, exit_code)
            if work.get("task_class") == "retro" and work.get("dept_id"):
                self._ack_proposals(work["dept_id"])
        out["memory"] = self._consolidate(work, run, env, emp, stage)
        out["proposals_routed"] = self._route_proposals(work, env, emp)
        return {**out, "stage": stage}

    def _consolidate(self, work: dict, run: dict, env: dict, emp: dict | None, stage: str) -> dict:
        if not any(env.get(k) for k in ("learnings", "decisions", "blockers", "terrain", "open_questions")) \
                and stage != "execute":
            return {"items": [], "promotions": []}
        dept_scope = self._dept_scope(work["dept_id"]) or (f"dept:{emp['dept']}" if emp else None)
        mem = self.memory.consolidate(work, run["id"], env, run["employee_id"], dept_scope, stage=stage)
        mem["promotions"] = self._promote(mem.pop("lessons"), emp)
        return mem

    def _route_proposals(self, work: dict, env: dict, emp: dict | None) -> int:
        """Proposals from any stage go to the department's steward as messages; the steward's retro consumes them."""
        props = env.get("proposals") or []
        dept = self._dept_slug(work["dept_id"]) or (emp["dept"] if emp else EXECUTIVE)
        for p in props:
            body = p if isinstance(p, str) else f"{p['title']}: {p['intent']}"
            self.work.post_message(f"emp:{emp['id']}" if emp else self.actor, f"dept:{dept}", body,
                                   kind="proposal", task_id=work["id"])
        return len(props)

    def _ack_proposals(self, dept_id: str) -> None:
        props = self.work.messages(to_refs=[f"dept:{self._dept_slug(dept_id)}"], kind="proposal", unacked=True,
                                   limit=500)
        self.work.ack([m["id"] for m in props])

    def _sample_verification(self, work: dict, run: dict, env: dict) -> dict | None:
        """Trusted G0 work is verified on a sample (autonomy.verify_sample_rate); the rest closes unverified.
        Trusted = the executor's last `trusted_after` verified results in this task class all passed."""
        if work["status"] != "open" or env["outcome"] != "success" or not run["employee_id"]:
            return None
        plan = self.work.approved_plan(work["id"])
        if not plan or plan["grade"] != 0:
            return None
        pol = self.org.policy(work["dept_id"])
        streak = self.work.executor_streak(run["employee_id"], work.get("task_class"))
        if streak < pol["trusted_after"] or autonomy.sampled(f"verify:{run['id']}", pol["verify_sample_rate"]):
            return None
        return self.work.skip_verification(
            work["id"], f"verification sampled out: trusted G0 work ({streak} consecutive verified passes; "
                        f"{pol['verify_sample_rate']:.0%} of such results are verified)")

    def _check_skill_changes(self, work: dict, env: dict) -> None:
        """An executor may change only the department skill files its approved plan named."""
        changes = env.get("skill_changes") or []
        if not changes:
            return
        plan = self.work.approved_plan(work["id"])
        named = set(((plan or {}).get("output") or {"body": {}})["body"].get("writes") or [])
        problems = []
        if not work.get("dept_id"):
            problems.append("skill_changes need work that belongs to a department")
        for i, c in enumerate(changes):
            f = Path(c["file"])
            if not skills_mod.NAME_RE.match(c["skill"]) or f.is_absolute() or ".." in f.parts or f.suffix != ".md":
                problems.append(f"skill_changes[{i}]: {c['skill']}/{c['file']} is not a skill markdown file")
            elif f"skill:{c['skill']}/{c['file']}" not in named:
                problems.append(f"skill_changes[{i}]: the approved plan does not list skill:{c['skill']}/{c['file']}")
        if problems:
            raise EnvelopeError(problems)

    def apply_skill_changes(self, work: dict) -> list[dict]:
        """Write a verified work's skill changes into its department's layer, keep them only if the library
        still checks clean, then refresh every registered harness export."""
        ex = self.work.latest(work["id"], "execute", "done")
        body = ((self.work.output(ex["id"]) if ex else None) or {"body": {}})["body"]
        changes = body.get("skill_changes") or []
        slug = self._dept_slug(work.get("dept_id"))
        if not changes or not slug:
            return []
        layer = self.skills.instance / f"@{slug}"
        before = {}
        for c in changes:
            p = layer / c["skill"] / c["file"]
            before[p] = p.read_text() if p.exists() else None
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(c["content"])
        problems = [x for x in self.skills.check(slug) if any(x.startswith(c["skill"] + " ") for c in changes)]
        if problems:  # leave the library as it was
            for p, old in before.items():
                if old is None:
                    p.unlink()
                else:
                    p.write_text(old)
        applied = not problems
        with write_txn(self.wconn):
            for c in changes:
                self.work._event("skill", f"@{slug}/{c['skill']}/{c['file']}", None,
                                 "changed" if applied else "rejected",
                                 (c["why"] if applied else "; ".join(problems))[:500], actor="kernel")
        exported = self.refresh_skill_exports() if applied else []
        return [{"skill": c["skill"], "file": c["file"], "applied": applied, "problems": problems,
                 "exported_to": exported} for c in changes]

    # ── skill exports: harnesses that load skills from disk get a copy; the kernel keeps them current ──
    def _exports_file(self) -> Path:
        return self.home.root / "config" / "skill-exports.json"

    def skill_exports(self) -> list[dict]:
        f = self._exports_file()
        return json.loads(f.read_text()) if f.exists() else []

    def export_skills(self, dest: Path, variables: dict, names: list[str] | None = None,
                      dept: str | None = None) -> list[Path]:
        """Export skills to a harness directory and remember it, so later skill changes reach it too."""
        out = self.skills.export(dest, variables, names, dept)
        entry = {"dir": str(dest.resolve()), "names": names, "dept": dept, "variables": variables}
        regs = [r for r in self.skill_exports() if r["dir"] != entry["dir"]] + [entry]
        self._exports_file().parent.mkdir(parents=True, exist_ok=True)
        self._exports_file().write_text(json.dumps(regs, indent=2))
        return out

    def refresh_skill_exports(self) -> list[str]:
        done = []
        for r in self.skill_exports():
            if Path(r["dir"]).is_dir():
                self.skills.export(Path(r["dir"]), r["variables"], r["names"], r["dept"])
                done.append(r["dir"])
        return done

    def stale_skill_exports(self) -> list[str]:
        """Registered exports whose files no longer match what the library renders."""
        stale = []
        for r in self.skill_exports():
            d = Path(r["dir"])
            for name in r["names"] or self.skills.names(r["dept"]):
                m = self.skills.meta(name, r["dept"])
                for f in ["SKILL.md"] + m["files"]:
                    p = d / name / f
                    if not p.exists() or p.read_text() != self.skills.render(self.skills.read(name, f, r["dept"]),
                                                                            r["variables"]):
                        stale.append(f"{d}: {name}/{f}")
        return stale

    def _check_org_refs(self, env: dict) -> None:
        problems = []

        def emp_ok(eid: str, where: str) -> None:
            try:
                if self.org.employee(eid)["status"] == "retired":
                    problems.append(f"{where}: employee {eid} is retired")
            except LookupError:
                problems.append(f"{where}: no employee {eid}")

        def dept_ok(slug: str, where: str) -> None:
            try:
                self.org.dept(slug)
            except LookupError:
                problems.append(f"{where}: no department {slug}")

        if env.get("assignee"):
            emp_ok(env["assignee"], "assignee")
        if env.get("dept"):
            dept_ok(env["dept"], "dept")
        for key in ("children", "actions"):
            for i, c in enumerate(env.get(key) or []):
                if c.get("dept"):
                    dept_ok(c["dept"], f"{key}[{i}]")
                if c.get("assignee"):
                    emp_ok(c["assignee"], f"{key}[{i}].assignee")
        if problems:
            raise EnvelopeError(problems)

    def _promote(self, lessons: list[dict], emp: dict | None) -> list[dict]:
        """Collective intelligence: employee lessons → department → organization."""
        if not emp or not lessons:
            return []
        threshold = int(self.config["memory"]["promotion_threshold"])
        members = [f"emp:{e['id']}" for e in self.org.employees(emp["dept"], include_retired=True)]
        dept_scopes = [f"dept:{d['slug']}" for d in self.org.depts()]
        promoted = []
        for item in lessons:
            up = self.memory.promote_recurring(item, members, f"dept:{emp['dept']}", threshold)
            if up:
                promoted.append({"to": f"dept:{emp['dept']}", "item": up["id"], "from": up["from_scopes"]})
                org_up = self.memory.promote_recurring(up, dept_scopes, "org", threshold)
                if org_up:
                    promoted.append({"to": "org", "item": org_up["id"], "from": org_up["from_scopes"]})
        return promoted

    def _contract_breach(self, run: dict, problems: list[str], driver_output: dict | None,
                         exit_code: int | None) -> dict:
        """An employee that did not honor the result contract. Observe-stage runs fail and the stage returns
        to ready; execution output is wrapped as 'partial' for an independent verifier to judge."""
        note = "result contract breach: " + "; ".join(problems)
        tail = (driver_output or {}).get("stdout_tail", "")
        if run["kind"] == "execute":
            env = {"outcome": "partial", "summary": f"Output did not satisfy the result contract ({problems[0]}).",
                   "documentation": f"**{note}**\n\nDriver stdout (tail):\n\n```\n{tail or '(none)'}\n```",
                   "blockers": [note]}
            out = self.work.submit_execute(run["id"], env, exit_code)
            return {"stage": "execute", "breach": problems, **out}
        node = self.work.release(run["id"], "failed", note)
        return {"stage": run["kind"], "breach": problems, "node": node, "work": self.work.work_of(node)}

    # ── skills ────────────────────────────────────────────────────────────
    def invoke_skill(self, name: str, file: str = "SKILL.md", employee_id: str | None = None,
                     dept: str | None = None) -> dict:
        """Resolve a skill (department → organization → standard library), render it for the employee, and
        record the use: which skills are used, by whom, is what the steward's evolution loop measures."""
        emp = self.org.employee(employee_id) if employee_id else None
        dept = dept or (emp["dept"] if emp else None)
        layer, path = self.skills.resolve(name, file, dept)
        text = self.skills.render(path.read_text(), {
            "holon": briefs.holon_command(self.home), "emp_id": emp["id"] if emp else None,
            "emp_name": emp["name"] if emp else None, "dept": dept,
            "roles": ", ".join(emp["roles"]) if emp else None})
        with write_txn(self.wconn):
            self.work._event("skill", f"{name}/{file}", None, layer, f"invoked ({dept or 'org'})",
                             actor=f"emp:{emp['id']}" if emp else self.actor)
        return {"skill": name, "file": file, "layer": layer, "path": str(path), "text": text}

    # ── push mode ─────────────────────────────────────────────────────────
    def run_driver(self, claimed: Claimed, driver_name: str) -> dict:
        drv = drivers.get(self.home, driver_name)
        brief = briefs.load(self.home, claimed.run["id"])
        workdir = claimed.node.get("workdir")
        trusted = drv.read_only_enforced and self.config["planner"].get("trust_harness_read_only")
        guard = (vocab.POSTURE[claimed.run["kind"]] == "observe" and workdir and Path(workdir).is_dir()
                 and self.config["planner"]["enforce_read_only"] and not trusted
                 and not claimed.brief_dir.resolve().is_relative_to(Path(workdir).resolve()))
        ignore = list(self.config["planner"].get("guard_ignore") or [])
        before = staleness.snapshot(Path(workdir), ignore) if guard else None

        def alive() -> None:  # the harness is still working: keep its lease (a dead dispatcher stops renewing)
            try:
                self.work.heartbeat(claimed.run["id"])
            except TransitionError:  # the agent already submitted, or the run was swept
                pass
        out = drivers.run(self.home, drv, brief, claimed.token, alive=alive)
        if out.get("usage"):
            self.work.set_run_usage(claimed.run["id"], **out["usage"])
        changed = staleness.changes(before, staleness.snapshot(Path(workdir), ignore)) if guard else []
        breach = (f"{claimed.run['kind']} run modified the working directory (must be read-only): "
                  + ", ".join(changed[:5]) + (f" and {len(changed) - 5} more" if len(changed) > 5 else ""))
        if self.work.get_run(claimed.run["id"])["status"] != "running":  # the agent submitted itself (pull-in-push)
            if changed:  # too late to discard the result: record it where audits and retros see it
                with write_txn(self.wconn):
                    self.work._event("task", claimed.node["id"], None, None, f"read-only breach: {breach}",
                                     actor=f"emp:{claimed.employee['id']}" if claimed.employee else None)
            return {"stage": claimed.run["kind"], "submitted_by_agent": True, "breach": [breach] if changed else None,
                    "work": self.work.work_of(claimed.node["id"])}
        if changed:
            return self._contract_breach(self.work.get_run(claimed.run["id"]), [breach], out, out["exit_code"])
        return self.submit(claimed.run["id"], out["envelope"], out["exit_code"], driver_output=out)

    def dispatch(self, employee_id: str, stage: str | None = None, task_id: str | None = None) -> dict | None:
        emp = self.org.employee(employee_id)
        # checked inside the claim's transaction: two wakes due in one tick must not both start
        claimed = self.claim(stage, employee_id, emp["driver"], task_id, max_running=emp["max_concurrency"])
        if claimed is None:
            return None
        return self.run_driver(claimed, emp["driver"])

    def tick(self, foreground: bool = False) -> dict:
        """Idempotent heartbeat; call it from any timer (systemd, cron, a harness scheduler)."""
        swept = self.work.sweep_leases()
        triggered = self.triggers(swept)
        notified = self.notify()
        fired = []
        for s in self.schedules.take_due():
            if s["kind"] == "recurring":
                work = self.work.instantiate_recurring(s["template_id"])
                fired.append({"schedule": s["id"], "recurring_work": work["id"]})
                continue
            if foreground:
                try:
                    res = self.dispatch(s["employee_id"], s["stage"])
                    self.schedules.record_outcome(s["id"], True)
                    fired.append({"schedule": s["id"], "dispatched": bool(res)})
                except Exception as e:  # one failing employee must not stop the organization's heartbeat
                    self.schedules.record_outcome(s["id"], False)
                    fired.append({"schedule": s["id"], "error": str(e)})
            else:
                self._spawn_dispatch(s["employee_id"], s["stage"], s["id"])
                fired.append({"schedule": s["id"], "spawned": True})
        return {"swept": swept, "triggered": triggered, "fired": fired, "notified": notified}

    def _spawn_dispatch(self, employee_id: str, stage: str | None, schedule_id: str) -> None:
        log = self.home.runtime_dir / "dispatch.log"
        cmd = [sys.executable, "-m", "holon", "--home", str(self.home.root), "dispatch", "--as", employee_id,
               "--schedule", schedule_id] + (["--stage", stage] if stage else [])
        with open(log, "a") as fh:
            subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                             env={**os.environ, **kernel_env(self.home)})

    # ── machine checks: acceptance a command can decide, run by the kernel, not by an agent ──
    def checks_for(self, work: dict) -> list[str]:
        c = self.config.get("checks", {})
        slug = self._dept_slug(work.get("dept_id"))
        return [*c.get("commands", []), *((c.get("dept") or {}).get(slug, {}).get("commands", []) if slug else []),
                *((c.get("class") or {}).get(work.get("task_class") or "", {}).get("commands", []))]

    def run_checks(self, work: dict) -> list[dict]:
        """Run the work's checks in its workdir. Every result is posted on the work (the verifier reads it
        in its context) and logged as an event; the caller turns a failure into rework."""
        cmds = self.checks_for(work)
        workdir = work.get("workdir")
        if not cmds or not workdir or not Path(workdir).is_dir():
            return []
        env = {k: v for k, v in os.environ.items() if k not in ("HOLON_CHAIR_KEY", "HOLON_KEY", "HOLON_RUN_TOKEN")}
        env.update({"HOLON_WORK_ID": work["id"], "HOLON_HOME": str(self.home.root)})
        results = []
        for cmd in cmds:
            try:
                p = subprocess.run(cmd, shell=True, cwd=workdir, env=env, capture_output=True, text=True,
                                   timeout=int(self.config["checks"]["timeout_s"]))
                code, tail = p.returncode, (p.stdout + p.stderr)[-1500:]
            except subprocess.TimeoutExpired:
                code, tail = 124, f"timed out after {self.config['checks']['timeout_s']}s"
            results.append({"command": cmd, "exit_code": code, "ok": code == 0, "tail": tail.strip()})
        summary = ", ".join(f"`{r['command']}` {'ok' if r['ok'] else 'FAILED (' + str(r['exit_code']) + ')'}"
                            for r in results)
        dept = self._dept_slug(work.get("dept_id"))
        with write_txn(self.wconn):
            self.work._event("task", work["id"], None, None, f"checks: {summary}", actor="kernel")
            self.work._message("kernel", "Machine checks after execution: " + summary + "".join(
                f"\n\n`{r['command']}` (exit {r['exit_code']}):\n```\n{r['tail'][-600:]}\n```" for r in results),
                f"dept:{dept}" if dept else "org", kind="channel", task_id=work["id"])
        return results

    # ── scope: what a department's employees may reach beyond their work's workdir ──
    def scope(self, dept_slug: str | None) -> dict:
        """The effective reach for a department: [scope] plus [scope.dept.<slug>]. `name` is the slug when
        the department has its own section, else "org"; harness adapters build one sandbox per name."""
        base = self.config.get("scope", {})
        own = (base.get("dept") or {}).get(dept_slug) if dept_slug else None
        merged = {k: [*base.get(k, []), *((own or {}).get(k, []))] for k in ("roots", "commands", "act_commands")}
        merged["roots"] = [str(Path(r).expanduser()) for r in merged["roots"]]
        return {"name": dept_slug if own is not None else "org", **merged}

    def scopes(self) -> list[dict]:
        """Every distinct scope: "org" first, then one per department with a [scope.dept.<slug>] section."""
        return [self.scope(None)] + [self.scope(s) for s in sorted((self.config.get("scope", {}).get("dept") or {}))]

    def scope_fingerprint(self) -> str:
        import hashlib
        return hashlib.sha256(json.dumps(self.scopes(), sort_keys=True).encode()).hexdigest()[:12]

    # ── budgets: how much work a department may have in flight, and start per day ──
    def budget(self, dept_slug: str | None) -> dict:
        b = {k: v for k, v in self.config.get("budget", {}).items() if k != "dept"}
        if dept_slug:
            b.update(self.config.get("budget", {}).get("dept", {}).get(dept_slug, {}))
        return {k: int(v) for k, v in b.items()}

    def budget_usage(self, dept_id: str) -> dict:
        since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        running, today = self.wconn.execute(
            "SELECT sum(r.status='running'), sum(r.started_at>?) FROM runs r JOIN tasks t ON t.id=r.task_id "
            "WHERE t.dept_id=?", (since, dept_id)).fetchone()
        return {"running": running or 0, "runs_last_24h": today or 0}

    def backed_off(self) -> list[str]:
        """Stages whose runs keep failing wait before the next try: 10 min after reaching the incident
        threshold, doubling with every further failure, at most a day. Retrying a broken harness at
        every wake fixes nothing; the incident does."""
        n0 = int(self.config["healing"]["run_failures_for_incident"])
        out = []
        for r in self.wconn.execute(
                "SELECT r.task_id, count(*) AS n, max(r.ended_at) AS last FROM runs r JOIN tasks t ON t.id=r.task_id "
                "WHERE r.status='failed' AND t.status='ready' GROUP BY r.task_id HAVING n>=?", (n0,)).fetchall():
            wait = min(timedelta(minutes=10) * 2 ** (r["n"] - n0), timedelta(days=1))
            if r["last"] and datetime.strptime(r["last"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc) \
                    + wait > datetime.now(timezone.utc):
                out.append(r["task_id"])
        return out

    def over_budget(self) -> list[str]:
        """Departments at a cap: their stages wait on the board until the department is under it again."""
        out = []
        for d in self.org.depts():
            cap = self.budget(d["slug"])
            if not (cap.get("max_running_per_dept") or cap.get("max_runs_per_dept_per_day")):
                continue
            use = self.budget_usage(d["id"])
            if (cap.get("max_running_per_dept") and use["running"] >= cap["max_running_per_dept"]) or \
                    (cap.get("max_runs_per_dept_per_day") and use["runs_last_24h"] >= cap["max_runs_per_dept_per_day"]):
                out.append(d["id"])
        return out

    # ── telling the Chair: one message per heartbeat about what newly needs them ──
    def pending_for_chair(self, since_seq: int) -> list[str]:
        lines = []
        for e in self.wconn.execute(
                "SELECT e.seq, t.id, t.stage, t.status, t.title, t.task_class, t.approver, t.blocked_reason "
                "FROM events e JOIN tasks t ON t.id=e.entity_id WHERE e.entity='task' AND e.seq>? ORDER BY e.seq",
                (since_seq,)).fetchall():
            if e["stage"] == "approve" and e["status"] == "ready" and e["approver"] == CHAIR:
                lines.append(f"approve? {e['title'].removeprefix('approve: ')} (holon plan show {e['id']})")
            elif e["stage"] is None and e["status"] == "needs_input":
                lines.append(f"question on {e['title']}: {(e['blocked_reason'] or '')[:200]} (holon task show {e['id']})")
            elif e["stage"] is None and e["status"] == "failed":
                lines.append(f"failed: {e['title']} (holon task show {e['id']})")
            elif e["stage"] is None and e["status"] == "open" and e["task_class"] == "incident":
                lines.append(f"incident: {e['title']}")
        n = self.wconn.execute("SELECT count(*) FROM audit_samples WHERE verdict IS NULL AND sampled_at>?",
                               (self._setting("notify_ts") or "",)).fetchone()[0]
        if n:
            lines.append(f"{n} delegated approvals sampled for your review (holon audit samples)")
        return list(dict.fromkeys(lines))

    def _setting(self, key: str) -> str | None:
        r = self.wconn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def notify(self) -> dict | None:
        """Run [notify] command with {message} once per heartbeat that found something new for the Chair."""
        command = (self.config.get("notify") or {}).get("command")
        if not command:
            return None
        last = int(self._setting("notify_seq") or 0)
        top = self.wconn.execute("SELECT coalesce(max(seq), 0) FROM events").fetchone()[0]
        lines = self.pending_for_chair(last)
        with write_txn(self.wconn):
            self.wconn.execute("INSERT OR REPLACE INTO settings VALUES ('notify_seq', ?)", (str(top),))
            self.wconn.execute("INSERT OR REPLACE INTO settings VALUES ('notify_ts', ?)", (now(),))
        if not lines:
            return None
        message = "Holon needs you:\n" + "\n".join(f"- {line}" for line in lines[:20])
        import shlex
        try:
            p = subprocess.run(command.replace("{message}", shlex.quote(message)), shell=True, capture_output=True,
                               text=True, timeout=60)
            ok, detail = p.returncode == 0, (p.stderr or p.stdout)[-300:]
        except (OSError, subprocess.TimeoutExpired) as e:
            ok, detail = False, str(e)
        with write_txn(self.wconn):
            self.work._event("notify", "chair", None, "sent" if ok else "failed",
                             f"{len(lines)} items" + ("" if ok else f": {detail}"), actor="kernel")
        return {"items": len(lines), "sent": ok}

    # ── feedback loops (R5): sensors that post work when something needs attention ──
    def triggers(self, swept: list[dict] | None = None) -> list[dict]:
        out = []
        out += self._incidents(swept or [])
        out += self._probation_reviews()
        out += self._department_retros()
        out += self._doctor()
        return out

    def _doctor(self) -> list[dict]:
        """Integrity on the heartbeat: every `doctor_every_min`, run `holon doctor`; each failing check opens
        an incident (one per check until it is resolved), which the Chair is told about."""
        every = int(self.config["healing"]["doctor_every_min"])
        last = self._setting("doctor_ts")
        if not every or (last and last > (datetime.now(timezone.utc) - timedelta(minutes=every))
                         .strftime("%Y-%m-%dT%H:%M:%S.%fZ")):
            return []
        from holon.integrity import doctor
        errors: dict[str, list[str]] = {}
        for f in doctor.run(self):
            if f["severity"] == "error":
                errors.setdefault(f["check"], []).append(f["message"])
        with write_txn(self.wconn):
            self.wconn.execute("INSERT OR REPLACE INTO settings VALUES ('doctor_ts', ?)", (now(),))
        out = []
        for check, msgs in errors.items():
            r = self._incident(f"Incident: doctor reports {check}",
                               "`holon doctor` reports:\n" + "\n".join(f"- {m}" for m in msgs[:10])
                               + "\nFind the cause and fix it; the incident is done when doctor is clean for this check.")
            if r:
                out.append(r)
        return out

    def _incident(self, title: str, intent: str) -> dict | None:
        dept = self.config["healing"]["incident_dept"]
        try:
            self.org.dept(dept)
        except LookupError:
            dept = EXECUTIVE
        if self.wconn.execute("SELECT 1 FROM tasks WHERE stage IS NULL AND title=? AND status IN "
                              "('blocked','open','waiting','needs_input')", (title,)).fetchone():
            return None
        w = self.work.add_work(title, intent, dept=dept, source="kernel", requested_by="kernel", priority=1,
                               task_class="incident", acceptance="the cause is fixed and the failing path works again")
        return {"incident": w["id"], "title": title}

    def _incidents(self, swept: list[dict]) -> list[dict]:
        h = self.config["healing"]
        out = []
        for s in swept:
            n = self.wconn.execute("SELECT count(*) FROM runs WHERE task_id=? AND status='timeout'",
                                   (s["task"],)).fetchone()[0]
            if n >= int(h["lease_expiries_for_incident"]):
                r = self._incident(f"Incident: runs keep dying on {s['task']}",
                                   f"{n} runs of the {s['stage']} stage {s['task']} expired their lease (last employee "
                                   f"{s['employee']}). Find why the harness run dies and fix it.")
                if r:
                    out.append(r)
        # a run that ends without a valid result returns its stage to the board; a stage that keeps failing
        # means a broken harness, model or brief, which retrying alone never fixes
        for r in self.wconn.execute(
                "SELECT r.task_id, t.stage, count(*) AS n, max(r.employee_id) AS emp FROM runs r JOIN tasks t "
                "ON t.id=r.task_id WHERE r.status='failed' AND t.status IN ('ready','claimed') GROUP BY r.task_id "
                "HAVING n>=?", (int(h["run_failures_for_incident"]),)).fetchall():
            i = self._incident(f"Incident: runs keep failing on {r['task_id']}",
                               f"{r['n']} runs of the {r['stage']} stage {r['task_id']} ended without a valid result "
                               f"(last employee {r['emp']}). Read their driver.log files under "
                               f"{self.home.runtime_dir / 'runs'} and fix the harness, model or brief.")
            if i:
                out.append(i)
        for sc in self.wconn.execute("SELECT * FROM schedules WHERE enabled=1 AND failure_streak>=?",
                                     (int(h["schedule_failures_for_incident"]),)).fetchall():
            r = self._incident(f"Incident: schedule {sc['id']} keeps failing",
                               f"Schedule {sc['id']} ({sc['kind']} for {sc['employee_id']}) failed "
                               f"{sc['failure_streak']} times in a row. See {self.home.runtime_dir / 'dispatch.log'}.")
            if r:
                out.append(r)
        return out

    def _probation_reviews(self) -> list[dict]:
        days = int(self.config["cadence"]["probation_days"])
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        out = []
        for r in self.wconn.execute("SELECT id FROM employees WHERE status='probation' AND hired_at<?", (cutoff,)):
            e = self.org.employee(r["id"])
            head = self.org.head_of(e["dept_id"])
            approver = approver_ref(head["id"]) if head and head["id"] != e["id"] else None
            if approver is None:
                ceo = self.org.ceo()
                approver = approver_ref(ceo["id"]) if ceo and ceo["id"] != e["id"] else CHAIR
            rec = self.org.review(e["id"])
            a = self.work.propose(
                title=f"Confirm {e['name']} ({e['id']}) after probation",
                intent=f"{e['name']}, {e['title']}, has been on probation for over {days} days. Record: "
                       f"{json.dumps({k: rec[k] for k in ('execution', 'planning')})}. Approve to make them active; "
                       "reject and then pause or fire them if the record does not justify it.",
                steps=f"On approval the kernel sets {e['id']} to active.",
                apply={"kind": "employee_status", "employee_id": e["id"], "status": "active",
                       "reason": "probation passed (approved proposal)"},
                approver=approver, grade=1, task_class="staffing", dept=e["dept"], author="kernel")
            out.append({"probation_review": a["id"], "employee": e["id"]})
        return out

    def _department_retros(self) -> list[dict]:
        c = self.config["cadence"]
        out = []
        for d in self.org.depts():
            if not any("retro" in e["stages"] for e in self.org.employees(d["id"])):
                continue
            props = self.work.messages(to_refs=[f"dept:{d['slug']}"], kind="proposal", unacked=True, limit=500)
            props = [m for m in props if m["to_ref"] == f"dept:{d['slug']}"]
            if len(props) < int(c["dept_retro_min_proposals"]):
                continue
            last = self.wconn.execute("SELECT max(created_at) FROM tasks WHERE stage IS NULL AND task_class='retro' "
                                      "AND dept_id=?", (d["id"],)).fetchone()[0]
            open_ = self.wconn.execute("SELECT 1 FROM tasks WHERE stage IS NULL AND task_class='retro' AND dept_id=? "
                                       "AND status='open'", (d["id"],)).fetchone()
            due = last is None or last < (datetime.now(timezone.utc) - timedelta(days=int(c["dept_retro_days"]))
                                          ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            if open_ or not (due or len(props) >= 5):
                continue
            with write_txn(self.wconn):
                w = self.work._add_work(f"Department retro: {d['slug']} {now()[:10]}",
                                        f"Review {len(props)} pending proposals and the department's metrics "
                                        "(`holon metrics --dept " + d["slug"] + "`); turn what should change into actions.",
                                        dept=d["id"], source="kernel", requested_by="kernel", task_class="retro",
                                        expand=False)
                self.work._add_stage(w, "retro", [])
            out.append({"dept_retro": w["id"], "dept": d["slug"], "proposals": len(props)})
        return out

    # ── sensors for the loops: what stewards, heads and the Chair steer by ──
    def metrics(self, dept: str | None = None, days: int = 30) -> dict:
        """Each metric is paired with its counter-metric so a department cannot improve one by damaging another."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        dept_id = self.org.dept(dept)["id"] if dept else None
        where, args = "t.created_at>?", [since]
        if dept_id:
            where += " AND t.dept_id=?"
            args.append(dept_id)
        q = lambda sql, extra=(): self.wconn.execute(sql.format(where=where), [*args, *extra]).fetchone()  # noqa: E731
        verifies = q("SELECT count(*), sum(t.outcome='pass') FROM tasks t WHERE t.stage='verify' AND t.status='done' "
                     "AND {where}")
        first = q("SELECT count(*), sum((SELECT max(e.attempt) FROM tasks e WHERE e.parent_id=t.parent_id "
                  "AND e.stage='execute')=1) FROM tasks t WHERE t.stage='verify' AND t.outcome='pass' AND {where}")
        execs = q("SELECT count(DISTINCT t.parent_id), sum(t.attempt>1) FROM tasks t WHERE t.stage='execute' AND {where}")
        gates = q("SELECT count(*), avg((julianday(t.decided_at)-julianday(t.created_at))*24), "
                  "sum(t.outcome='reject') FROM tasks t WHERE t.stage='approve' AND t.decided_at IS NOT NULL AND {where}")
        backlog = q("SELECT count(*), min(t.created_at) FROM tasks t WHERE t.stage IS NOT NULL AND t.status='ready' "
                    "AND {where}")
        cost = q("SELECT sum(r.cost), sum(coalesce(r.tokens_in,0)+coalesce(r.tokens_out,0)) FROM runs r JOIN tasks t "
                 "ON t.id=r.task_id WHERE {where}")
        verified = q("SELECT count(*) FROM outputs o JOIN tasks t ON t.id=o.task_id WHERE o.verified=1 AND {where}")[0]
        cycle = {r[0]: round(r[1], 2) for r in self.wconn.execute(
            "SELECT t.stage, avg((julianday(r.ended_at)-julianday(r.started_at))*24) FROM runs r JOIN tasks t "
            f"ON t.id=r.task_id WHERE r.status='succeeded' AND {where} GROUP BY t.stage", args) if r[1] is not None}
        terrain = self.memory.conn.execute(
            "SELECT count(*), sum(uses>0) FROM items WHERE provenance LIKE 'terrain:%' AND superseded_by IS NULL"
            + (" AND scope=?" if dept else ""), ([f"dept:{dept}"] if dept else [])).fetchone()
        ratio = lambda a, b: round(a / b, 3) if b else None  # noqa: E731
        backlog_age_h = None
        if backlog[1]:
            backlog_age_h = round((datetime.now(timezone.utc) - datetime.strptime(
                backlog[1], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)).total_seconds() / 3600, 1)
        return {
            "dept": dept or "(organization)", "days": days,
            "budget": {**self.budget(dept), **self.budget_usage(dept_id)} if dept_id else None,
            "first_pass_rate": {"value": ratio(first[1] or 0, first[0]), "counter": "verify_pass_rate"},
            "verify_pass_rate": {"value": ratio(verifies[1] or 0, verifies[0]), "counter": "rework_rate"},
            "rework_rate": {"value": ratio(execs[1] or 0, execs[0]), "counter": "first_pass_rate"},
            "gate_latency_h": {"value": round(gates[1], 2) if gates[1] is not None else None,
                               "counter": "rejection_rate"},
            "rejection_rate": {"value": ratio(gates[2] or 0, gates[0]), "counter": "gate_latency_h"},
            "cycle_time_h_by_stage": cycle,
            "backlog": {"ready_stages": backlog[0], "oldest_age_h": backlog_age_h},
            "research_reuse": {"value": ratio(terrain[1] or 0, terrain[0]), "counter": "first_pass_rate"},
            "cost_per_verified_result": {"value": ratio(cost[0] or 0, verified), "tokens": cost[1] or 0,
                                         "counter": "verify_pass_rate"},
        }
