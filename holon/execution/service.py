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
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from holon.capabilities import decisions, kb as kb_mod, playbooks
from holon.capabilities.context import DEFAULT_BUDGET_CHARS, compile_context, identity_card
from holon.capabilities.decisions import Decider, choice, noul, p_yes, picked
from holon.capabilities.memory import Memory
from holon.capabilities import skills as skills_mod
from holon.capabilities.skills import Library
from holon.core import contract, vocab
from holon.core.contract import EnvelopeError
from holon.bylaws import units
from holon.core.db import connect, migrate, now, write_txn
from holon.core.paths import Home
from holon.core.vocab import CHAIR, EXECUTIVE
from holon.execution import autonomy, briefs, drivers, bylaws, grading, isolation, security
from holon.execution.briefs import kernel_env
from holon.execution import policy as run_policy
from holon.execution.org import Org, approver_ref
from holon.execution.cron import Cron
from holon.execution.schedules import Schedules
from holon.execution import staleness
from holon.execution.work import CHECKING, HELD, Claimant, Rules, TransitionError, Work

DEFAULT_CONFIG = {
    "context": {"budget_chars": DEFAULT_BUDGET_CHARS, "recall_floor_chars": 3600},
    "planner": {"enforce_read_only": True, "trust_harness_read_only": False, "guard_ignore": []},
    "grading": {},
    "autonomy": {},
    "pipeline": {},
    "memory": {"promotion_threshold": 2},
    "healing": {"incident_dept": "it", "lease_expiries_for_incident": 2, "schedule_failures_for_incident": 3,
                "run_failures_for_incident": 3, "doctor_every_min": 30, "backup_every_h": 24, "backup_to": "",
                "build_regression_samples": 10, "build_regression_drop": 0.25},
    "retention": {"runs_days": 0, "messages_days": 0, "backup_max_age_days": 7, "backup_keep": 3},
    "pilot": {"depts": []},   # pilot departments: outside-reach floors at G4 — the Chair's call, job by job
    # G4 (the Chair-key-free external intake): [intake.dept.<slug>] names the grade floor and
    # the task classes a department's intake key may file. The key itself lives with the
    # scanner; only its hash is stored, and the key can never approve or decide.
    "intake": {"dept": {}},
    # department secrets (G8, docs/BYLAWS.md §6): [secrets.dept.<slug>] VAR = path to a 0600 key file;
    # resolved into the run's environment at claim — the value never lives in config
    "secrets": {"dept": {}},
    "cadence": {"dept_retro_days": 7, "dept_retro_min_proposals": 1, "probation_days": 14},
    # a department rate past its bound (over min_samples) makes its retro due without proposals;
    # a _min of 0 or a _max of 1 turns that bound off
    "metrics": {"min_samples": 5, "verify_pass_rate_min": 0.5, "rework_rate_max": 0.5, "rejection_rate_max": 0.5,
                "rubric_agreement_min": 0.8},
    "budget": {"max_running_per_dept": 0, "max_runs_per_dept_per_day": 0,   # 0 = no cap
               "tokens_per_run": 0, "cost_per_run": 0.0},   # H5: compiled into policy.json; 0 = no cap
    "notify": {"command": ""},   # e.g. a chat CLI with {message}; empty = no notifications
    "events": {"cooldown_s": 300},   # an event routine fires at most once per this (H7)
    "chair": {"channels": []},       # '<platform>:<user id>' refs whose channel messages are the Chair's (H8)
    # what employees may reach beyond their work's workdir; [scope.dept.<slug>] adds to it per department
    "scope": {"roots": [], "commands": [], "act_commands": []},
    # machine checks the kernel runs in the workdir after every execute, before any verifier;
    # [checks.dept.<slug>] and [checks.class.<task_class>] add their own
    "checks": {"commands": [], "timeout_s": 600},
    # lanes (docs/BYLAWS.md §10): fast = one session for routine (G0) work; script = the kernel runs a command
    "lanes": {"fast": True, "script_timeout_s": 600, "external_p_max": 0.5},
    # a decision model for lane choice, rubric judging, failure triage and routing; empty url = off
    "classifier": dict(decisions.DEFAULTS),
    # the trust boundary (docs/BYLAWS.md §11): agents run as agent_user, through the runner, and reach the
    # organization only through the broker; empty agent_user = off (agents run as the kernel's user)
    "isolation": {"agent_user": "", "broker": "", "runner": "", "grants": [], "reads": []},
    # playbooks (docs/BYLAWS.md §12): refinement is due after refine_after runs on a version, on any verifier
    # failure or judge/verifier disagreement, or when a criterion averages below the pass bar over
    # min_judged results; a task class gets its first playbook after class_after verified results
    "playbooks": {"refine_after": 5, "min_judged": 2, "class_after": 3},
    # objectives: reviewed when their pool finishes a cycle, and at least every review_every_days
    "objectives": {"review_every_days": 7},
    # smoke checks (docs/BYLAWS.md §13): [[smoke.checks]] for everyone, [[smoke.dept.<slug>.checks]] or
    # [smoke.dept.<slug>] file = "<toml with [[checks]]>" per department; a critical failure blocks the
    # department's agents until it passes; results are cached for every_min
    "smoke": {"checks": [], "every_min": 30, "timeout_s": 60},
}
OBJECTIVE_PLAN = (
    "This work is an objective: an outcome the organization pursues over time, judged by the key results in its "
    "acceptance. Decompose it (kind `decompose`) into the pool that achieves it: one-off work for what is done "
    "once, and routines for what must happen repeatedly. A child with a `cron` is a routine: give it a `lane` "
    "(`script` with a `command` when the job is deterministic; `fast` for small, bounded judgment; `full` for "
    "open-ended work that needs its own plan each time) and a `rubric`: checkable criteria that tie each result "
    "to the key results. Routines and their rubrics start rough on purpose: every verified run refines their "
    "playbook, and the objective's review reshapes the pool.")
OBJECTIVE_REVIEW = (
    "Review this objective against its key results, using the dashboard below. Say in `summary` how far each key "
    "result is, and in `cause` what most limits progress. Your `actions` reshape the pool: new one-off work, new "
    "routines (`cron`, `lane`, `rubric`; a routine waits for its approver), or work to change a routine or its "
    "playbook. Retiring a routine is the Chair's (`holon schedule disable`): ask for it in an action. Set "
    "`objective_met` only when every key result is met by verified results.")
# what each lane costs and is for: the classifier's options, and the order "cheapest first" means
LANE_OPTIONS = {
    "script": "deterministic: fixed inputs and fixed steps with no judgment; a shell or Python command does it "
              "the same way every time",
    "fast": "needs some language judgment (read, summarise, classify, draft, decide) but is small, bounded and "
            "routine; one agent session does it",
    "full": "open-ended, multi-step, risky, novel or consequential; needs a plan, an approval and an independent "
            "check",
}
assert tuple(LANE_OPTIONS) == vocab.LANES
FAILURE_CAUSES = {
    "harness": "the agent harness crashed, hung, looped or misbehaved",
    "model": "the model endpoint errored, refused, rate-limited, timed out or returned garbage",
    "brief": "the agent misunderstood the task or did not return the result contract",
    "environment": "a missing tool, file, permission, credential or network path",
    "work": "the work itself is impossible, contradictory or underspecified",
}
CONFIG_KEYS = {s: set(v) for s, v in DEFAULT_CONFIG.items() if s not in ("grading", "autonomy", "pipeline")}
CONFIG_KEYS["budget"] |= {"dept", "employee"}  # [budget.dept.<slug>], [budget.employee.<id>] overrides
CONFIG_KEYS["scope"].add("dept")   # [scope.dept.<slug>] additions
CONFIG_KEYS["checks"] |= {"dept", "class"}  # [checks.dept.<slug>], [checks.class.<task_class>]
CONFIG_KEYS["smoke"].add("dept")  # [smoke.dept.<slug>]
CONFIG_KEYS["classes"] = set()   # [classes] org = [...], <dept slug> = [...]: checked by lint against departments


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


def _disk_refusals(run_dir) -> list[dict]:
    """The refusals the harness recorded on its own, in `<run dir>/refusals.jsonl`.

    Measured 2026-10-21: harnesses write this file (one JSON object per refused call) and the
    kernel read nothing — `grep -rn refusals.jsonl holon/` returns zero readers. So a body that
    refused a call and recorded it the harness's way lost the refusal entirely, and with it the
    blocker H-03 promises the verifier. The envelope's own `refusals[]` still wins; this is the
    fallback, and it is read through the same agent-file reader (no symlink hop, bounded size) as
    every other artifact the body leaves.
    """
    from holon.execution import isolation
    try:
        text = isolation.read_agent_file(Path(run_dir) / "refusals.jsonl")
    except (isolation.IsolationError, OSError, ValueError):
        return []
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            one = json.loads(line)
        except ValueError:
            continue  # a truncated last line is not a reason to lose the rest
        if isinstance(one, dict) and one.get("tool"):
            out.append(one)
    return out


