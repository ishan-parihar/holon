"""The kernel service: wires work.db, memory.db, briefs, and drivers together.

Two ways for any harness to take part, neither requiring an integration:
  push  `holon dispatch`/`holon tick` claim work, write a brief, and invoke a driver.
  pull  an agent already running anywhere calls `holon claim --role R`, does the work,
        writes result.json, and calls `holon submit <run_id>`.
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
from holon.capabilities.memory import Memory, scope_for
from holon.core.db import connect, migrate
from holon.core.paths import Home
from holon.execution import briefs, drivers
from holon.execution.envelopes import VALIDATORS, EnvelopeError
from holon.execution.briefs import KIND_OF_ROLE, kernel_env
from holon.execution.roster import Roster
from holon.execution.staleness import _hash_dir
from holon.execution.work import TransitionError, Work

DEFAULT_CONFIG = {
    "context": {"budget_chars": DEFAULT_BUDGET_CHARS},
    "planner": {"enforce_read_only": True},
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


class Kernel:
    def __init__(self, home: Home, actor: str = "human:cli"):
        self.home = home
        self.actor = actor
        home.ensure()
        self.wconn = connect(home.work_db)
        self.mconn = connect(home.memory_db)
        migrate(self.wconn, "work")
        migrate(self.mconn, "memory")
        self.work = Work(self.wconn, actor)
        self.memory = Memory(self.mconn)
        self.roster = Roster(self.wconn, actor)
        self.config = load_config(home)

    def close(self) -> None:
        self.wconn.close()
        self.mconn.close()

    # ── human gate (approval writes your notes into memory) ───────────────
    def approve(self, plan_id: str, note: str | None = None, recurring: bool = False) -> dict:
        out = self.work.approve(plan_id, note, recurring)
        self.memory.record_review(out["task"], out["plan"], "approved", note)
        return out

    def reject(self, plan_id: str, note: str) -> dict:
        out = self.work.reject(plan_id, note)
        self.memory.record_review(out["task"], out["plan"], "rejected", note)
        return out

    def answer(self, task_id: str, text: str) -> dict:
        task = self.work.get_task(task_id)
        question = task.get("blocked_reason") or "planner question"
        task = self.work.answer(task_id, text)
        self.memory.add_item(scope_for(task), "decision", f"Q: {question}\nA: {text}", "human", confidence="high")
        return task

    # ── claiming (pull mode entry point; push mode builds on it) ──────────
    def claim(self, role: str, agent_id: str | None = None, driver: str | None = None,
              task_id: str | None = None) -> Claimed | None:
        kind = KIND_OF_ROLE[role]
        plan = None
        if kind == "plan":
            got = self.work.claim_for_planning(agent_id, driver, task_id=task_id)
            if got is None:
                return None
            task, run = got
        elif kind == "execute":
            got = self.work.claim_for_execution(agent_id, driver, task_id=task_id)
            if got is None:
                return None
            task, plan, run = got
        else:
            got = self.work.claim_for_verification(agent_id, driver)
            if got is None:
                return None
            task, run = got
        md, _ = compile_context(self.home, self.work, self.memory, task["id"], kind,
                                budget=int(self.config["context"]["budget_chars"]), run_id=run["id"])
        pdir = briefs.build(self.home, run, task, md)
        self.work.set_run_paths(run["id"], str(pdir), str(pdir / "driver.log"))
        return Claimed(run=self.work.get_run(run["id"]), task=task, brief_dir=pdir, plan=plan)

    # ── ingestion: the one place agent output enters the organization ─────────
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
        except EnvelopeError as e:
            return self._contract_breach(run, e.problems, driver_output, exit_code)
        if kind == "plan":
            return {"kind": kind, **self.work.submit_plan(run_id, env, exit_code)}
        if kind == "execute":
            out = self.work.submit_result(run_id, env, exit_code)
            out["memory"] = self.memory.consolidate(out["task"], run_id, env)
            return {"kind": kind, **out}
        task = self.work.verify(run["task_id"], env["verdict"], env["note"], run_id=run_id)
        return {"kind": kind, "task": task}

    def _contract_breach(self, run: dict, problems: list[str], driver_output: dict | None,
                         exit_code: int | None) -> dict:
        """An agent that did not honor the result contract. Planning and verification runs
        are abandoned (the work returns to the queue); execution output is wrapped as a
        'partial' result so a verifier, not the agent, decides what happened."""
        note = "result contract breach: " + "; ".join(problems)
        tail = (driver_output or {}).get("stdout_tail", "")
        if run["kind"] == "execute":
            env = {"outcome": "partial", "summary": f"Agent output did not satisfy the result contract ({problems[0]}).",
                   "documentation": f"**{note}**\n\nDriver stdout (tail):\n\n```\n{tail or '(none)'}\n```",
                   "blockers": [note]}
            out = self.work.submit_result(run["id"], env, exit_code)
            return {"kind": "execute", "breach": problems, **out}
        from holon.core.db import write_txn
        with write_txn(self.wconn):
            self.work._close_run(run["id"], "failed", exit_code)
            task = self.work.get_task(run["task_id"])
            if run["kind"] == "plan" and task["status"] == "planning":
                task = self.work._move(task["id"], "inbox", note, actor=run["agent_id"])
        return {"kind": run["kind"], "breach": problems, "task": task}

    # ── push mode: invoke a harness through its driver ────────────────────
    def run_driver(self, claimed: Claimed, driver_name: str) -> dict:
        drv = drivers.get(self.home, driver_name)
        brief = briefs.load(self.home, claimed.run["id"])
        workdir = claimed.task.get("workdir")
        guard = (claimed.run["kind"] == "plan" and workdir and Path(workdir).is_dir()
                 and self.config["planner"]["enforce_read_only"]
                 and not claimed.brief_dir.resolve().is_relative_to(Path(workdir).resolve()))
        before = _hash_dir(Path(workdir)) if guard else None
        out = drivers.run(self.home, drv, brief, claimed.run["kind"])
        if guard and _hash_dir(Path(workdir)) != before:
            return self._contract_breach(self.work.get_run(claimed.run["id"]),
                                         ["planner modified the working directory during read-only planning"],
                                         out, out["exit_code"])
        return self.submit(claimed.run["id"], out["envelope"], out["exit_code"], driver_output=out)

    def dispatch(self, role: str, agent_id: str, task_id: str | None = None) -> dict | None:
        agent = self.roster.get_agent(agent_id)
        if agent["status"] != "active":
            raise TransitionError(f"agent {agent_id} is {agent['status']}")
        if agent["role"] != role:
            raise TransitionError(f"agent {agent_id} is a {agent['role']}, not a {role}")
        if self.roster.running_count(agent_id) >= agent["max_concurrency"]:
            return None
        claimed = self.claim(role, agent_id, agent["driver"], task_id)
        if claimed is None:
            return None
        return self.run_driver(claimed, agent["driver"])

    # ── the heartbeat of the organization ─────────────────────────────────────
    def tick(self, foreground: bool = False) -> dict:
        """Idempotent; call it from any timer (systemd, cron, launchd, a harness scheduler)."""
        swept = self.work.sweep_leases()
        fired = []
        for s in self.roster.take_due():
            if s["kind"] == "recurring":
                task = self.roster.instantiate_recurring(s["plan_id"])
                fired.append({"schedule": s["id"], "recurring_task": task["id"]})
                continue
            if foreground:
                try:
                    res = self.dispatch(s["kind"], s["agent_id"])
                    self.roster.record_schedule_outcome(s["id"], True)
                    fired.append({"schedule": s["id"], "dispatched": bool(res)})
                except Exception as e:  # a failing agent must not stop the tick
                    self.roster.record_schedule_outcome(s["id"], False)
                    fired.append({"schedule": s["id"], "error": str(e)})
            else:
                self._spawn_dispatch(s["kind"], s["agent_id"], s["id"])
                fired.append({"schedule": s["id"], "spawned": True})
        return {"swept": swept, "fired": fired}

    def _spawn_dispatch(self, role: str, agent_id: str, schedule_id: str) -> None:
        log = self.home.runtime_dir / "dispatch.log"
        with open(log, "a") as fh:
            subprocess.Popen(
                [sys.executable, "-m", "holon", "--home", str(self.home.root), "--actor", f"schedule:{schedule_id}",
                 "dispatch", "--role", role, "--agent", agent_id, "--schedule", schedule_id],
                stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                env={**os.environ, **kernel_env(self.home)})
