"""The kernel service: wires the organization, work, memory, briefs, and drivers together.

Two ways for any harness to take part, neither requiring an integration:
  push  `holon dispatch`/`holon tick` claim work for an employee, write a brief, invoke its driver.
  pull  an employee session running anywhere calls `holon claim --duty D --as <emp>`, does the
        work, writes result.json, and calls `holon submit <run_id>`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from holon.capabilities.context import DEFAULT_BUDGET_CHARS, compile_context
from holon.capabilities.memory import Memory
from holon.core.db import connect, migrate, now, write_txn
from holon.core.paths import Home
from holon.execution import briefs, drivers, grading
from holon.execution.briefs import kernel_env
from holon.execution.envelopes import VALIDATORS, EnvelopeError
from holon.execution.org import CHAIR, EXECUTIVE, AuthorityConfig, Org
from holon.execution.schedules import Schedules
from holon.execution.staleness import _hash_dir
from holon.execution.work import Claimant, TransitionError, Work

DEFAULT_CONFIG = {
    "context": {"budget_chars": DEFAULT_BUDGET_CHARS},
    "planner": {"enforce_read_only": True},
    "grading": {},
    "authority": {},
    "memory": {"promotion_threshold": 2},
}


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
    task: dict
    brief_dir: Path
    plan: dict | None = None
    employee: dict | None = None


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
        self.work = Work(self.wconn, actor)
        self.memory = Memory(self.mconn)
        self.org = Org(self.wconn, actor, AuthorityConfig.from_config(self.config))
        self.schedules = Schedules(self.wconn, actor)
        self.org.bootstrap()

    def close(self) -> None:
        self.wconn.close()
        self.mconn.close()

    # ── helpers ───────────────────────────────────────────────────────────
    def _dept_scope(self, dept_id: str | None) -> str | None:
        if not dept_id:
            return None
        return f"dept:{self.org.dept(dept_id)['slug']}"

    def claimant(self, employee_id: str | None) -> tuple[Claimant, dict | None]:
        if not employee_id:
            return Claimant(), None
        e = self.org.employee(employee_id)
        if e["status"] not in ("active", "probation"):
            raise TransitionError(f"employee {employee_id} is {e['status']}")
        ceo = self.org.ceo()
        return Claimant(id=e["id"], dept_id=e["dept_id"], is_head=e["is_head"],
                        is_ceo=bool(ceo and ceo["id"] == e["id"]), in_executive=e["dept"] == EXECUTIVE), e

    # ── the Chair's gate (and any delegated approver's) ───────────────────
    def approve(self, plan_id: str, note: str | None = None, recurring: bool = False,
                review_run: str | None = None) -> dict:
        out = self.work.approve(plan_id, note, recurring, review_run)
        task, plan = out["task"], out["plan"]
        self.memory.record_review(task, plan, "approved", note, self._dept_scope(task["dept_id"]))
        if out["decider"] != CHAIR and self.org.should_sample(plan_id):
            with write_txn(self.wconn):
                self.wconn.execute("INSERT OR IGNORE INTO audit_samples (plan_id, approver, sampled_at) VALUES (?,?,?)",
                                   (plan_id, out["decider"], now()))
            out["audit_sampled"] = True
        return out

    def reject(self, plan_id: str, note: str, review_run: str | None = None) -> dict:
        out = self.work.reject(plan_id, note, review_run)
        self.memory.record_review(out["task"], out["plan"], "rejected", note, self._dept_scope(out["task"]["dept_id"]))
        return out

    def regrade(self, plan_id: str, grade: int, note: str) -> dict:
        if self.actor != CHAIR:
            raise TransitionError("only the Chair regrades plans")
        plan = self.work.get_plan(plan_id)
        task = self.work.get_task(plan["task_id"])
        approver = self.org.resolve_approver(task, grade, plan["author"])
        return self.work.set_grade(plan_id, grade, approver, note)

    def answer(self, task_id: str, text: str) -> dict:
        task = self.work.get_task(task_id)
        question = task.get("blocked_reason") or "planner question"
        task = self.work.answer(task_id, text)
        scope = task.get("project_ref") or self._dept_scope(task["dept_id"]) or "org"
        self.memory.add_item(scope, "decision", f"Q: {question}\nA: {text}", self.actor, confidence="high")
        return task

    def verify(self, task_id: str, verdict: str, note: str, run_id: str | None = None) -> dict:
        task = self.work.verify(task_id, verdict, note, run_id)
        plan = self.work.latest_plan(task_id, "approved") or self.work.latest_plan(task_id)
        change = None
        if plan and plan["decided_by"] and plan["decided_by"].startswith("emp:"):
            change = self.org.update_earned_authority(plan["decided_by"], task.get("task_class"))
        return {"task": task, "authority_change": change}

    def judge_sample(self, plan_id: str, verdict: str, note: str) -> dict:
        """The Chair re-judges a sampled employee approval; a disagreement costs the approver authority."""
        if verdict not in ("agree", "disagree"):
            raise ValueError("verdict must be agree or disagree")
        row = self.wconn.execute("SELECT * FROM audit_samples WHERE plan_id=?", (plan_id,)).fetchone()
        if row is None:
            raise LookupError(f"plan {plan_id} is not an audit sample")
        with write_txn(self.wconn):
            self.wconn.execute("UPDATE audit_samples SET verdict=?, note=?, judged_at=? WHERE plan_id=?",
                               (verdict, note, now(), plan_id))
        task = self.work.get_task(self.work.get_plan(plan_id)["task_id"])
        change = self.org.update_earned_authority(row["approver"], task.get("task_class")) \
            if verdict == "disagree" else None
        return {"plan_id": plan_id, "verdict": verdict, "authority_change": change}

    def audit_samples(self, pending_only: bool = True) -> list[dict]:
        q = ("SELECT a.*, p.grade, t.title FROM audit_samples a JOIN plans p ON p.id=a.plan_id "
             "JOIN tasks t ON t.id=p.task_id" + (" WHERE a.verdict IS NULL" if pending_only else "")
             + " ORDER BY a.sampled_at")
        return [dict(r) for r in self.wconn.execute(q)]

    # ── communication ─────────────────────────────────────────────────────
    def request(self, sender: str, dept: str, title: str, intent: str, **kw) -> dict:
        """A cross-department ask: becomes a task in that department's inbox and a message to it."""
        task = self.work.add_task(title, intent, dept=dept, source="agent" if sender != CHAIR else "chair",
                                  requested_by=sender, **kw)
        self.work.post_message(sender, f"dept:{self.org.dept(dept)['slug']}", f"Request: {title}\n\n{intent}",
                               kind="request", task_id=task["id"])
        return task

    def inbox(self, ref: str) -> list[dict]:
        refs = [ref]
        if ref.startswith("emp:"):
            e = self.org.employee(ref[4:])
            refs += [f"dept:{e['dept']}", "org"]
        return self.work.messages(to_refs=refs)

    # ── claiming ──────────────────────────────────────────────────────────
    def claim(self, duty: str, employee_id: str | None = None, driver: str | None = None,
              task_id: str | None = None) -> Claimed | None:
        who, emp = self.claimant(employee_id)
        if emp and duty not in emp["duties"]:
            raise TransitionError(f"{emp['name']} ({employee_id}) does not have the '{duty}' duty")
        driver = driver or (emp["driver"] if emp else None)
        plan = None
        if duty == "plan":
            got = self.work.claim_for_planning(who, driver, task_id=task_id)
            if got is None:
                return None
            task, run = got
        elif duty == "execute":
            got = self.work.claim_for_execution(who, driver, task_id=task_id)
            if got is None:
                return None
            task, plan, run = got
        elif duty == "verify":
            got = self.work.claim_for_verification(who, driver)
            if got is None:
                return None
            task, run = got
        elif duty == "review":
            got = self.work.claim_for_review(who, driver)
            if got is None:
                return None
            task, plan, run = got
        else:
            raise ValueError(f"unknown duty {duty}")
        md, _ = compile_context(self.home, self.work, self.memory, task["id"], duty,
                                budget=int(self.config["context"]["budget_chars"]), run_id=run["id"],
                                employee=emp, predecessors=self.org.predecessors(emp["id"]) if emp else None,
                                plan_id=plan["id"] if (plan and duty == "review") else None)
        bdir = briefs.build(self.home, run, task, md)
        self.work.set_run_paths(run["id"], str(bdir), str(bdir / "driver.log"))
        return Claimed(run=self.work.get_run(run["id"]), task=task, brief_dir=bdir, plan=plan, employee=emp)

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
        kind = run["kind"]
        try:
            if envelope is None:
                raise EnvelopeError(["no result.json and no JSON output"])
            env = VALIDATORS[kind](envelope)
            if kind == "plan":
                self._check_org_refs(env)
        except EnvelopeError as e:
            return self._contract_breach(run, e.problems, driver_output, exit_code)
        if kind == "plan":
            task = self.work.get_task(run["task_id"])
            if env["kind"] == "needs_input":
                return {"kind": kind, **self.work.submit_plan(run_id, env, exit_code=exit_code)}
            g = grading.grade_plan(self.wconn, task, env, self.config)
            approver = self.org.resolve_approver(task, g["grade"], run["employee_id"])
            return {"kind": kind, **self.work.submit_plan(run_id, env, g, approver, exit_code)}
        if kind == "execute":
            out = self.work.submit_result(run_id, env, exit_code)
            task = out["task"]
            emp = self.org.employee(run["employee_id"]) if run["employee_id"] else None
            mem = self.memory.consolidate(task, run_id, env, run["employee_id"],
                                          self._dept_scope(task["dept_id"]) or (f"dept:{emp['dept']}" if emp else None))
            mem["promotions"] = self._promote(mem.pop("lessons"), emp)
            return {"kind": kind, **out, "memory": mem}
        if kind == "verify":
            return {"kind": kind, **self.verify(run["task_id"], env["verdict"], env["note"], run_id=run_id)}
        if env["decision"] == "approve":
            return {"kind": kind, **self.approve(run["plan_id"], env.get("note"), review_run=run_id)}
        return {"kind": kind, **self.reject(run["plan_id"], env["note"], review_run=run_id)}

    def _check_org_refs(self, env: dict) -> None:
        problems = []

        def emp_ok(eid: str, where: str) -> None:
            try:
                if self.org.employee(eid)["status"] == "retired":
                    problems.append(f"{where}: employee {eid} is retired")
            except LookupError:
                problems.append(f"{where}: no employee {eid}")

        if env.get("assignee"):
            emp_ok(env["assignee"], "assignee")
        for i, c in enumerate(env.get("children") or []):
            if c.get("dept"):
                try:
                    self.org.dept(c["dept"])
                except LookupError:
                    problems.append(f"children[{i}]: no department {c['dept']}")
            if c.get("assignee"):
                emp_ok(c["assignee"], f"children[{i}].assignee")
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
        """An employee that did not honor the result contract. Plan/verify/review runs fail and the
        work returns to the queue; execution output is wrapped as 'partial' for an independent verifier."""
        note = "result contract breach: " + "; ".join(problems)
        tail = (driver_output or {}).get("stdout_tail", "")
        if run["kind"] == "execute":
            env = {"outcome": "partial", "summary": f"Output did not satisfy the result contract ({problems[0]}).",
                   "documentation": f"**{note}**\n\nDriver stdout (tail):\n\n```\n{tail or '(none)'}\n```",
                   "blockers": [note]}
            out = self.work.submit_result(run["id"], env, exit_code)
            return {"kind": "execute", "breach": problems, **out}
        with write_txn(self.wconn):
            self.work._close_run(run["id"], "failed", exit_code)
            task = self.work.get_task(run["task_id"])
            if run["kind"] == "plan" and task["status"] == "planning":
                task = self.work._move(task["id"], "inbox", note,
                                       actor=f"emp:{run['employee_id']}" if run["employee_id"] else None)
        return {"kind": run["kind"], "breach": problems, "task": task}

    # ── push mode ─────────────────────────────────────────────────────────
    def run_driver(self, claimed: Claimed, driver_name: str) -> dict:
        drv = drivers.get(self.home, driver_name)
        brief = briefs.load(self.home, claimed.run["id"])
        workdir = claimed.task.get("workdir")
        guard = (claimed.run["kind"] in ("plan", "review") and workdir and Path(workdir).is_dir()
                 and self.config["planner"]["enforce_read_only"]
                 and not claimed.brief_dir.resolve().is_relative_to(Path(workdir).resolve()))
        before = _hash_dir(Path(workdir)) if guard else None
        out = drivers.run(self.home, drv, brief, claimed.run["kind"])
        if guard and _hash_dir(Path(workdir)) != before:
            return self._contract_breach(self.work.get_run(claimed.run["id"]),
                                         [f"{claimed.run['kind']} run modified the working directory (must be read-only)"],
                                         out, out["exit_code"])
        return self.submit(claimed.run["id"], out["envelope"], out["exit_code"], driver_output=out)

    def dispatch(self, duty: str, employee_id: str, task_id: str | None = None) -> dict | None:
        emp = self.org.employee(employee_id)
        if self.wconn.execute("SELECT count(*) FROM runs WHERE employee_id=? AND status='running'",
                              (employee_id,)).fetchone()[0] >= emp["max_concurrency"]:
            return None
        claimed = self.claim(duty, employee_id, emp["driver"], task_id)
        if claimed is None:
            return None
        return self.run_driver(claimed, emp["driver"])

    def tick(self, foreground: bool = False) -> dict:
        """Idempotent heartbeat; call it from any timer (systemd, cron, a harness scheduler)."""
        swept = self.work.sweep_leases()
        fired = []
        for s in self.schedules.take_due():
            if s["kind"] == "recurring":
                task = self.schedules.instantiate_recurring(s["plan_id"])
                fired.append({"schedule": s["id"], "recurring_task": task["id"]})
                continue
            if foreground:
                try:
                    res = self.dispatch(s["kind"], s["employee_id"])
                    self.schedules.record_outcome(s["id"], True)
                    fired.append({"schedule": s["id"], "dispatched": bool(res)})
                except Exception as e:  # one failing employee must not stop the organization's heartbeat
                    self.schedules.record_outcome(s["id"], False)
                    fired.append({"schedule": s["id"], "error": str(e)})
            else:
                self._spawn_dispatch(s["kind"], s["employee_id"], s["id"])
                fired.append({"schedule": s["id"], "spawned": True})
        return {"swept": swept, "fired": fired}

    def _spawn_dispatch(self, duty: str, employee_id: str, schedule_id: str) -> None:
        log = self.home.runtime_dir / "dispatch.log"
        with open(log, "a") as fh:
            subprocess.Popen(
                [sys.executable, "-m", "holon", "--home", str(self.home.root), "--actor", f"schedule:{schedule_id}",
                 "dispatch", "--duty", duty, "--as", employee_id, "--schedule", schedule_id],
                stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                env={**os.environ, **kernel_env(self.home)})