class KernelRules(Rules):
    """The department bylaws deciding which optional stages a piece of work goes through, and the
    feedback loops that fire when work goes wrong."""

    def __init__(self, k: "Kernel"):
        self.k = k

    def _pipeline(self, work: dict) -> dict:
        p = {**bylaws.PIPELINE_DEFAULTS, **self.k.config.get("pipeline", {})}
        if work.get("dept_id"):
            g = self.k.org.dept(work["dept_id"]).get("bylaws")
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
        if work.get("task_class") == "playbook-refinement" and work.get("source") == "kernel":
            return False  # its intent is the research: the kernel compiled the evidence
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

    def fast_refusal(self, work: dict) -> str | None:
        """The fast lane skips plan and approval, so only work the kernel itself would grade G0 may take it
        (low risk, a task class with a verified track record); whoever asked for the lane."""
        if not self.k.config["lanes"]["fast"]:
            return "[lanes] fast is off"
        s = self.k.scope(self.k._dept_slug(work.get("dept_id")))
        g = grading.grade_plan(self.k.wconn, work, {"grade": "G0"}, self.k.config,
                               reach=s["roots"], read_only_reach=s["read_roots"])
        return None if g["floor"] == 0 else f"its grade floor is G{g['floor']} ({'; '.join(g['reasons'])})"


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
        self.org = Org(self.wconn, actor, self.config, self.home)
        self.work.appliers["authority"] = lambda a: self.org.write_authority(
            a["approver"], a["task_class"], a["max_grade"], a["reason"])
        self.work.appliers["employee_status"] = self._apply_status
        self.work.appliers["routine"] = self._apply_routine
        self.decider = Decider(self.config["classifier"])
        self._build_cache: dict[str, str | None] = {}   # driver → harness build (H10), one probe per process
        self.skills = Library(home)
        self.schedules = Schedules(self.wconn, actor)
        self.org.bootstrap()
        self.auth = security.Auth(self.wconn, home)

    def close(self) -> None:
        self.wconn.close()
        self.mconn.close()

    # ── helpers ───────────────────────────────────────────────────────────
    @property
    def isolation(self) -> dict | None:
        """{"agent_user", "broker", "runner"} when agents run as their own user, else None."""
        c = self.config["isolation"]
        if not c["agent_user"]:
            return None
        iid = self.auth.instance_id
        return {"agent_user": c["agent_user"], "broker": c["broker"] or f"@holon-broker-{iid}",
                "runner": c["runner"] or f"@holon-runner-{iid}"}

    def _exec(self, cmd: str, cwd: str, env: dict, timeout_s: float) -> tuple[int, str]:
        """A command the kernel runs for a work (a check, a script): as the agent user when isolated, since
        it runs code an agent may have written. (exit code, output)."""
        iso = self.isolation
        if iso:
            code, out, err = isolation.run(iso["runner"], cmd, cwd, env, timeout_s)
        else:
            code, out, err = drivers.execute(cmd, cwd, {**self._check_env(), **env}, timeout_s)
        return code, out + err

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
                        is_ceo=bool(ceo and ceo["id"] == e["id"]), in_executive=e["dept"] == EXECUTIVE,
                        task_classes=tuple(e["task_classes"]) if e.get("task_classes") else None), e

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
        if out["routines"]:
            author = (self.work.output(out["plan"]["id"]) or {"body": {}})["body"].get("author")
            out["routines"] = self._routines_from_items(out["routines"], out["decider"],
                                                        f"emp:{author}" if author else out["decider"])
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
            self._register_guard(out["work"])
        plan = self.work.approved_plan(out["work"]["id"])
        change = None
        decider = plan["approval"]["decided_by"] if plan else None
        if decider and decider.startswith("emp:"):
            change = self._earned_authority(decider, out["work"].get("task_class"))
        return {**out, "authority_change": change}

    def _register_guard(self, work: dict) -> None:
        """K-2: an incident's verified fix may register one guard — the fixer's `guard` field names a
        [checks.class] entry (the Chair authored the command; the fixer only names it, because the
        heartbeat runs guards as its own user). The heartbeat runs it forever; a failure reopens the
        incident. A fix that registers nothing is a doctor warning until waived."""
        if work.get("task_class") != "incident":
            return
        ex = self.work.latest(work["id"], "execute")
        body = (self.work.output(ex["id"]) or {}).get("body") or {} if ex else {}
        g = body.get("guard")
        if not isinstance(g, dict) or not g.get("check"):
            return
        name = (g.get("name") or work["title"][:60]).strip()
        with write_txn(self.wconn):
            self.wconn.execute("INSERT OR REPLACE INTO guards VALUES (?,?,?,?,?)",
                               (work["id"], name, g["check"], int(g.get("timeout") or 60), now()))
            self.work._event("work", work["id"], None, None, f"guard registered: {name} ({g['check']})")

    def _guard_triggers(self) -> list[dict]:
        """K-2: registered guards run on the heartbeat like smoke checks; a failing one reopens its
        incident — a fixed defect is re-checked forever, not just remembered. A guard names a
        [checks.class] entry: the heartbeat runs the Chair's command in the fix's workdir, never
        shell an employee wrote."""
        out = []
        for g in self.wconn.execute("SELECT * FROM guards").fetchall():
            cmds = ((self.config.get("checks") or {}).get("class") or {}).get(g["check"], {}).get("commands", [])
            w = self.work.get(g["work_id"])
            cwd = w.get("workdir") if w and w.get("workdir") and Path(w["workdir"]).is_dir() else str(self.home.root)
            code, tail = (0, "")
            if not cmds:
                code, tail = 1, f"[checks.class.{g['check']}] no longer exists: the guard is dead — " \
                                "reregister it or waive the incident"
            for cmd in cmds:
                c, t = self._exec(cmd, cwd, {}, g["timeout"] or 60)
                code, tail = (code or c, tail or t)
            if code:
                r = self._incident(f"Incident: guard '{g['name']}' failed",
                                   f"The guard registered by the fix of {g['work_id']} "
                                   f"([checks.class.{g['check']}], run in {cwd}) exited {code}:\n```\n"
                                   f"{tail.strip()[-400:]}\n```\n"
                                   "This defect was fixed and verified once; it is back. Find what regressed, "
                                   "fix it, and the guard passing again closes this.")
                if r:
                    out.append(r)
        return out

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
                              exclude_depts=self.over_budget() + self.smoke_blocked(), max_running=max_running,
                              exclude_tasks=self.backed_off())
        if got is None:
            return None
        node, run = got
        work = self.work.work_of(node)
        try:   # G8: only the acting stage carries a department's secrets — a missing key file
            # must refuse an execute, never stop an observer from looking
            secrets_env = self._dept_secrets(self._dept_slug(node["dept_id"])) if node["stage"] == "execute" else {}
        except ValueError as e:   # a secret that cannot be read refuses the run, never runs it empty
            self.work.release(run["id"], "failed", f"secret refused: {e}")
            return None
        pb = self.playbook_of(work) if node["stage"] in ("plan", "execute", "verify") else None
        if pb and node["stage"] == "execute":
            with write_txn(self.wconn):
                self.wconn.execute("UPDATE tasks SET playbook=?, playbook_version=? WHERE id=?",
                                   (pb["name"], pb["version"], work["id"]))
        if node["stage"] == "execute" and self._dept_slug(node["dept_id"]):
            self._snapshot_skills(run["id"], self._dept_slug(node["dept_id"]))
        extra = []
        if pb:
            extra.append((f"Playbook: {pb['name']} v{pb['version']} (how this job is done and judged)",
                          pb["protocol"] + "\n\n### Rubric\n\n" + "\n".join(f"- {c}" for c in pb["rubric"])))
        if work.get("task_class") == "objective" and node["stage"] in ("plan", "retro"):
            extra.append(("Objective", OBJECTIVE_PLAN if node["stage"] == "plan" else OBJECTIVE_REVIEW))
            extra.append(("Objective dashboard", self.objective_dashboard(work["id"])))
        if node["stage"] in ("plan", "shape", "retro"):
            others = [d for d in self.org.depts() if d["id"] != work.get("dept_id")]
            if others:
                extra.append(("Other departments (what each is for)", "\n".join(
                    f"- `{d['slug']}`: {d.get('mission') or d['name']}" for d in others)
                    + "\n\nWork another department should do is a child with its `dept` (in a decomposition) or "
                      "`$holon msg request <dept> …`; its head shapes it."))
        md, _ = compile_context(self.home, self.work, self.memory, node["id"],
                                budget=int(self.config["context"]["budget_chars"]), run_id=run["id"],
                                recall_floor=int(self.config["context"]["recall_floor_chars"]),
                                employee=emp, predecessors=self.org.predecessors(emp["id"]) if emp else None,
                                extra=extra)
        slug = self._dept_slug(node["dept_id"])
        if self.classes_for(slug) and node["stage"] in ("shape", "plan", "retro"):
            md += ("\n\n## Task classes\n\nNew or split work in " + (slug or "the organization") + " uses one of: "
                   + ", ".join(f"`{c}`" for c in self.classes_for(slug)) + ".\n")
        if node["stage"] == "plan":
            md += self._grade_floor_section(self.work.work_of(node), slug, employee_id)
        scope = self.scope(slug)
        plan_out = None
        if node["stage"] == "execute":
            approved = self.work.approved_plan(node["parent_id"] or node["id"])
            plan_out = (approved or {}).get("output", {}).get("body") if approved else None
        policy_doc = run_policy.compile_policy(
            node, work, scope, plan_out,
            run_policy.per_run_budget(self.config, slug, emp["id"] if emp else None))
        bdir = briefs.build(self.home, run, node, md, self.skills, slug, emp,
                            self.identity(emp["id"]) if emp else None, token, scope=scope,
                            policy=policy_doc, secrets=secrets_env)
        if self.isolation:
            isolation.share_run_dir(bdir, self.isolation["agent_user"])
        self.work.set_run_paths(run["id"], str(bdir), str(bdir / "driver.log"))
        return Claimed(run=self.work.get_run(run["id"]), node=node, brief_dir=bdir, token=token, employee=emp)

    def _dept_secrets(self, slug: str | None) -> dict[str, str]:
        """G8: a department's secrets, resolved from 0600 key files into the run's environment at
        claim — the value never lives in config, never in a message, never in Notion. A key file
        that cannot be read raises: the run is refused with the reason instead of running with an
        empty string, and the repeated refusal reaches the Chair as a failing stage."""
        refs = ((self.config.get("secrets") or {}).get("dept") or {}).get(slug) or {} if slug else {}
        out = {}
        for var, path in sorted(refs.items()):
            p = Path(path).expanduser()
            try:
                out[var] = p.read_text().strip()
            except OSError as e:
                raise ValueError(f"{var} for dept '{slug}': cannot read {p} "
                                 f"({e.strerror or e}) — fix the file or the config, then claim again") from None
        return out

    def _grade_floor_section(self, work: dict, slug: str | None, author: str | None) -> str:
        """What the kernel will compute for a plan of this work, so a planner never has to derive it."""
        reach = self.scope(slug)
        g = grading.grade_plan(self.wconn, work, {"grade": "G0"}, self.config, reach=reach["roots"],
                              read_only_reach=reach["read_roots"])
        approver = self.org.resolve_approver(work, g["floor"], author)
        inside = ", ".join(f"`{p}`" for p in ([work["workdir"]] if work.get("workdir") else [])
                             + reach["roots"]) or "none"
        return ("\n\n## Grade floor (computed by the kernel)\n\n"
                f"Before your plan's own effects, the floor is **G{g['floor']}**:\n"
                + "".join(f"- {r}\n" for r in g["reasons"] if "planner proposed" not in r)
                + f"\nIt rises to G2 for external effects, a decomposition or a `skill:` write; to G2 (G3 when the "
                f"department has a reach) for writes outside {inside}; to G3 for sensitive paths. "
                f"At G{g['floor']} the plan goes to `{approver}`. Propose your own grade from the planner skill's "
                "`references/grading.md`; the effective grade is the higher of the two. `assignee` names the "
                "employee who executes; leave it out and the department's executors claim it.\n")

    def _read_trajectory(self, run_id: str) -> dict | None:
        """trajectory.json (H4): the run's ordered steps — thought summary, tool, arguments digest,
        outcome — compacted for the gold standard. Absent or malformed: dropped, never fatal;
        the trajectory steers the next run, the result decides this one."""
        try:
            text = isolation.read_agent_file(self.home.run_dir(run_id) / "trajectory.json")
            return self.work.compact_trajectory(json.loads(text)) if text else None
        except (json.JSONDecodeError, isolation.IsolationError, ValueError):
            return None

    def _run_usage(self, run: dict, env: dict) -> dict | None:
        """H-04: what the run consumed, for every stage. The harness's own measurement
        (`<run dir>/usage.json`) wins over the envelope's claim (§11); a build neither of them
        names falls back to the driver's build_cmd so H10's pass rates always group by something."""
        u: dict = {}
        try:
            text = isolation.read_agent_file(self.home.run_dir(run["id"]) / "usage.json")
            if text:
                f = json.loads(text)
                if isinstance(f, dict):
                    u = {k: f[k] for k in ("tokens_in", "tokens_out", "cost", "tool_calls", "duration_s", "build")
                         if f.get(k) is not None}
        except (json.JSONDecodeError, isolation.IsolationError, ValueError):
            pass
        e = env.get("usage") if isinstance(env.get("usage"), dict) else {}
        for k in ("tokens_in", "tokens_out", "cost", "tool_calls", "duration_s", "build"):
            if u.get(k) is None and e.get(k) is not None:
                u[k] = e[k]
        if u.get("build") is None and run.get("driver"):
            build = self._driver_build(run["driver"])
            if build:
                u["build"] = build
        return u or None

    def _driver_build(self, driver_name: str) -> str | None:
        """The harness build that ran (H10): the driver's build_cmd, run once per driver per
        process and cached. A cheap version-reporting command; a driver without one, or a
        failing one, reports no build."""
        if driver_name in self._build_cache:
            return self._build_cache[driver_name]
        build = None
        try:
            cmd = getattr(drivers.get(self.home, driver_name), "build_cmd", "") or ""
            if cmd:
                p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
                build = (p.stdout or p.stderr).strip()[:120] if p.returncode == 0 else None
        except (subprocess.SubprocessError, OSError, drivers.DriverError):
            build = None
        self._build_cache[driver_name] = build
        return build

    # ── ingestion: the one place agent output enters the organization ─────
    def submit(self, run_id: str, envelope: dict | None = None, exit_code: int | None = 0,
               driver_output: dict | None = None, defer: bool = False) -> dict:
        """`defer`: the caller is an agent's own process (`holon submit`, MCP, the broker), which its harness
        may be stopped with at any moment once the result is in: an execute result is only recorded, and its
        verify stage held until the kernel runs the checks and judge (`post_execute`) in its own process."""
        run = self.work.get_run(run_id)
        if run["status"] != "running":
            raise TransitionError(f"run {run_id} is '{run['status']}' (late result after lease expiry is discarded)")
        if envelope is None:
            try:
                text = isolation.read_agent_file(self.home.run_dir(run_id) / "result.json")
                envelope = json.loads(text) if text is not None else None
            except json.JSONDecodeError as e:
                envelope = {"_invalid_json": str(e)}
            except isolation.IsolationError as e:
                envelope = {"_invalid_json": str(e)}
        stage = run["kind"]
        try:
            if envelope is None:
                raise EnvelopeError(["no result.json and no JSON output"])
            if "_invalid_json" in envelope:
                raise EnvelopeError([f"result.json: {envelope['_invalid_json']}"])
            env = contract.validate(stage, envelope)
            self._check_org_refs(env, self.work.work_of(run["task_id"]))
            if stage == "execute":
                self._check_skill_changes(self.work.work_of(run["task_id"]), env)
                self._check_guard(self.work.work_of(run["task_id"]), env)
        except EnvelopeError as e:
            return self._contract_breach(run, e.problems, driver_output, exit_code)
        node = self.work.get(run["task_id"])
        work = self.work.get(node["parent_id"])
        emp = self.org.employee(run["employee_id"]) if run["employee_id"] else None
        # H-03: refusals the harness recorded under policy.json are blockers, and they must be in the
        # stored result before anything judges it — an execute that succeeded only by shrinking from
        # its plan never verifies clean without a decision on what it refused.
        #
        # The same seam as below: the disk file is the fallback for a body that recorded its
        # refusals the harness's own way, and the envelope still wins.
        disk_refusals = _disk_refusals(self.home.run_dir(run_id))
        if stage == "execute" and env.get("outcome") == "success" and (env.get("refusals") or disk_refusals):
            all_of = env.get("refusals") or disk_refusals
            env = {**env, "refusals": all_of,
                   "blockers": [*env.get("blockers", []),
                   *[f"refused: {r.get('tool', '?')} under policy" + (f" ({r['rule']})" if r.get("rule") else "")
                     for r in all_of]]}
        if stage == "research":
            out = self.work.submit_research(run_id, env, exit_code)
        elif stage == "shape":
            out = self.work.submit_shape(run_id, env, exit_code)
        elif stage == "plan":
            if env["kind"] == "needs_input":
                out = self.work.submit_plan(run_id, env, exit_code=exit_code)
            else:
                env = self._canonical_writes(env, work)
                s = self.scope(self._dept_slug(work["dept_id"]))
                g = grading.grade_plan(self.wconn, work, env, self.config,
                                       reach=s["roots"], read_only_reach=s["read_roots"])
                approver = self.org.resolve_approver(work, g["grade"], run["employee_id"])
                out = self.work.submit_plan(run_id, env, g, approver, exit_code)
        elif stage == "approve":
            out = self.decide(node["id"], env["decision"], env.get("note"), review_run=run_id)
        elif stage == "execute":
            traj = self._read_trajectory(run_id)
            if traj:
                env["_trajectory"] = traj
            u = self._run_usage(run, env)   # before the judge: its rows carry this run's build (H10)
            if u:
                self.work.set_run_usage(run_id, u.get("tokens_in"), u.get("tokens_out"), u.get("cost"),
                                         u.get("tool_calls"), u.get("duration_s"), u.get("build"))
            out = self.work.submit_execute(run_id, env, exit_code)
            self._pin_artifacts(work, env, run_id)
            self._hold_direct_skill_writes(work["id"], self.work.output(node["id"]), env)  # whatever the outcome
            v = self.work.latest(work["id"], "verify", "ready")
            if v and defer:
                with write_txn(self.wconn):
                    self.wconn.execute("UPDATE tasks SET blocked_reason=? WHERE id=?", (HELD, v["id"]))
                out["post_execute"] = "deferred to the kernel"
            else:
                out.update(self.post_execute(work["id"]))
        elif stage == "verify":
            out = self.verify(node["id"], env["verdict"], env["note"], run_id=run_id)
        else:
            out = self.work.submit_retro(run_id, env, exit_code)
            if out["routines"]:
                out["routines"] = self._routines_from_items(out["routines"], None, f"emp:{run['employee_id']}")
            if work.get("task_class") == "retro" and work.get("dept_id"):
                self._ack_proposals(work["dept_id"])
        # H-04: usage is recorded for every stage — what the harness measured in usage.json wins
        # over the envelope's claim (§11), and a run that reports no build falls back to its
        # driver's build_cmd so pass rates always group by something (H10). Execute recorded
        # its usage before its judge ran, above.
        if stage != "execute":
            u = self._run_usage(run, env)
            if u:
                self.work.set_run_usage(run_id, u.get("tokens_in"), u.get("tokens_out"), u.get("cost"),
                                         u.get("tool_calls"), u.get("duration_s"), u.get("build"))
        # H3: refusals the harness recorded under policy.json are blockers, never
        # breaches — surface them on the run's event trail and in the submit output so
        # rework/retro sees why the stage shrank from its plan.
        #
        # The envelope is the body's own hand-in and stays authoritative. Measured 2026-10-21:
        # `refusals.jsonl` is written by harnesses into the run directory and read by nothing in
        # the kernel, so a body that records a refusal the harness's own way — and does not mirror
        # it into its envelope — has it silently dropped, and the blocker H-03 promises never
        # reaches the verifier. Read the file when the envelope has nothing to say.
        refusals = env.get("refusals") or disk_refusals
        if refusals:
            note = "; ".join(f"{r.get('tool', '?')}: {r.get('reason', '?')}" for r in refusals)[:400]
            with write_txn(self.wconn):
                self.work._event("run", run_id, None, None, f"policy refusal: {note}")
            out["refusals"] = refusals
        out["memory"] = self._consolidate(work, run, env, emp, stage)
        out["proposals_routed"] = self._route_proposals(work, env, emp)
        return {**out, "stage": stage}

    def post_execute(self, work_id: str) -> dict:
        """What follows an execute result, in the kernel's process: machine checks, then the lane's
        verification (rubric judge) or trusted sampling. Idempotent and exclusive: a held verify stage is
        taken by one process at a time; if this process dies, the heartbeat retakes it."""
        v = self.work.latest(work_id, "verify", "ready")
        if not v:
            return {}
        with write_txn(self.wconn):
            if v["blocked_reason"] in (HELD, CHECKING) and not self.wconn.execute(
                    "UPDATE tasks SET blocked_reason=?, updated_at=? WHERE id=? AND blocked_reason=?",
                    (CHECKING, now(), v["id"], HELD)).rowcount:
                return {"post_execute": "already running"}
        ex = self.work.get(self.work.needs(v["id"])[0])
        o = self.work.output(ex["id"])
        env, run = {**o["body"], "documentation": o["documentation"]}, self.work.get_run(o["run_id"])
        out: dict = {}
        try:
            if env["outcome"] != "failure":
                checks = self.run_checks(self.work.get(work_id))
                out["checks"] = checks
                if checks and not all(c["ok"] for c in checks):
                    failed = [c for c in checks if not c["ok"]]
                    note = "Machine checks failed (run by the kernel; no verifier was spent):\n" + "\n".join(
                        f"- `{c['command']}` exited {c['exit_code']}:\n```\n{c['tail']}\n```" for c in failed)
                    self._release(v["id"])
                    out["work"] = self.verify(work_id, "fail", note)["work"]
            if not out.get("checks") or all(c["ok"] for c in out["checks"]):
                self._release(v["id"])
                w = self.work.get(work_id)
                skipped = self._lane_verification(w, run, env) if w["lane"] in ("fast", "script") \
                    else self._sample_verification(w, run, env)
                out["verification_skipped"] = bool(skipped)
                if skipped:
                    out["work"] = skipped
                    if skipped["status"] == "done":
                        out["skill_changes"] = self.apply_skill_changes(skipped)
        finally:
            self._release(v["id"])  # whatever happened, a verifier may now look
        return out

    def _release(self, stage_id: str) -> None:
        with write_txn(self.wconn):
            self.wconn.execute("UPDATE tasks SET blocked_reason=NULL WHERE id=? AND blocked_reason IN (?,?)",
                               (stage_id, HELD, CHECKING))

    def held_checks(self) -> list[str]:
        """Works whose result waits for the kernel's checks: held over a minute (the dispatcher had its
        chance), or checking for over an hour (that process died)."""
        a = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        b = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with write_txn(self.wconn):
            self.wconn.execute("UPDATE tasks SET blocked_reason=? WHERE stage='verify' AND status='ready' AND "
                               "blocked_reason=? AND updated_at<?", (HELD, CHECKING, b))
        return [r[0] for r in self.wconn.execute("SELECT parent_id FROM tasks WHERE stage='verify' AND status='ready' "
                                                 "AND blocked_reason=? AND updated_at<?", (HELD, a))]

    # ── skills are written by the kernel, after verification; a direct write is taken back and held ──
    def _skill_files(self, slug: str) -> dict[str, str]:
        layer = self.skills.instance / f"@{slug}" if self.skills.instance else None
        if not layer or not layer.is_dir():
            return {}
        return {str(p.relative_to(layer)): p.read_text(errors="replace") for p in sorted(layer.rglob("*.md"))}

    def _snapshot_skills(self, run_id: str, slug: str) -> None:
        with write_txn(self.wconn):
            self.wconn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",
                               (f"skills:{run_id}", json.dumps({"dept": slug, "files": self._skill_files(slug)})))

    def _hold_direct_skill_writes(self, work_id: str, output: dict, env: dict) -> dict:
        """An executor that edited its department's skill files directly (it could, without isolation) has
        changed how the department works before anyone verified it. The kernel restores the files as they
        were when the run started; an edit its approved plan named becomes a proposed `skill_changes` entry,
        applied only if verification passes; any other edit is a breach, recorded for the Chair."""
        raw = self._setting(f"skills:{output['run_id']}") if output.get("run_id") else None
        if not raw:
            return env
        snap = json.loads(raw)
        slug, before, now_ = snap["dept"], snap["files"], self._skill_files(snap["dept"])
        changed = sorted(f for f in set(before) | set(now_) if before.get(f) != now_.get(f))
        if not changed:
            return env
        plan = self.work.approved_plan(work_id)
        named = set(((plan or {}).get("output") or {"body": {}})["body"].get("writes") or [])
        layer = self.skills.instance / f"@{slug}"
        held, breached = list(env.get("skill_changes") or []), []
        for f in changed:
            p = layer / f
            if f in before:
                p.write_text(before[f])
            elif p.exists():
                p.unlink()
            skill, _, file = f.partition("/")
            if f"skill:{f}" in named and f in now_ and not any(c["skill"] == skill and c["file"] == file for c in held):
                held.append({"skill": skill, "file": file, "content": now_[f],
                             "why": "written directly by the executor; held by the kernel until verified"})
            elif f"skill:{f}" not in named:
                breached.append(f)
        body = {**output["body"], "skill_changes": held}
        with write_txn(self.wconn):
            self.wconn.execute("UPDATE outputs SET body=? WHERE id=?", (json.dumps(body, ensure_ascii=False), output["id"]))
            self.work._event("task", work_id, None, None, (f"skill writes held for verification: {changed}"
                             + (f"; lane breach: skill files the plan did not name were restored: {breached}"
                                if breached else ""))[:900], actor="kernel")
        return {**env, "skill_changes": held}

    def _canonical_writes(self, env: dict, work: dict) -> dict:
        """A plan that names a file in its department's skill layer by path means a skill change: it becomes
        `skill:<skill>/<file>`, so it is graded as one, its executor returns the file in `skill_changes`, and
        it lands only when verified. Any other path into the instance (databases, config) is kernel state no
        plan may write: it is kept as named, which grades it beyond the department's reach."""
        slug = self._dept_slug(work.get("dept_id"))
        if not slug or not env.get("writes") or not self.skills.instance:
            return env
        layer = (self.skills.instance / f"@{slug}").resolve()
        out = []
        for w in env["writes"]:
            p = Path(w).expanduser() if isinstance(w, str) and not w.startswith("skill:") else None
            rel = p.resolve().relative_to(layer) if p and p.is_absolute() and p.resolve().is_relative_to(layer) else None
            out.append(f"skill:{rel.parts[0]}/{'/'.join(rel.parts[1:])}" if rel and len(rel.parts) >= 2 else w)
        return {**env, "writes": out}

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
        Trusted = the executor's last `trusted_after` verified results in this task class all passed.
        A result carrying blockers never closes unverified (H-03): nobody may sample away the
        decision on what it refused or shrank from."""
        if work["status"] != "open" or env["outcome"] != "success" or not run["employee_id"] \
                or env.get("blockers"):
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

    def _pin_artifacts(self, work: dict, env: dict, run_id: str) -> None:
        """A2: `artifacts` had no reader at all — the field is defined in the contract and named
        once in the executor skill, and no line of code consumed it, so a result's durable work
        rotted in `outputs` where only that result would ever see it, and an artifact pointer that
        stopped resolving was invisible.

        Each artifact the run actually produced is pinned to memory as an `artifact` item at the
        *work's* scope (the organization's record, not the employee's), with the run's id in the
        provenance so a reader can follow it back to the run that wrote it. A later identical
        artifact reinforces the same item rather than duplicating it, which is memory's own
        fingerprint rule. A `failure` is skipped: its artifacts are claims about work that did not
        finish, and pinning them would teach the organization to trust paths that may not exist."""
        if env.get("outcome") not in ("success", "partial"):
            return
        slug = self._dept_slug(work["dept_id"]) if work.get("dept_id") else None
        scope = f"dept:{slug}" if slug else "org"
        for a in env.get("artifacts") or []:
            if not isinstance(a, str) or not a.strip():
                continue
            try:
                self.memory.add_item(scope, "artifact", f"artifact: {a}", f"run:{run_id}")
            except ValueError:
                continue

    def _check_guard(self, work: dict, env: dict) -> None:
        """K-2: an incident fix's guard names a [checks.class] entry — the Chair authored the
        command; the fixer only names it. A guard carrying shell of its own is refused here: the
        heartbeat runs guards as its own user, which an employee's command must never become
        (2026-10-16 red team)."""
        g = env.get("guard")
        if not g or work.get("task_class") != "incident":
            return
        known = (self.config.get("checks") or {}).get("class") or {}
        if g.get("check") not in known:
            raise EnvelopeError([f"guard.check {g.get('check')!r} is not a [checks.class] entry "
                                 f"(have: {', '.join(sorted(known)) or 'none'}): a guard names a "
                                 "Chair-authored check, never a command of its own"])

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
        if applied:
            for name in {c["skill"] for c in changes}:
                pb = playbooks.read(self.skills, slug, name)
                if pb:
                    v = playbooks.bump(self.skills, slug, name, pb["version"])
                    with write_txn(self.wconn):
                        self.work._event("skill", f"@{slug}/{name}", f"v{pb['version']}", f"v{v}",
                                         f"playbook refined by {work['id']}", actor="kernel")
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

    def classes_for(self, dept_slug: str | None) -> list[str]:
        """The task classes a department's work may carry ([classes] org + <slug>); empty = any. A shared
        taxonomy is what lets track records accrue, so earned authority and sampling can engage.
        The rule lives in the organization, so a hire's own record is checked against the same
        names the brief offers."""
        return self.org.classes_for(dept_slug)

    def _check_org_refs(self, env: dict, work: dict | None = None) -> None:
        problems = []
        home_dept = self._dept_slug(work.get("dept_id")) if work else None

        def class_ok(task_class: str | None, slug: str | None, where: str) -> None:
            allowed = self.classes_for(slug)
            if task_class and allowed and task_class not in allowed:
                problems.append(f"{where}: task class {task_class!r} is not one of {slug or 'the organization'}'s "
                                f"{allowed}")

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
        class_ok(env.get("task_class"), env.get("dept") or home_dept, "task_class")
        for key in ("children", "actions"):
            for i, c in enumerate(env.get(key) or []):
                if c.get("dept"):
                    dept_ok(c["dept"], f"{key}[{i}]")
                class_ok(c.get("task_class"), c.get("dept") or home_dept, f"{key}[{i}].task_class")
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

    def kb_search(self, question: str, limit: int = 4) -> list[str]:
        """The other half of `recall`: `genome ask` over every knowledge base this principal may see,
        rendered as its `path:lines` hits.

        F3 (2026-10-21 reachability audit): the kernel joined memory, episodes, skills, playbooks and
        gold standards, and the law — INTENT, BYLAWS, CONTRACT, AGENTS — lived in the knowledge bases
        with no query that joined them, so a body was judged against rules it had no way to read.
        Every source is a read the kernel already owns; a missing or unseeded KB is silence, never an
        error, because recall must still answer from what it has.
        """
        chair = security.Principal("chair")
        try:
            entries = kb_mod.listing(self, chair)
        except Exception:   # no KBs, or a malformed registry: recall degrades, never dies
            return []
        out: list[str] = []
        for slug in sorted(entries):
            try:
                text = kb_mod.run(self, chair, slug, "ask", [question])
                hits = json.loads(text).get("hits") or []
            except Exception:
                continue
            for h in hits:
                path = h.get("path", "?")
                lines = f"{h.get('start')}-{h.get('end')}" if h.get("start") else "?"
                head = (h.get("heading") or "").split(" › ")[0][:80]
                out.append(f"kb/{slug} — {path}:{lines} § {head}")
                if len(out) >= limit:
                    return out
        return out

    # ── lanes: the cheapest way to do each job, chosen at intake and enforced (docs/BYLAWS.md §10) ──
    def _ask(self, why: str, state: str, questions: dict, patient: bool = False) -> dict | None:
        """A decision, or None. A failure (rate limit, outage) is recorded as an event and reaches the Chair:
        every caller then takes its safe default, which costs verifier sessions until the model is back."""
        a = self.decider.ask(state, questions, patient)
        if a is None and self.decider.on:
            with write_txn(self.wconn):
                self.work._event("classifier", "decider", None, "failed",
                                 f"decision model unavailable ({why}): {self.decider.error}"[:600], actor="kernel")
        return a

    def classify(self, title: str, intent: str, command: str | None = None) -> dict:
        """The decision model's view of a job: which lane it needs and whether it reaches outside the
        organization. `lane` is None when the model is off, failed, or is less sure than `min_confidence`."""
        a = self._ask("lane", f"Job: {title}\n\n{intent}" + (f"\n\nCommand: {command}" if command else ""), {
            "lane": choice("What is the cheapest way to do this job reliably?", LANE_OPTIONS),
            "external": noul("Does the job send, post or publish anything to people outside the organization: "
                             "clients, leads, recruiters, applications, emails or messages to third parties, public "
                             "posts or comments, payments, orders or trades? Reports, digests and notifications to "
                             "the Chair or the organization's own channels do not count.",
                             "it reaches third parties or the public",
                             "it only reads, writes the organization's files, or reports to the Chair")})
        if a is None:
            return {"lane": None, "confidence": None, "external_p": None, "source": "off" if not self.decider.on
                    else "failed", "error": self.decider.error}
        lane, conf = picked(a["lane"])
        return {"lane": lane if conf >= float(self.decider.cfg["min_confidence"]) else None,
                "model_lane": lane, "confidence": round(conf, 3), "probabilities": a["lane"].get("probabilities"),
                "external_p": round(p_yes(a["external"]), 3), "source": "classifier"}

    def _external(self, cls: dict) -> bool:
        return (cls.get("external_p") or 0) >= float(self.config["lanes"]["external_p_max"])

    @staticmethod
    def _per_day(cron: str) -> int:
        """How many times a cron fires in the next 24 hours, by walking its real times."""
        c = Cron(cron)
        t = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        end = t + timedelta(days=1)
        n = 0
        while True:
            t = c.next_after(t)
            if t > end:
                return n
            n += 1

    @classmethod
    def _fires_often(cls, cron: str, at_most: int = 11) -> bool:
        """A cron that fires more often than `at_most` times a day buys a body on every firing.

        The cheapest full-lane firing is one agent session. A */5 job is 288 of them a day; even an
        hourly job is 24. The threshold matches the boundary the cast importer in `adapters/`
        already recognises (it flags a job at `>= 12/day` and enforces nothing) — this is that
        flag with a decision attached. An unreadable cron is not a reason to refuse a routine,
        so a parse failure is False."""
        try:
            return cls._per_day(cron) > at_most
        except Exception:
            return False  # an unreadable cron is not a reason to refuse a routine

    def _external_by_name(self, title: str, intent: str) -> tuple[bool, str]:
        """Outside-reach, decided by the job's own words rather than by a model.

        The decision model is an external dependency: it is off by default, it rate-limits, and
        it fails. When it has no opinion, `external_p` is None and every job reads as internal —
        which for an IMPORT means the job lands enabled and scheduled. The verbs that reach
        people outside the organization are named in the job itself, so the kernel can decide
        without asking anyone. This is a floor, not a ceiling: the model can still say "outside"
        for a job whose words do not name a verb.
        """
        text = f"{title}\n{intent}".lower()
        hits = sorted({v for v in vocab.OUTSIDE_REACH_VERBS
                       if re.search(rf"\b{re.escape(v)}\b", text)})
        return bool(hits), ", ".join(hits)

    def intake_lane(self, title: str, intent: str, requested: str | None = None) -> tuple[str, dict]:
        """The lane for one-off work: the requested one, or the classifier's when it is sure. A job it judges
        deterministic still runs fast (one-off work has no approved command); one that reaches outside the
        organization runs full. The kernel re-checks the fast lane against the grade floor either way."""
        if requested in ("fast", "full"):
            return requested, {"source": "requested"}
        cls = self.classify(title, intent)
        lane = "full" if cls["lane"] in (None, "full") or self._external(cls) else "fast"
        return lane, cls

    def add_routine(self, title: str, intent: str, *, dept: str, cron: str | None = None, event: str | None = None,
                    lane: str | None = None,
                    command: str | None = None, rubric: list[str] | None = None, why: str | None = None,
                    workdir: str | None = None, task_class: str | None = None, risk: str = "normal",
                    project_ref: str | None = None, objective_id: str | None = None, strict: bool = True,
                    deliver: bool = False, imported: bool = False) -> dict:
        """A routine (a cron job, or an event trigger) as a proposal for the approver its grade names. The
        lane is enforced cheapest first: a job the classifier judges deterministic must be a script
        (`command`); a costlier lane than the classifier's needs `why`, which is recorded; a cheaper one
        is raised to the classifier's. A routine runs unattended, so its floor is G2 (G3 when it
        reaches outside the organization). `strict=False` (a planner's routine): a costlier lane than
        the classifier's is lowered to it instead.
        `event` (H7): 'event:<pattern>' — no cron; the runtime that sees a matching event fires it
        (`holon routine fire`), once per [events] cooldown_s; Holon never matches events itself."""
        if bool(cron) == bool(event):
            raise ValueError("a routine needs --cron or --on event:<pattern>, exactly one")
        if event and not event.startswith("event:"):
            raise ValueError("an event trigger is 'event:<pattern>': the runtime that sees the event matches it")
        if cron:
            Cron(cron)
        if lane not in (None, *vocab.LANES):
            raise ValueError(f"lane must be one of {vocab.LANES}")
        if command and lane not in (None, "script"):
            raise ValueError("--command makes a script routine; drop --lane or --command")
        if lane == "script" and not command:
            raise ValueError("the script lane runs an approved --command")
        cls = self.classify(title, intent, command)
        order = list(vocab.LANES)
        notes = []
        if command:
            lane = "script"
            if cls["lane"] and cls["lane"] != "script":
                notes.append(f"the classifier judged this a {cls['lane']} job ({cls['confidence']:.0%}); the rubric "
                             "judge and the verifier will tell whether the command is enough")
        elif cls["lane"] == "script" and not why:
            raise ValueError(f"this job looks deterministic (script, {cls['confidence']:.0%}): give it as a script "
                             "with --command (have the department write one: `holon task add`), or say --why an "
                             "agent must do it")
        elif lane is None:
            lane = cls["lane"] if cls["lane"] in ("fast", "full") else "full"
            if lane == "full" and cls["lane"] is None and cron and self._fires_often(cron):
                # No decision model has an opinion, and the cron fires often enough that a full
                # lane buys a body on every firing — 288 of them a day for a */5 job. Fail closed
                # the way the import already does for outside-reach: the kernel refuses to spend
                # that unattended, because nothing has said a body is needed.
                raise ValueError(f"no decision model is available and this routine fires on '{cron}' "
                                 f"({self._per_day(cron)} times a day): a full lane would dispatch a body "
                                 "on every firing. Configure [classifier], give it as --command, or say "
                                 "--why an agent must do it")
        elif cls["lane"] and order.index(lane) < order.index(cls["lane"]):
            notes.append(f"raised from {lane} to {cls['lane']}: the classifier is {cls['confidence']:.0%} sure it "
                         "needs that much care")
            lane = cls["lane"]
        elif cls["lane"] and order.index(lane) > order.index(cls["lane"]):
            if not why and not strict:
                notes.append(f"lowered from {lane} to {cls['lane']}: the classifier is {cls['confidence']:.0%} sure "
                             "that is enough")
                lane = cls["lane"]
            elif not why:
                raise ValueError(f"the classifier says {cls['lane']} is enough ({cls['confidence']:.0%}); a {lane} "
                                 "routine costs more on every firing: say --why")
        if why:
            notes.append(f"lane chosen by the Chair over the classifier: {why}")
        external = self._external(cls)
        # Outside-reach has two witnesses: the decision model, and the job's own words. The model
        # may be off, rate-limited or down — and then it has no opinion, which used to read as
        # "internal", which for an IMPORT meant the job landed enabled and scheduled. The words
        # are always available, so they are a floor the model cannot lower (I1).
        by_name, verbs = self._external_by_name(title, intent)
        if by_name and not external:
            external = True
            reasons_by_name = f"its own words name an outside-reach verb ({verbs}): floor G3"
        else:
            reasons_by_name = ""
        if imported and cls.get("external_p") is None and not by_name:
            # No opinion from anywhere and no verb named: the kernel cannot tell whether this job
            # reaches outside, so an import lands OFF. Fail closed: the Chair enables it per job
            # (`holon schedule enable`), which is a decision, not a default.
            unjudged = True
        else:
            unjudged = False
        floor = max(2, grading.RISK_FLOOR.get(risk, 1), 3 if external else 0)
        reasons = ["a routine runs unattended on a schedule: floor G2"]
        pilot = self.config.get("pilot", {}).get("depts") or []
        if external and pilot and dept in pilot:
            floor = max(floor, 4)   # I5: in a pilot department the Chair approves outside-reach, job by job
            reasons.append(f"pilot department '{dept}': outside-reach is the Chair's call (G4)")
        if external:
            if cls.get("external_p") is not None:
                reasons.append(f"the classifier judges it reaches outside the organization "
                               f"(p={cls['external_p']}): floor G3")
            if reasons_by_name:
                reasons.append(reasons_by_name)
        if unjudged:
            reasons.append("the decision model had no opinion and the job names no outside-reach verb: "
                           "an import lands OFF until the Chair enables it")
        if grading.RISK_FLOOR.get(risk, 1) > 2:
            reasons.append(f"risk={risk}: floor G{grading.RISK_FLOOR[risk]}")
        dept_id = self.org.dept(dept)["id"]
        approver = self.org.resolve_approver({"dept_id": dept_id, "task_class": task_class}, floor, None)
        when = (f"on `{cron}`" if cron else
                f"when a matching event fires it (`{event}`; the runtime that saw it calls `holon routine fire`, "
                "once per cooldown)")
        steps = {"script": f"The kernel runs `{command}` in {workdir or 'its run directory'} {when}; checks and "
                           "the rubric judge each result.",
                 "fast": f"{when[0].upper() + when[1:]}, one agent session does the job; the rubric judge checks "
                         "each result and a verifier sees the unsure ones.",
                 "full": f"{when[0].upper() + when[1:]}, a fresh piece of work is planned, approved, executed and verified."}[lane]
        return self.work.propose(
            title=f"Routine: {title}", intent=intent, steps=steps,
            apply={"kind": "routine", "cron": cron, "event": event,
                   # I1: an outside-reach import — or one the kernel could not judge — lands OFF;
                   # the Chair enables it per job after review. One bulk approval must never
                   # light an outreach fleet.
                   "starts_disabled": bool(imported and (floor >= 3 or unjudged))},
            approver=approver, grade=floor, task_class=task_class, dept=dept_id, author=self.actor,
            reasons=reasons + notes, body={"lane": lane, "rubric": list(rubric or []), "classification": cls,
                                           "playbook": playbooks.for_routine(title), "deliver": bool(deliver),
                                           **({"command": command} if command else {})},
            acceptance="; ".join(rubric) if rubric else None, workdir=workdir, risk=risk, project_ref=project_ref,
            objective_id=objective_id)

    def _apply_routine(self, a: dict) -> None:
        """On approval: the plan becomes a standing (recurring) plan, its schedule starts, and its playbook
        (protocol + rubric, version 1) is written into the department's skills, where refinement improves it."""
        self.wconn.execute("UPDATE tasks SET recurring=1 WHERE id=?", (a["_plan"],))
        sid = self.schedules.insert("recurring", a.get("cron") or "", template_id=a["_plan"], event=a.get("event"))
        if a.get("starts_disabled"):
            # I1: an outside-reach import (G3+) starts OFF; one bulk approval must never light an
            # outreach fleet. The Chair enables it per job after review (`holon schedule enable`).
            # Appliers run inside the approval's transaction — direct rows, like insert above.
            self.wconn.execute("UPDATE schedules SET enabled=0, next_run_at=NULL WHERE id=?", (sid,))
            self.work._event("schedule", sid, None, "disabled",
                             "outside-reach import: starts off, enabled per job by the Chair")
        plan = self.work.get(a["_plan"])
        src, body = self.work.get(plan["parent_id"]), self.work.output(a["_plan"])["body"]
        slug, name = self._dept_slug(src["dept_id"]), body.get("playbook")
        if slug and name and not playbooks.read(self.skills, slug, name):
            title = src["title"].removeprefix("Routine: ")
            rubric = body.get("rubric") or [src["acceptance"] or f"The result does what was asked: {src['intent'][:300]}",
                                            "The result says what was done and what was found, with evidence"]
            steps = [f"Run `{body['command']}` (the kernel does this; the protocol records why and what to check)"] \
                if body.get("command") else [src["intent"]]
            playbooks.write(self.skills, slug, name, playbooks.render(name, title, src["intent"], steps, rubric))
            self.work._event("skill", f"@{slug}/{name}", None, "changed", "playbook v1, with the routine's approval",
                             actor="kernel")

    def _routines_from_items(self, items: list[dict], approved_by: str | None, author: str) -> list[dict]:
        """Routines a planner (in an approved decomposition) or a retro proposed. The decomposition's
        decider also approves each routine when the routine's grade is theirs to decide; otherwise the
        routine waits for its approver. A job the classifier judges deterministic becomes work to write its
        script instead, since a planner cannot approve code that does not exist yet."""
        out = []
        for c in items:
            dept = self._dept_slug(c["dept"]) if str(c["dept"]).startswith("dept_") else c["dept"]
            try:
                a = self.add_routine(c["title"], c["intent"], dept=dept, cron=c["cron"], lane=c.get("lane"),
                                     command=c.get("command"), rubric=c.get("rubric"), workdir=c.get("workdir"),
                                     task_class=c.get("task_class"), risk=c.get("risk") or "normal",
                                     objective_id=c.get("objective_id"), strict=False)
            except ValueError as e:
                w = self.work.add_work(f"Make routine '{c['title']}' a script", f"{c['intent']}\n\n{e}. Write the "
                                       "script in the department's reach, then propose the routine with its command.",
                                       dept=dept, parent_id=c.get("objective_id"), source="agent", requested_by=author,
                                       task_class=c.get("task_class"))
                out.append({"script_work": w["id"], "title": c["title"]})
                continue
            if approved_by and approved_by in (CHAIR, a["approver"]):
                actor = self.work.actor
                self.work.actor = approved_by
                try:
                    self.decide(a["id"], "approve", f"approved with the decomposition by {approved_by}")
                finally:
                    self.work.actor = actor
                out.append({"routine": a["parent_id"], "title": c["title"], "approved": True})
            else:
                out.append({"routine": a["parent_id"], "title": c["title"], "approval": a["id"],
                            "approver": a["approver"]})
        return out

    def playbook_of(self, work: dict) -> dict | None:
        """The playbook a work follows: its routine's, else its task class's in its department."""
        slug = self._dept_slug(work.get("dept_id"))
        if not slug:
            return None
        plan = self.work.approved_plan(work["id"])
        name = ((plan or {}).get("output") or {"body": {}})["body"].get("playbook")
        if not name and (work.get("requested_by") or "").startswith("recurring:"):
            name = ((self.work.output(work["requested_by"][10:]) or {"body": {}})["body"]).get("playbook")
        if not name and work.get("task_class") and work["task_class"] not in ("objective", "playbook-refinement"):
            name = playbooks.for_class(work["task_class"])
        return playbooks.read(self.skills, slug, name) if name else None

    def routines(self) -> list[dict]:
        out = []
        for s in self.schedules.list():
            if s["kind"] != "recurring":
                continue
            plan = self.work.get(s["template_id"])
            body = (self.work.output(plan["id"]) or {"body": {}})["body"]
            runs = self.wconn.execute(
                "SELECT count(*), sum(status='done'), sum(status='failed') FROM tasks WHERE stage IS NULL AND "
                "requested_by=?", (f"recurring:{plan['id']}",)).fetchone()
            out.append({"schedule": s["id"], "enabled": bool(s["enabled"]), "cron": s["cron"], "plan": plan["id"],
                        "title": self.work.get(plan["parent_id"])["title"].removeprefix("Routine: "),
                        "lane": body.get("lane") or "full", "command": body.get("command"),
                        "rubric": body.get("rubric") or [], "next_run_at": s["next_run_at"],
                        "fired": runs[0], "done": runs[1] or 0, "failed": runs[2] or 0})
        return out

    def rubric(self, work: dict) -> list[str]:
        """What a result of this work is judged on: the routine's rubric, else its acceptance criteria."""
        plan = self.work.approved_plan(work["id"])
        r = ((plan or {}).get("output") or {"body": {}})["body"].get("rubric")
        return list(r) if r else [work["acceptance"] or f"The result does what was asked: {work['intent'][:400]}"]

    def judge(self, work: dict, env: dict, run: dict | None = None) -> dict | None:
        """The rubric judge: the decision model's probability that the result meets each criterion, and that
        it is as good as the gold standard. None when the model is off or failed. Each judgment row
        carries the build that ran the result (H10), so pass rates group by build."""
        pb = self.playbook_of(work)
        rubric = (pb["rubric"] if pb and pb["rubric"] else self.rubric(work))[:12]
        gold = self.work.gold_standard(work)
        qs = {f"c{i}": noul(f"Does the result meet this criterion: {c}", "the result shows it is met",
                            "it is not met, or the result does not show it") for i, c in enumerate(rubric)}
        if gold:
            qs["gold"] = noul("Is this result at least as complete and correct as the gold standard (the last "
                              "verified run of the same job)?", "as good or better",
                              "worse, or missing something the gold standard had")
        state = (f"Job: {work['title']}\n{work['intent']}\n\nResult ({env['outcome']}): {env['summary']}\n\n"
                 f"{env['documentation'][:12000]}")
        if gold:
            state += f"\n\n--- Gold standard ({gold['work_id']}) ---\n{gold['summary']}\n{(gold['documentation'] or '')[:6000]}"
            if gold.get("trajectory_text"):
                state += f"\n\nHow the gold standard got there (compacted trajectory):\n{gold['trajectory_text']}"
        a = self._ask("rubric", state, qs, patient=True)
        if a is None:
            return None
        scores = {("gold standard" if q == "gold" else rubric[int(q[1:])]): round(p_yes(a[q]), 3) for q in qs}
        bar = float(self.decider.cfg["pass_p"])
        w = self.work.get(work["id"])
        with write_txn(self.wconn):
            for c, p in scores.items():
                self.wconn.execute("INSERT INTO judgments (work_id, playbook, version, criterion, p, passed, build, created_at)"
                                    " VALUES (?,?,?,?,?,?,?,?)", (work["id"], w["playbook"] or (pb or {}).get("name"),
                                    w["playbook_version"] or (pb or {}).get("version"), c, p,
                                    int(p >= bar), (run or {}).get("harness_build"), now()))
        return {"pass": all(v >= bar for v in scores.values()), "scores": scores}

    def _lane_verification(self, work: dict, run: dict, env: dict) -> dict | None:
        """Script and fast work skip the verifier when nothing calls for one: graded again on what it did
        (beyond its approved grade: a lane breach, which the Chair is told about), then judged against its
        rubric. Unsure, failing, sampled or breaching results keep their verify stage — and so does a
        result with blockers (H-03): the skips never decide what it refused."""
        if work["status"] != "open" or env["outcome"] != "success" or not self.work.latest(work["id"], "verify", "ready") \
                or env.get("blockers"):
            return None
        approved = self.work.approved_plan(work["id"])
        s = self.scope(self._dept_slug(work["dept_id"]))
        g = grading.grade_plan(self.wconn, work, {"writes": env.get("writes") or [], "grade": "G0",
                                                  "external_effects": env.get("external_effects") or []},
                               self.config, reach=s["roots"], read_only_reach=s["read_roots"])
        if approved and g["floor"] > approved["grade"]:
            with write_txn(self.wconn):
                self.work._event("task", work["id"], None, None, f"lane breach: a {work['lane']} result graded "
                                 f"G{g['floor']} above its approved G{approved['grade']}: {'; '.join(g['reasons'])}"[:900],
                                 actor="kernel")
            return None
        verdict = self.judge(work, env, run)
        if verdict is None:
            if work["lane"] == "script" and not self.decider.on:
                return self.work.skip_verification(work["id"], "script exited 0 and its checks passed", by="checks")
            return self._sample_verification(work, run, env) if work["lane"] == "fast" else None
        scores = "; ".join(f"{c[:80]}: {p}" for c, p in verdict["scores"].items())
        pol = self.org.policy(work["dept_id"])
        sampled = work["lane"] == "fast" and autonomy.sampled(f"verify:{run['id']}", pol["verify_sample_rate"])
        with write_txn(self.wconn):
            self.work._event("task", work["id"], None, None,
                             f"rubric {'pass' if verdict['pass'] else 'unsure'}{' (sampled)' if sampled else ''}: "
                             + scores[:800], actor="kernel")
            if not verdict["pass"] or sampled:
                self.work._message("kernel", "Rubric judge (decision model, probability each criterion is met; "
                                   f"{'sampled for a verifier' if sampled else 'below the pass bar'}):\n"
                                   + "\n".join(f"- {c}: {p}" for c, p in verdict["scores"].items()),
                                   f"task:{work['id']}", kind="channel", task_id=work["id"])
        if verdict["pass"] and not sampled:
            return self.work.skip_verification(work["id"], f"rubric judge passed: {scores}"[:900], by="rubric")
        return None

    def run_script(self, stage_id: str | None = None) -> dict | None:
        """Run a ready script stage: the routine's approved command, in its workdir, by the kernel (no agent).
        Its exit code is the outcome; the result goes through checks and the rubric judge like any other."""
        token, token_hash = security.new_token()
        got = self.work.claim(Claimant(), ["execute"], "script", stage_id, token_hash, kernel=True)
        if got is None:
            return None
        node, run = got
        cmd = self.work.approved_plan(node["parent_id"])["output"]["body"]["command"]
        rdir = self.home.run_dir(run["id"])
        rdir.mkdir(parents=True, exist_ok=True)
        if self.isolation:
            isolation.share_run_dir(rdir, self.isolation["agent_user"])
        cwd = node["workdir"] if node.get("workdir") and Path(node["workdir"]).is_dir() else str(rdir)
        code, output = self._exec(cmd, cwd, {"HOLON_WORK_ID": node["parent_id"]},
                                  int(self.config["lanes"]["script_timeout_s"]))
        (rdir / "driver.log").write_text(f"$ {cmd}\n[cwd] {cwd}\n[exit] {code}\n{output}")
        self.work.set_run_paths(run["id"], str(rdir), str(rdir / "driver.log"))
        env = {"outcome": "success" if code == 0 else "failure", "summary": f"`{cmd}` exited {code}",
               "documentation": f"```\n{output[-8000:].strip() or '(no output)'}\n```"}
        return self.submit(run["id"], env, code)

    def route_dept(self, title: str, intent: str) -> dict:
        """Which department should own new work, from their missions. `dept` is None when the model is off or
        unsure: the work then goes to the executive, whose CEO shapes and routes it."""
        depts = [d for d in self.org.depts() if d["slug"] != EXECUTIVE]
        if len(depts) < 2:
            return {"dept": depts[0]["slug"] if depts else None, "source": "only one"}
        a = self._ask("routing", f"Work: {title}\n\n{intent}", {"dept": choice(
            "Which department should own this work?",
            {d["slug"]: f"{d['name']}: {d.get('mission') or ''}"[:300] for d in depts})})
        if a is None:
            return {"dept": None, "source": "off" if not self.decider.on else "failed", "error": self.decider.error}
        slug, conf = picked(a["dept"])
        return {"dept": slug if conf >= float(self.decider.cfg["min_confidence"]) else None, "model_dept": slug,
                "confidence": round(conf, 3), "source": "classifier"}

    def _failure_cause(self, task_id: str) -> str:
        """Failure triage for an incident: the decision model's reading of the last failed run's log."""
        r = self.wconn.execute("SELECT log_path FROM runs WHERE task_id=? AND status IN ('failed','timeout') AND "
                               "log_path IS NOT NULL ORDER BY started_at DESC LIMIT 1", (task_id,)).fetchone()
        try:
            tail = Path(r[0]).read_text(errors="replace")[-6000:] if r else ""
        except OSError:
            tail = ""
        a = self._ask("triage", f"Log of a failed agent run:\n{tail}", {"cause": choice(
            "What most likely made this run fail?", FAILURE_CAUSES)}) if tail else None
        if a is None:
            return ""
        cause, conf = picked(a["cause"])
        return f"\nLikely cause (decision model, {conf:.0%}): {cause}: {FAILURE_CAUSES.get(cause, '')}."

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

        def alive() -> bool:  # the harness is still working: keep its lease (a dead dispatcher stops renewing)
            try:
                self.work.heartbeat(claimed.run["id"])
                return True
            except TransitionError:  # the agent already submitted, or the run was swept: stop the harness
                return False
        out = drivers.run(self.home, drv, brief, claimed.token, alive=alive, isolation=self.isolation)
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
            post = self.post_execute(claimed.node["parent_id"]) if claimed.run["kind"] == "execute" else {}
            return {"stage": claimed.run["kind"], "submitted_by_agent": True, "breach": [breach] if changed else None,
                    **post, "work": self.work.work_of(claimed.node["id"])}
        if changed:
            return self._contract_breach(self.work.get_run(claimed.run["id"]), [breach], out, out["exit_code"])
        if out.get("envelope") is None:
            # The body produced no envelope. A harness that printed its answer but never wrote the
            # file — the observed failure is a body that reasons its way to `holon_submit` and then
            # keeps exploring until the timeout takes it — leaves the run with nothing at all, so
            # the stage is recycled with no record of what it tried. Record the stall: a kernel
            # event says so, surfaced to the Chair, and a guard test below keeps it from regressing.
            self.work._event("run", claimed.run["id"], None, None,
                            "no envelope: the body produced no result (kept exploring until its "
                            "budget ran out); its output is in driver.log",
                            actor=f"emp:{claimed.employee['id']}" if claimed.employee else None)
        return self.submit(claimed.run["id"], out["envelope"], out["exit_code"], driver_output=out)

    def export_department(self, dept: str, dest: str) -> dict:
        """G12: write one department's archive (see execution/portability.py)."""
        from holon.execution import portability
        d = self.org.dept(dept)
        return portability.export_department(self, d["id"], dest)

    def import_department(self, archive: str) -> dict:
        """G12: create a department from an archive (see execution/portability.py)."""
        from holon.execution import portability
        return portability.import_department(self, archive)

    def intake_file(self, key: str, title: str, intent: str, *, dept: str | None = None,
                    task_class: str | None = None, risk: str = "normal",
                    acceptance: str | None = None, priority: int = 3) -> dict:
        """File work with an **intake key** — G4, the Chair-key-free external intake.

        A scanner in a venture pool has no Holon identity, so before this every lead it
        found stopped at the Chair. This is the credential that ends that stop, and it is
        scoped three ways so it is never a second Chair key:

        1. **One department.** The key names its own; filing anywhere else is refused,
           and the refusal names the department it tried.
        2. **A grade floor the Chair sets per department.** A key at G1 cannot file an
           irreversible job (G4) or anything the floor puts above it.
        3. **The task classes the key may file**, so a key for `lead` does not file
           `objective`.

        The filed work is a normal root node: it enters the work graph, is planned,
        approved, executed and verified like anything else. The key only *files*; it
        never approves and never decides.
        """
        p = self.auth.intake_principal(key)
        target = dept or p.dept_id
        if not target:
            raise PermissionError("an intake key must name the department it files into")
        if target != p.dept_id:
            raise PermissionError(f"an intake key for {p.dept_id} cannot file into {target}")
        if p.task_classes and task_class and task_class not in p.task_classes:
            raise PermissionError(f"an intake key for {p.dept_id} may not file task class "
                                   f"{task_class!r} (it may file: {', '.join(p.task_classes)})")
        # the floor: the Chair's per-department limit, and the risk's own floor
        risk_floor = grading.RISK_FLOOR.get(risk, 1)
        limit = grading.parse_grade(p.floor) if isinstance(p.floor, str) else 1
        if risk_floor > limit:
            raise PermissionError(f"an intake key for {p.dept_id} has floor {p.floor}; "
                                   f"this job is {risk} (floor G{risk_floor})")
        d = self.org.dept(target)
        work = self.work.add_work(title, intent, dept=d["id"], task_class=task_class, risk=risk,
                                   acceptance=acceptance, priority=priority, source="intake",
                                   requested_by=p.actor, expand=True)
        return work

    def chair_channels(self) -> set[str]:
        """The Chair's channel identities (H8): '<platform>:<user id>' refs, e.g. 'telegram:123456'.
        A runtime that fronts a channel (a gateway) may run Chair commands only for a message from
        one of these identities; the broker allowlist still forbids every Chair command, so an agent
        cannot reach that path."""
        return set(self.config.get("chair", {}).get("channels") or [])

    def prepare_dispatch(self, employee_id: str, stage: str | None = None, task_id: str | None = None) -> dict | None:
        """H6: claim a stage and hand an external supervisor everything it needs to run the body
        itself — the driver's rendered command, the run's environment, the brief, policy.json and the
        run token — without spawning anything. The supervisor hands the finished body back with
        `finish_run` (CONTRACT §12). The claim, lease and concurrency rules are exactly dispatch's."""
        emp = self.org.employee(employee_id)
        claimed = self.claim(stage, employee_id, emp["driver"], task_id, max_running=emp["max_concurrency"])
        if claimed is None:
            return None
        drv = drivers.get(self.home, emp["driver"])
        brief = briefs.load(self.home, claimed.run["id"])
        cmd, cwd = drivers.render(drv, brief)
        env = {**drivers.run_env(self.home, brief, claimed.token,
                                 self.isolation.get("broker") if self.isolation else None), **drv.env}
        return {"run": claimed.run["id"], "stage": brief["stage"], "task": brief["task_id"],
                "employee": employee_id, "driver": drv.name, "command": cmd, "cwd": cwd,
                "env": env, "timeout_s": drv.timeout_s, "brief_md": brief["brief_md"],
                "result_path": brief["result_path"], "policy_json": str(claimed.brief_dir / "policy.json"),
                "lease_until": claimed.run["lease_until"],
                **({"isolation": {"agent_user": self.isolation["agent_user"],
                                   "runner": self.isolation.get("runner")}} if self.isolation else {})}

    def finish_run(self, run_id: str, exit_code: int = 0) -> dict:
        """H6: a supervisor hands a finished body in. This process is the kernel's short Python
        process: it salvages result.json (a missing or invalid result is a contract breach, exactly
        as in push mode), submits it, and runs what follows — machine checks, then the judge."""
        run = self.work.get_run(run_id)
        if run["status"] != "running":
            raise TransitionError(f"run {run_id} is '{run['status']}' (nothing to finish)")
        return self.submit(run_id, None, exit_code)

    def dispatch(self, employee_id: str, stage: str | None = None, task_id: str | None = None) -> dict | None:
        emp = self.org.employee(employee_id)
        # checked inside the claim's transaction: two wakes due in one tick must not both start
        claimed = self.claim(stage, employee_id, emp["driver"], task_id, max_running=emp["max_concurrency"])
        if claimed is None:
            return None
        return self.run_driver(claimed, emp["driver"])

    def tick(self, foreground: bool = False) -> dict:
        """Idempotent heartbeat; call it from any timer (systemd, cron, a harness scheduler)."""
        salvaged = self._salvage()
        swept = self.work.sweep_leases()
        triggered = self.triggers(swept)
        notified = self.notify()
        delivered = self._deliver()
        fired = []
        for s in self.schedules.take_due():
            if s["kind"] == "recurring":
                fired.append(self._fire_recurring(s))
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
        for wid in self.held_checks():  # results an agent handed in itself, whose dispatcher did not finish them
            if foreground:
                fired.append({"post_execute": wid, **{k: v for k, v in self.post_execute(wid).items() if k != "work"}})
            else:
                self._spawn([sys.executable, "-m", "holon", "--home", str(self.home.root), "post-execute", wid])
                fired.append({"post_execute": wid, "spawned": True})
        for (sid,) in self.wconn.execute("SELECT id FROM tasks WHERE status='ready' AND stage='execute' AND "
                                         "lane='script' ORDER BY priority, created_at").fetchall():
            if sid in self.backed_off():
                continue
            if foreground:
                res = self.run_script(sid)
                fired.append({"script": sid, "work": (res or {}).get("work", {}).get("status")})
            else:
                self._spawn([sys.executable, "-m", "holon", "--home", str(self.home.root), "run-script", sid])
                fired.append({"script": sid, "spawned": True})
        # The heartbeat's liveness stamp: the last successful tick's unix seconds, at
        # .clock/last_tick — the file the gateway's clock organ and the kernel's own
        # timer both write, and the doctor's heartbeat.stale check reads. Best-effort by
        # contract (organs.rs): a tick that cannot write it still did its work.
        try:
            clock_dir = self.home.root / ".clock"
            clock_dir.mkdir(exist_ok=True)
            (clock_dir / "last_tick").write_text(str(int(datetime.now(timezone.utc).timestamp())))
        except OSError:
            pass
        return {"swept": swept, "triggered": triggered, "fired": fired, "notified": notified, "delivered": delivered,
                "salvaged": salvaged}

    def _salvage(self) -> list[str]:
        """A run whose lease expired but whose agent wrote its result (its dispatcher died after the harness
        finished): the result is handed in instead of counting the run as failed and doing the work again."""
        out = []
        for (rid,) in self.wconn.execute("SELECT id FROM runs WHERE status='running' AND lease_until<?",
                                         (now(),)).fetchall():
            try:
                if isolation.read_agent_file(self.home.run_dir(rid) / "result.json") is None:
                    continue
                self.submit(rid, defer=True)
            except (isolation.IsolationError, TransitionError, ValueError):
                continue
            with write_txn(self.wconn):
                self.work._event("run", rid, None, None, "salvaged: its result was on disk when its lease expired",
                                 actor="kernel")
            out.append(rid)
        return out

    def fire_routine(self, schedule_id: str, payload: str | None = None) -> dict:
        """H7: fire an event routine once per cooldown. The runtime that saw a matching event calls
        this (a gateway, a webhook receiver — `holon routine fire`); Holon never matches events
        itself. The payload rides on the firing's work as a message, where the executor's brief shows
        it. Cooldown first, then the same skip-if-open guard a cron firing has."""
        s = self.schedules.get(schedule_id)
        if not s["event"]:
            raise TransitionError(f"schedule {schedule_id} has no event; a cron schedule fires on its clock")
        if not s["enabled"]:
            return {"schedule": schedule_id, "skipped": "disabled"}
        if s["next_run_at"] and s["next_run_at"] > now():
            return {"schedule": schedule_id, "skipped": f"cooldown until {s['next_run_at']}"}
        self.schedules.arm_cooldown(schedule_id, int(self.config.get("events", {}).get("cooldown_s", 300)))
        out = self._fire_recurring(s)
        wid = out.get("recurring_work")
        if wid and payload:
            self.work.post_message("event", f"work:{wid}", f"{s['event']}\n{payload}", task_id=wid)
        return out

    def _fire_recurring(self, s: dict) -> dict:
        """A fresh instance of the recurring plan, unless its work was cancelled (the schedule is turned off)
        or the previous instance is still open (this firing is skipped, so slow work never piles up)."""
        plan = self.work.get(s["template_id"])
        source = self.work.get(plan["parent_id"])
        if source["status"] == "cancelled":
            self.schedules.set_enabled(s["id"], False)
            return {"schedule": s["id"], "disabled": f"its work {source['id']} was cancelled"}
        prev = self.wconn.execute(
            "SELECT id FROM tasks WHERE stage IS NULL AND requested_by=? AND status NOT IN "
            f"({','.join('?' * len(vocab.TERMINAL))})", (f"recurring:{plan['id']}", *vocab.TERMINAL)).fetchone()
        if prev:
            with write_txn(self.wconn):
                self.work._event("schedule", s["id"], None, None, f"skipped: {prev[0]} from the last firing is open",
                                 actor="kernel")
            return {"schedule": s["id"], "skipped": prev[0]}
        return {"schedule": s["id"], "recurring_work": self.work.instantiate_recurring(s["template_id"])["id"]}

    def _spawn_dispatch(self, employee_id: str, stage: str | None, schedule_id: str) -> None:
        self._spawn([sys.executable, "-m", "holon", "--home", str(self.home.root), "dispatch", "--as", employee_id,
                     "--schedule", schedule_id] + (["--stage", stage] if stage else []))

    def _spawn(self, cmd: list[str]) -> None:
        log = self.home.runtime_dir / "dispatch.log"
        with open(log, "a") as fh:
            subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                             env={**os.environ, **kernel_env(self.home)})

    # ── machine checks: acceptance a command can decide, run by the kernel, not by an agent ──
    def checks_for(self, work: dict) -> list[str]:
        c = self.config.get("checks", {})
        slug = self._dept_slug(work.get("dept_id"))
        return [*c.get("commands", []), *((c.get("dept") or {}).get(slug, {}).get("commands", []) if slug else []),
                *((c.get("class") or {}).get(work.get("task_class") or "", {}).get("commands", []))]

    def _check_env(self) -> dict:
        """The kernel's environment for a command it runs itself: no Chair, employee or run credential."""
        env = {k: v for k, v in os.environ.items() if k not in ("HOLON_CHAIR_KEY", "HOLON_KEY", "HOLON_RUN_TOKEN")}
        return {**env, "HOLON_HOME": str(self.home.root)}

    def run_checks(self, work: dict) -> list[dict]:
        """Run the work's checks in its workdir. Every result is posted on the work (the verifier reads it
        in its context) and logged as an event; the caller turns a failure into rework."""
        cmds = self.checks_for(work)
        workdir = work.get("workdir")
        if not cmds or not workdir or not Path(workdir).is_dir():
            return []
        results = []
        for cmd in cmds:
            code, tail = self._exec(cmd, workdir, {"HOLON_WORK_ID": work["id"]}, int(self.config["checks"]["timeout_s"]))
            tail = tail[-1500:]
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
        """The effective reach for a department: [scope] plus [scope.dept.<slug>], plus what every
        pooled unit grants (U2). A pooled unit's roots join `read_roots` — presence and reads, never
        the pen (BYLAWS §14) — and, when the department already has a terminal fence (`commands` or
        `act_commands`, whichever exists), its services' entry-point binaries join that fence. `name` is the slug when the department has its own
        section, else "org"; harness adapters build one sandbox per name."""
        base = self.config.get("scope", {})
        own = (base.get("dept") or {}).get(dept_slug) if dept_slug else None
        merged = {k: [*base.get(k, []), *((own or {}).get(k, []))] for k in ("roots", "commands", "act_commands")}
        pooled = units.pooled_by(self.wconn, dept_slug) if dept_slug else []
        merged["read_roots"] = [r for u in pooled for r in u["roots"]]
        binaries = [s["command"].split()[0] for u in pooled for s in u["services"] if s.get("command")]
        for key in ("commands", "act_commands"):
            # only a fence that exists: an empty list means ungoverned, and deriving one out of
            # pooling would refuse every other command the department runs (R-05) — and the act
            # fence is a fence too, so an act_commands-only department gets the binaries (red-team)
            if merged[key]:
                merged[key] = list(dict.fromkeys(merged[key] + binaries))
        merged["roots"] = [str(Path(r).expanduser()) for r in merged["roots"]]
        merged["read_roots"] = [str(Path(r).expanduser()) for r in merged["read_roots"]]
        return {"name": dept_slug if own is not None else "org", **merged}

    def scopes(self) -> list[dict]:
        """Every distinct scope: "org" first, then one per department with a [scope.dept.<slug>] section."""
        return [self.scope(None)] + [self.scope(s) for s in sorted((self.config.get("scope", {}).get("dept") or {}))]

    def scope_fingerprint(self) -> str:
        import hashlib
        return hashlib.sha256(json.dumps(self.scopes(), sort_keys=True).encode()).hexdigest()[:12]

    # ── smoke: the infrastructure a department runs on, checked before its agents run ──
    def smoke_checks(self, dept: str | None = None) -> list[dict]:
        """Every declared smoke check: {dept (None = everyone), name, cmd, critical, timeout, reason}."""
        sm = self.config.get("smoke", {})
        found = [(None, c) for c in sm.get("checks") or []]
        for slug, sub in (sm.get("dept") or {}).items():
            if dept and slug != dept:
                continue
            found += [(slug, c) for c in sub.get("checks") or []]
            if sub.get("file"):
                f = Path(sub["file"]).expanduser()
                try:
                    found += [(slug, c) for c in tomllib.loads(f.read_text()).get("checks") or []]
                except (OSError, tomllib.TOMLDecodeError) as e:
                    found.append((slug, {"name": f"smoke file {f}", "cmd": f"echo {shlex.quote(str(e))}; false",
                                         "critical": True, "reason": "the department's smoke manifest must load"}))
        return [{"dept": d, "name": c.get("name") or c.get("cmd", "?"), "cmd": c.get("cmd", "false"),
                 "critical": bool(c.get("critical")), "timeout": int(c.get("timeout") or sm.get("timeout_s") or 60),
                 "reason": c.get("reason", "")} for d, c in found]

    def run_smoke(self, dept: str | None = None) -> list[dict]:
        """Run the smoke checks (as the agent user under isolation: they exercise what agents use), in the
        department's first scope root (else the instance), and remember the results for the gate."""
        results = []
        for c in self.smoke_checks(dept):
            # writable scope roots only: a department whose only reach is pooled has no root it may
            # write smoke artifacts into, so its checks run in the instance root (the explicit
            # decision R-06 asks for — a pooled root is read-only, never a smoke cwd)
            roots = self.scope(c["dept"])["roots"] if c["dept"] else []
            cwd = next((r for r in roots if Path(r).is_dir()), str(self.home.root))
            started = datetime.now(timezone.utc)
            code, out = self._exec(c["cmd"], cwd, {}, c["timeout"])
            results.append({**c, "ok": code == 0, "exit_code": code, "tail": out.strip()[-600:],
                            "seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 2)})
        if not dept:
            with write_txn(self.wconn):
                self.wconn.execute("INSERT OR REPLACE INTO settings VALUES ('smoke', ?)",
                                   (json.dumps({"ts": now(), "results": results}),))
        return results

    def smoke_results(self, max_age_min: float | None = None) -> list[dict]:
        """The last smoke results; re-run when older than `max_age_min` (default [smoke] every_min)."""
        raw = self._setting("smoke")
        cached = json.loads(raw) if raw else None
        age = float(self.config["smoke"]["every_min"] if max_age_min is None else max_age_min)
        if cached and cached["ts"] > (datetime.now(timezone.utc) - timedelta(minutes=age)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"):
            return cached["results"]
        return self.run_smoke() if self.smoke_checks() else []

    def smoke_blocked(self) -> list[str]:
        """Departments whose agents wait: a critical check of theirs, or one for everyone, failed last time.
        The incident department is never blocked, so it can fix what broke. Uses the cache only: the gate
        never runs commands inside a claim."""
        raw = self._setting("smoke")
        failed = [r for r in (json.loads(raw)["results"] if raw else []) if r["critical"] and not r["ok"]]
        if not failed:
            return []
        everyone = any(r["dept"] is None for r in failed)
        slugs = {r["dept"] for r in failed if r["dept"]}
        spare = self.config["healing"]["incident_dept"]
        return [d["id"] for d in self.org.depts() if d["slug"] != spare and (everyone or d["slug"] in slugs)]

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
                "SELECT e.seq, e.note, t.id, t.stage, t.status, t.title, t.task_class, t.approver, t.blocked_reason "
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
            elif e["stage"] is None and (e["note"] or "").startswith("lane breach"):
                lines.append(f"{e['note'][:200]} on {e['title']} (holon task show {e['id']})")
        down = self.wconn.execute("SELECT note FROM events WHERE entity='classifier' AND to_status='failed' AND seq>? "
                                  "ORDER BY seq DESC LIMIT 1", (since_seq,)).fetchone()
        if down:
            lines.append(f"{down[0][:240]}; results go to verifiers meanwhile")
        n = self.wconn.execute("SELECT count(*) FROM audit_samples WHERE verdict IS NULL AND sampled_at>?",
                               (self._setting("notify_ts") or "",)).fetchone()[0]
        if n:
            lines.append(f"{n} delegated approvals sampled for your review (holon audit samples)")
        lines.extend(self._pending_messages())
        return list(dict.fromkeys(lines))

    def _pending_messages(self) -> list[str]:
        """Messages addressed to the Chair that the Chair has not read yet.

        The digest read `events JOIN tasks` and nothing else, so `msg send` and `msg request`
        landed in `messages` and the Chair never learned of them: a body blocked on which key to
        use had no path to its owner at all, and nothing reported the gap. `ack_required` and
        `acked_at` already exist on the table, so 'unread' is that column rather than a new one.

        Only the kinds that ask something of the Chair are owed: a `question` needs its answer, a
        `request` needs its decision. A `dm` addressed to the Chair is a human writing to a human,
        not work the kernel should chase, and `proposal` and `answer` are department-internal."""
        out = []
        for m in self.wconn.execute(
                "SELECT id, sender, kind, body FROM messages WHERE to_ref=? AND acked_at IS NULL "
                "AND kind IN ('question','request') ORDER BY ts", (vocab.CHAIR,)).fetchall():
            what = (m["body"] or "").strip().replace("\n", " ")
            out.append(f"{m['kind']} from {m['sender']}: {what[:160]} (holon msg thread {m['id']})")
        return out

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
            # the cursor moves past what was seen only when nothing is owed; a delivered message moves it below
            if not lines:
                self.wconn.execute("INSERT OR REPLACE INTO settings VALUES ('notify_seq', ?)", (str(top),))
            self.wconn.execute("INSERT OR REPLACE INTO settings VALUES ('notify_ts', ?)", (now(),))
        if not lines:
            return None
        message = "Holon needs you:\n" + "\n".join(f"- {line}" for line in lines[:20])
        import shlex
        dropped = (self.config.get("notify") or {}).get("dropped_marker")
        try:
            p = subprocess.run(command.replace("{message}", shlex.quote(message)), shell=True, capture_output=True,
                               text=True, timeout=60)
            overlap = (p.stderr or "") + (p.stdout or "")
            # exit 0 is the only proof of delivery the contract gives the kernel, but it is not
            # enough on its own: a command that considers "nothing was configured" a successful
            # no-op exits 0 having delivered nothing. The instance's own `dropped_marker` names
            # that text. Without this check the cursor advances, `notify: sent` is recorded, the
            # digest is never retried, and the Chair never learns what the organization needs.
            ok, detail = p.returncode == 0 and not (dropped and dropped in overlap), overlap[-300:]
        except (OSError, subprocess.TimeoutExpired) as e:
            ok, detail = False, str(e)
        ack = []
        with write_txn(self.wconn):
            if ok:  # OS-5: a failed send leaves its items owed, and the next heartbeat retries them
                self.wconn.execute("INSERT OR REPLACE INTO settings VALUES ('notify_seq', ?)", (str(top),))
                # a question the Chair has been told is a question the Chair has read: without this
                # the same line is re-sent on every heartbeat until something else advances the
                # cursor, which is how a digest becomes noise nobody opens. The ids are collected
                # inside this transaction and acked after it: `Work.ack` opens its own.
                ack = [m[0] for m in self.wconn.execute(
                    "SELECT id FROM messages WHERE to_ref=? AND acked_at IS NULL AND kind IN ('question','request')",
                    (vocab.CHAIR,)).fetchall()]
            self.work._event("notify", "chair", None, "sent" if ok else "failed",
                             f"{len(lines)} items" + ("" if ok else f": {detail}"), actor="kernel")
        if ack:
            self.work.ack(ack)
        return {"items": len(lines), "sent": ok}

    def _deliver(self) -> list[str]:
        """Routines approved with `deliver`: each finished firing's result goes to the Chair through [notify]
        (the legacy crons' digests to a chat thread). A delivery that fails is retried on the next heartbeat."""
        command = (self.config.get("notify") or {}).get("command")
        if not command:
            return []
        since = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        out = []
        for w in self.wconn.execute(
                "SELECT * FROM tasks WHERE stage IS NULL AND status IN ('done','failed') AND requested_by LIKE "
                "'recurring:%' AND updated_at>? AND id NOT IN (SELECT entity_id FROM events WHERE entity='delivery' "
                "AND to_status='sent') ORDER BY updated_at", (since,)).fetchall():
            body = (self.work.output(w["requested_by"][10:]) or {"body": {}})["body"]
            if not body.get("deliver"):
                continue
            ex = self.work.last_execute_output(w["id"]) or {}
            text = f"{w['title']} [{w['status']}]\n{ex.get('summary') or ''}\n\n{(ex.get('documentation') or '')[:3000]}"
            try:
                p = subprocess.run(command.replace("{message}", shlex.quote(text)), shell=True, capture_output=True,
                                   text=True, timeout=60)
                ok = p.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            with write_txn(self.wconn):
                self.work._event("delivery", w["id"], None, "sent" if ok else "failed", None, actor="kernel")
            if ok:
                out.append(w["id"])
        return out

    # ── feedback loops (R5): sensors that post work when something needs attention ──
    def triggers(self, swept: list[dict] | None = None) -> list[dict]:
        out = []
        out += self._incidents(swept or [])
        out += self._build_regression()
        out += self._guard_triggers()
        out += self._probation_reviews()
        out += self._department_retros()
        out += self._doctor()
        out += self._backup()
        out += self._playbook_refinements()
        out += self._objective_reviews()
        return out

    # ── the playbook loop: protocols and rubrics improve from their own evidence ──
    def playbook_evidence(self, name: str, dept_id: str, version: int, since: str = "") -> dict:
        """What the runs under one version of a playbook show: runs, verifier failures, rubric passes a
        verifier then failed (disagreements), and each criterion's mean probability of being met."""
        runs = [dict(r) for r in self.wconn.execute(
            "SELECT * FROM tasks WHERE stage IS NULL AND playbook=? AND playbook_version=? AND dept_id=? AND "
            "created_at>? ORDER BY created_at", (name, version, dept_id, since))]
        ids = [r["id"] for r in runs]
        marks = ",".join("?" * len(ids)) or "''"
        fails = [dict(r) for r in self.wconn.execute(
            f"SELECT v.parent_id, o.body FROM tasks v JOIN outputs o ON o.task_id=v.id WHERE v.stage='verify' AND "
            f"v.outcome='fail' AND v.parent_id IN ({marks})", ids)]
        judged_pass = {r[0] for r in self.wconn.execute(
            f"SELECT entity_id FROM events WHERE entity='task' AND note LIKE 'rubric pass%' AND entity_id IN ({marks})",
            ids)}
        criteria = [dict(r) for r in self.wconn.execute(
            "SELECT criterion, avg(p) AS mean, count(*) AS n, sum(passed) AS passed FROM judgments WHERE playbook=? "
            "AND version=? AND created_at>? GROUP BY criterion ORDER BY mean", (name, version, since))]
        learned, notes = [], []
        for r in runs:  # what the runs themselves taught: executors' learnings and proposals, verifiers' notes
            ex = self.work.last_execute_output(r["id"])
            body = (ex or {}).get("body") or {}
            for x in (body.get("learnings") or []) + (body.get("proposals") or []):
                text = x if isinstance(x, str) else x.get("content") or f"{x.get('title', '')}: {x.get('intent', '')}"
                learned.append(f"{r['id']}: {text}")
            for s in self.work.stages(r["id"], "verify", "done"):
                o = self.work.output(s["id"])
                if o:
                    notes.append(f"{r['id']} ({s['outcome']}): {o['body'].get('note', '')}")
        gold = self.work.gold_standard(runs[-1]) if runs else None
        return {"runs": len(runs), "done": sum(r["status"] == "done" for r in runs), "learned": learned,
                "verifier_notes": notes, "gold": gold,
                "failed": sum(r["status"] == "failed" for r in runs),
                "verify_fails": [{"work": f["parent_id"], "note": json.loads(f["body"]).get("note", "")} for f in fails],
                "disagreements": sorted(judged_pass & {f["parent_id"] for f in fails}), "criteria": criteria,
                "last": runs[-1] if runs else None}

    def _playbook_refinements(self) -> list[dict]:
        """Each playbook in use gets refinement work when its evidence calls for it: a verifier failed a
        run, the judge and a verifier disagreed, a criterion keeps scoring below the bar, or enough runs
        have accumulated to consolidate. A task class with enough verified results and no playbook gets one
        written. The work goes through the full pipeline; the change lands only once verified."""
        c, bar = self.config["playbooks"], float(self.decider.cfg["pass_p"])
        out = []
        for name, dept_id in self.wconn.execute(
                "SELECT DISTINCT playbook, dept_id FROM tasks WHERE stage IS NULL AND playbook IS NOT NULL").fetchall():
            slug = self._dept_slug(dept_id)
            pb = playbooks.read(self.skills, slug, name) if slug else None
            if not pb:
                continue
            title = f"Refine playbook: {name}"
            last = self.wconn.execute("SELECT max(created_at) FROM tasks WHERE stage IS NULL AND title=?",
                                      (title,)).fetchone()[0] or ""
            if self._incident_open(title):
                continue
            ev = self.playbook_evidence(name, dept_id, pb["version"], last)
            why = []
            if ev["verify_fails"]:
                why.append(f"{len(ev['verify_fails'])} run(s) failed verification")
            if ev["disagreements"]:
                why.append(f"the rubric judge passed {len(ev['disagreements'])} run(s) a verifier failed: the rubric "
                           "misses what matters")
            weak = [x for x in ev["criteria"] if x["n"] >= int(c["min_judged"]) and x["mean"] < bar]
            if weak:
                why.append(f"{len(weak)} criterion(s) average below {bar}")
            if ev["runs"] >= int(c["refine_after"]):
                why.append(f"{ev['runs']} runs on v{pb['version']} to consolidate")
            if why:
                out.append(self._refinement(title, name, slug, pb, ev, why))
        for slug, task_class, n in self._unwritten_class_playbooks():
            out.append(self._refinement(f"Write playbook: {playbooks.for_class(task_class)}",
                                        playbooks.for_class(task_class), slug, None,
                                        {"task_class": task_class, "verified": n},
                                        [f"{n} verified results in class {task_class} and no playbook yet"]))
        return [o for o in out if o]

    def _unwritten_class_playbooks(self) -> list[tuple[str, str, int]]:
        out = []
        for dept_id, task_class, n in self.wconn.execute(
                "SELECT w.dept_id, w.task_class, count(DISTINCT w.id) FROM outputs o JOIN tasks s ON s.id=o.task_id "
                "JOIN tasks w ON w.id=s.parent_id WHERE o.kind='execute' AND o.verified=1 AND w.dept_id IS NOT NULL "
                "AND w.task_class IS NOT NULL AND w.task_class NOT IN ('objective','playbook-refinement','incident',"
                "'retro') AND w.playbook IS NULL GROUP BY w.dept_id, w.task_class HAVING count(DISTINCT w.id)>=?",
                (int(self.config["playbooks"]["class_after"]),)).fetchall():
            slug = self._dept_slug(dept_id)
            if not playbooks.read(self.skills, slug, playbooks.for_class(task_class)) and not self.wconn.execute(
                    "SELECT 1 FROM tasks WHERE stage IS NULL AND title=?",
                    (f"Write playbook: {playbooks.for_class(task_class)}",)).fetchone():
                out.append((slug, task_class, n))
        return out

    def _refinement(self, title: str, name: str, slug: str, pb: dict | None, ev: dict, why: list[str]) -> dict:
        if pb:
            crit = "\n".join(f"- {x['criterion']}: mean p={x['mean']:.2f} over {x['n']} ({x['passed']} passed)"
                             for x in ev["criteria"]) or "- (no judgments yet)"
            fails = "\n".join(f"- {f['work']}: {f['note'][:400]}" for f in ev["verify_fails"][:5]) or "- none"
            learned = "\n".join(f"- {x[:400]}" for x in ev["learned"][-12:]) or "- none recorded"
            vnotes = "\n".join(f"- {x[:600]}" for x in ev["verifier_notes"][-6:]) or "- none"
            gold = (f"{ev['gold']['work_id']}: {(ev['gold']['summary'] or '')[:600]}" if ev.get("gold") else "none yet")
            body = (f"The playbook `{name}` (v{pb['version']}) needs refining: {'; '.join(why)}.\n\n"
                    f"Runs on v{pb['version']}: {ev['runs']} ({ev['done']} done, {ev['failed']} failed).\n\n"
                    f"Rubric judgments per criterion:\n{crit}\n\nVerifier failures:\n{fails}\n\n"
                    f"What the runs learned and proposed (executors):\n{learned}\n\n"
                    f"What verifiers checked (their notes, passes included):\n{vnotes}\n\n"
                    f"Gold standard (the last verified result): {gold}\n\n"
                    f"Current protocol (`holon skill show {name} --dept {slug}`):\n\n{pb['protocol'][:3000]}\n\n"
                    f"Current rubric:\n" + "\n".join(f"- {c}" for c in pb["rubric"]))
        else:
            body = (f"Task class `{ev['task_class']}` has {ev['verified']} verified results in this department and no "
                    f"playbook. Distil one from them (their results and verifier notes: `holon task list --dept "
                    f"{slug}`): the objective, the protocol that worked, and a rubric of checkable criteria.")
        intent = (body + f"\n\nPlan the change as writes to `skill:{name}/SKILL.md` and `skill:{name}/rubric.md` "
                  "(a playbook skill: frontmatter `kind: playbook`, sections `## Objective`, `## Protocol`, "
                  "`## Lessons`; rubric lines start with `- `). Tie every change to the evidence. Sharpen criteria "
                  "that pass when they should not; retire ones that measure nothing; add what verifiers keep "
                  "catching. The kernel bumps the version when the change is verified, and later runs are judged "
                  "against it, so the next refinement can see whether this one helped.")
        w = self.work.add_work(title, intent[:20000], dept=slug, source="kernel", requested_by="kernel", priority=2,
                               task_class="playbook-refinement",
                               acceptance=f"skill:{name} is updated, `holon skill check --dept {slug}` is clean, and "
                                          "each change cites the evidence it answers")
        return {"refinement": w["id"], "playbook": name, "why": why}

    # ── the objective loop: a pool of work and routines steered toward key results ──
    def add_objective(self, title: str, intent: str, *, dept: str, key_results: list[str],
                      project_ref: str | None = None, workdir: str | None = None) -> dict:
        """An objective: its planner decomposes it into one-off work and routines; it stays open while its
        pool runs, and its review reshapes the pool until the key results are met."""
        if not key_results:
            raise ValueError("an objective needs at least one key result (--key-result)")
        return self.work.add_work(title, intent, dept=dept, task_class="objective", project_ref=project_ref,
                                  acceptance="\n".join(f"- {k}" for k in key_results), workdir=workdir,
                                  source=CHAIR if self.actor == CHAIR else "agent", requested_by=self.actor,
                                  priority=1)

    def objective_dashboard(self, oid: str) -> str:
        o = self.work.get(oid)
        kids = [dict(r) for r in self.wconn.execute(
            "SELECT * FROM tasks WHERE stage IS NULL AND objective_id=? AND requested_by NOT LIKE 'recurring:%' "
            "ORDER BY created_at", (oid,))]
        lines = [f"**{o['title']}** [{o['status']}]", "", "Key results:", o["acceptance"] or "(none)", "", "Work:"]
        lines += [f"- {k['title']} [{k['status']}, {k['lane']}]" for k in kids if k["task_class"] != "objective"] or ["- none"]
        lines += ["", "Routines:"]
        for r in self.routines():
            src = self.work.get(self.work.get(r["plan"])["parent_id"])
            if src["objective_id"] != oid:
                continue
            body = (self.work.output(r["plan"]) or {"body": {}})["body"]
            pb = playbooks.read(self.skills, self._dept_slug(src["dept_id"]), body.get("playbook") or "")
            crit = self.wconn.execute("SELECT criterion, avg(p), count(*) FROM judgments WHERE playbook=? AND version=? "
                                      "GROUP BY criterion", (pb["name"], pb["version"])).fetchall() if pb else []
            lines.append(f"- {r['title']} ({r['lane']}, `{r['cron']}`, {'on' if r['enabled'] else 'off'}): fired "
                         f"{r['fired']}, done {r['done']}, failed {r['failed']}"
                         + (f"; playbook v{pb['version']}" if pb else "")
                         + "".join(f"\n  - {c[0][:90]}: mean p={c[1]:.2f} over {c[2]}" for c in crit))
        done = [k for k in kids if k["status"] == "done"][-5:]
        if done:
            lines += ["", "Latest results:"]
            for k in done:
                ex = self.work.last_execute_output(k["id"])
                lines.append(f"- {k['title']}: {((ex or {}).get('summary') or '')[:200]}")
        return "\n".join(lines)

    def _objective_reviews(self) -> list[dict]:
        """An objective's review is due when its pool has finished a cycle (nothing of its own in flight and
        something finished since the last review) or every `review_every_days`."""
        out = []
        every = timedelta(days=float(self.config["objectives"]["review_every_days"]))
        for o in self.wconn.execute("SELECT * FROM tasks WHERE stage IS NULL AND task_class='objective' AND "
                                    "status='waiting'").fetchall():
            o = dict(o)
            if any(s["stage"] == "retro" and s["status"] in ("ready", "claimed") for s in self.work.stages(o["id"])):
                continue
            last = max([s["created_at"] for s in self.work.stages(o["id"], "retro")] or [o["updated_at"]])
            busy = self.wconn.execute(
                "SELECT 1 FROM tasks WHERE stage IS NULL AND objective_id=? AND requested_by NOT LIKE 'recurring:%' "
                "AND status NOT IN ('done','failed','cancelled')", (o["id"],)).fetchone()
            moved = self.wconn.execute(
                "SELECT 1 FROM tasks WHERE stage IS NULL AND objective_id=? AND status IN ('done','failed','cancelled') "
                "AND updated_at>?", (o["id"], last)).fetchone()
            stale = last < (datetime.now(timezone.utc) - every).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            if (moved and not busy) or (stale and moved):
                with write_txn(self.wconn):
                    r = self.work.add_retro(o["id"], "objective review: " + (
                        "its pool finished a cycle" if not busy else f"{every.days} days since the last review"))
                if r:
                    out.append({"objective_review": r["id"], "objective": o["id"]})
        return out

    def _backup(self) -> list[dict]:
        """Every `backup_every_h`, a consistent copy of the instance (the organization exists nowhere else);
        a failure opens an incident. The older backups past `[retention] backup_keep` are then pruned and
        reported: a backup that fills the disk it protects is worthless."""
        h = self.config["healing"]
        last = self._setting("backup_ts")
        if not h["backup_every_h"] or (last and last > (datetime.now(timezone.utc) - timedelta(
                hours=float(h["backup_every_h"]))).strftime("%Y-%m-%dT%H:%M:%S.%fZ")):
            return []
        from holon.integrity import backup
        with write_txn(self.wconn):
            self.wconn.execute("INSERT OR REPLACE INTO settings VALUES ('backup_ts', ?)", (now(),))
        dest = h["backup_to"] or str(self.home.root / "backups")
        try:
            took = [{"backup": backup.backup(self, dest)["path"]}]
            took.extend({"backup_pruned": f"{r['bytes'] / 1048576:.0f} MB in {Path(r['path']).name}"}
                        for r in backup.prune(dest, int(self.config["retention"]["backup_keep"])))
            return took
        except OSError as e:
            r = self._incident("Incident: the scheduled backup failed", f"`holon backup` failed: {e}\nFix the "
                               "cause (disk, permissions, `[healing] backup_to`); done when the next backup succeeds.")
            return [r] if r else []

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

    def _incident_open(self, title: str) -> bool:
        return bool(self.wconn.execute("SELECT 1 FROM tasks WHERE stage IS NULL AND title=? AND status IN "
                                       "('blocked','open','waiting','needs_input')", (title,)).fetchone())

    def _incident(self, title: str, intent: str) -> dict | None:
        dept = self.config["healing"]["incident_dept"]
        try:
            self.org.dept(dept)
        except LookupError:
            dept = EXECUTIVE
        if self._incident_open(title):
            return None
        w = self.work.add_work(title, intent, dept=dept, source="kernel", requested_by="kernel", priority=1,
                               task_class="incident", acceptance="the cause is fixed and the failing path works again")
        return {"incident": w["id"], "title": title}

    def _build_regression(self) -> list[dict]:
        """H10: the rubric judge's pass rate groups by harness build; when the latest build's is materially
        below the previous build's (both with enough judgments), incident work opens — a regressed build
        is visible from judgments alone, before anyone reads the runs."""
        h = self.config["healing"]
        samples, drop = int(h["build_regression_samples"]), float(h["build_regression_drop"])
        out = []
        if not samples or drop <= 0:
            return out
        by_dept: dict[str, list[dict]] = {}
        for r in self.wconn.execute(
                "SELECT t.dept_id AS dept, j.build, count(*) AS n, avg(j.passed) AS rate, max(j.created_at) AS last "
                "FROM judgments j JOIN tasks t ON t.id=j.work_id WHERE j.build IS NOT NULL "
                "GROUP BY t.dept_id, j.build ORDER BY last").fetchall():
            by_dept.setdefault(r["dept"], []).append(dict(r))
        for dept, builds in by_dept.items():
            if len(builds) < 2 or builds[-1]["build"] == builds[-2]["build"]:
                continue
            prev, new = builds[-2], builds[-1]
            if min(prev["n"], new["n"]) < samples or new["rate"] >= prev["rate"] - drop:
                continue
            where = self._dept_slug(dept) or "the organization"
            r = self._incident(f"Incident: harness build regressed ({new['build']})",
                               f"The rubric judge's pass rate on {where} dropped from {prev['rate']:.0%} on build "
                               f"{prev['build']} to {new['rate']:.0%} on build {new['build']} ({new['n']} judgments). "
                               "The build changed and the work got worse: find what the new build does "
                               "differently (result.usage.build names it on every run), fix or roll back the body, "
                               "and let the department verify a result before closing this.")
            if r:
                out.append(r)
        return out

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
            title = f"Incident: runs keep failing on {r['task_id']}"
            if self._incident_open(title):
                continue
            i = self._incident(title, f"{r['n']} runs of the {r['stage']} stage {r['task_id']} ended without a valid "
                               f"result (last employee {r['emp']}). Read their driver.log files under "
                               f"{self.home.runtime_dir / 'runs'} and fix the harness, model or brief."
                               + self._failure_cause(r["task_id"]))
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
            breaches = self._metric_breaches(d["slug"])
            if len(props) < int(c["dept_retro_min_proposals"]) and not breaches:
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
                                        "(`holon metrics --dept " + d["slug"] + "`); turn what should change into actions."
                                        + "".join(f"\nOut of bounds: {b}." for b in breaches),
                                        dept=d["id"], source="kernel", requested_by="kernel", task_class="retro",
                                        expand=False)
                self.work._add_stage(w, "retro", [])
            out.append({"dept_retro": w["id"], "dept": d["slug"], "proposals": len(props), "breaches": breaches})
        return out

    def _metric_breaches(self, dept: str) -> list[str]:
        """The department's rates past their [metrics] bounds, over enough samples to mean something."""
        c, m = self.config["metrics"], self.metrics(dept)
        out = []
        for key, bound in c.items():
            name, _, side = key.rpartition("_")
            if side not in ("min", "max") or m[name]["value"] is None or m[name]["n"] < int(c["min_samples"]):
                continue
            v = m[name]["value"]
            if v < bound if side == "min" else v > bound:
                counter = m[name]["counter"]
                out.append(f"{name} {v} is {'below' if side == 'min' else 'above'} {bound} over {m[name]['n']} "
                           f"(its counter-metric {counter} is {m[counter]['value']})")
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
        judged = q("SELECT count(*), sum(v.outcome='pass') FROM events e JOIN tasks t ON t.id=e.entity_id JOIN tasks v "
                   "ON v.parent_id=t.id AND v.stage='verify' AND v.status='done' WHERE e.entity='task' AND "
                   "e.note LIKE 'rubric pass%' AND {where}")
        lanes = {r[0]: r[1] for r in self.wconn.execute(
            f"SELECT t.lane, count(*) FROM tasks t WHERE t.stage IS NULL AND {where} GROUP BY t.lane", args)}
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
            "first_pass_rate": {"value": ratio(first[1] or 0, first[0]), "n": first[0], "counter": "verify_pass_rate"},
            "verify_pass_rate": {"value": ratio(verifies[1] or 0, verifies[0]), "n": verifies[0],
                                 "counter": "rework_rate"},
            "rework_rate": {"value": ratio(execs[1] or 0, execs[0]), "n": execs[0], "counter": "first_pass_rate"},
            "gate_latency_h": {"value": round(gates[1], 2) if gates[1] is not None else None,
                               "counter": "rejection_rate"},
            "rejection_rate": {"value": ratio(gates[2] or 0, gates[0]), "n": gates[0], "counter": "gate_latency_h"},
            "cycle_time_h_by_stage": cycle,
            "backlog": {"ready_stages": backlog[0], "oldest_age_h": backlog_age_h},
            "research_reuse": {"value": ratio(terrain[1] or 0, terrain[0]), "counter": "first_pass_rate"},
            "cost_per_verified_result": {"value": ratio(cost[0] or 0, verified), "tokens": cost[1] or 0,
                                         "counter": "verify_pass_rate"},
            # of results the rubric judge passed that a verifier also checked, the share the verifier passed
            "rubric_agreement": {"value": ratio(judged[1] or 0, judged[0]), "n": judged[0],
                                 "counter": "verify_pass_rate"},
            "work_by_lane": lanes,
        }
