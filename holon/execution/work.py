"""work.db: the delegated-work state machine. The only writer of work.db.

    inbox -> planning -> awaiting_approval -> approved -> executing -> verifying -> done
                |  \\-> needs_input -> inbox      ^  |           |            \\-> failed
                |                                |  \\-> stale -> inbox       (retry / replan)
                \\-> (decompose approved) waiting_on_subtasks -> done when all children close

Every transition is checked against TRANSITIONS and recorded in `events`.
"""

from __future__ import annotations

import json
import sqlite3
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
DEFAULT_LEASE_S = {"plan": 1800, "execute": 3600, "verify": 1200}


class TransitionError(RuntimeError):
    pass


class NotFound(LookupError):
    pass


def _lease(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def _dumps(v: Any) -> str | None:
    if v is None:
        return None
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


class Work:
    def __init__(self, conn: sqlite3.Connection, actor: str = "human:cli"):
        self.conn = conn
        self.actor = actor

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

    def _stat(self, task_class: str | None, **inc: int) -> None:
        key = task_class or "(none)"
        self.conn.execute("INSERT OR IGNORE INTO task_class_stats (task_class) VALUES (?)", (key,))
        for col, n in inc.items():
            if col == "last_failure_at":
                self.conn.execute("UPDATE task_class_stats SET last_failure_at=? WHERE task_class=?", (now(), key))
            else:
                self.conn.execute(f"UPDATE task_class_stats SET {col}={col}+? WHERE task_class=?", (n, key))

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

    def list_tasks(self, status: str | None = None, project_ref: str | None = None, parent_id: str | None = None,
                   limit: int = 50) -> list[dict]:
        q, args = "SELECT * FROM tasks WHERE 1=1", []
        if status:
            q += " AND status IN (%s)" % ",".join("?" * len(status.split(",")))
            args += status.split(",")
        if project_ref:
            q += " AND project_ref=?"
            args.append(project_ref)
        if parent_id:
            q += " AND parent_id=?"
            args.append(parent_id)
        q += " ORDER BY priority, created_at LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(q, args)]

    def awaiting_approval(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT p.*, t.title AS task_title, t.priority, t.risk, t.task_class FROM plans p "
            "JOIN tasks t ON t.id=p.task_id WHERE p.status='awaiting_approval' ORDER BY t.priority, p.created_at")]

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
                 parent_id: str | None = None, priority: int = 3, source: str = "human", task_class: str | None = None,
                 risk: str = "normal", workdir: str | None = None, due: str | None = None,
                 max_attempts: int = 2) -> dict:
        if not title.strip() or not intent.strip():
            raise ValueError("title and intent are required")
        tid, ts = new_id("task"), now()
        with write_txn(self.conn):
            if parent_id:
                self.get_task(parent_id)
            self.conn.execute(
                "INSERT INTO tasks (id, project_ref, parent_id, title, intent, acceptance, priority, source, task_class,"
                " risk, workdir, due, max_attempts, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, project_ref, parent_id, title.strip(), intent.strip(), acceptance, priority, source, task_class,
                 risk, workdir, due, max_attempts, ts, ts))
            self._event("task", tid, None, "inbox", f"source={source}")
        return self.get_task(tid)

    # ── planning ──────────────────────────────────────────────────────────
    def claim_for_planning(self, agent_id: str | None, driver: str | None = None,
                           lease_s: int = DEFAULT_LEASE_S["plan"], task_id: str | None = None) -> tuple[dict, dict] | None:
        with write_txn(self.conn):
            if task_id:
                row = self.conn.execute("SELECT id FROM tasks WHERE id=? AND status='inbox'", (task_id,)).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT id FROM tasks WHERE status='inbox' ORDER BY priority, created_at LIMIT 1").fetchone()
            if row is None:
                return None
            task = self._move(row["id"], "planning", f"claimed by {agent_id}", actor=agent_id or self.actor)
            run = self._open_run(task["id"], None, "plan", agent_id, driver, lease_s)
        return task, run

    def _open_run(self, task_id: str, plan_id: str | None, kind: str, agent_id: str | None, driver: str | None,
                  lease_s: int) -> dict:
        attempt = 1
        if kind == "execute":
            attempt = 1 + self.conn.execute(
                "SELECT count(*) FROM runs WHERE task_id=? AND kind='execute' AND plan_id=?", (task_id, plan_id)
            ).fetchone()[0]
        rid, ts = new_id("run"), now()
        self.conn.execute(
            "INSERT INTO runs (id, task_id, plan_id, kind, agent_id, driver, attempt, lease_until, heartbeat_at, started_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, task_id, plan_id, kind, agent_id, driver, attempt, _lease(lease_s), ts, ts))
        self._event("run", rid, None, "running", f"{kind} for {task_id}", actor=agent_id or self.actor)
        return self.get_run(rid)

    def _close_run(self, run_id: str, status: str, exit_code: int | None = None, **metrics: Any) -> None:
        run = self.get_run(run_id)
        if run["status"] != "running":
            raise TransitionError(f"run {run_id} is already '{run['status']}'")
        self.conn.execute(
            "UPDATE runs SET status=?, ended_at=?, exit_code=?, tokens_in=?, tokens_out=?, cost=? WHERE id=?",
            (status, now(), exit_code, metrics.get("tokens_in"), metrics.get("tokens_out"), metrics.get("cost"), run_id))
        self._event("run", run_id, "running", status, actor=run["agent_id"] or self.actor)

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

    def submit_plan(self, run_id: str, env: dict, exit_code: int | None = 0) -> dict:
        """Ingest a validated planner envelope. Returns {'task', 'plan'?, 'question'?}."""
        run = self.get_run(run_id)
        if run["kind"] != "plan":
            raise TransitionError(f"run {run_id} is a {run['kind']} run, not a plan run")
        with write_txn(self.conn):
            task = self.get_task(run["task_id"])
            if task["status"] != "planning":
                raise TransitionError(f"task {task['id']} is '{task['status']}', not planning")
            self._close_run(run_id, "succeeded", exit_code)
            if env["kind"] == "needs_input":
                self._message(sender=run["agent_id"] or "planner", to_ref="human", task_id=task["id"],
                              body=env["question"], ack_required=1)
                task = self._move(task["id"], "needs_input", "planner needs input", actor=run["agent_id"],
                                  blocked_reason=env["question"])
                return {"task": task, "question": env["question"]}
            version = 1 + self.conn.execute("SELECT coalesce(max(version),0) FROM plans WHERE task_id=?",
                                            (task["id"],)).fetchone()[0]
            self.conn.execute("UPDATE plans SET status='superseded' WHERE task_id=? AND status='awaiting_approval'",
                              (task["id"],))
            pid = new_id("plan")
            if env["kind"] == "decompose":
                steps, findings, writes, fp = _dumps(env["children"]), env["rationale"], None, None
            else:
                steps = env["steps"] if isinstance(env["steps"], str) else _dumps(env["steps"])
                findings, writes = env["findings"], env.get("writes") or []
                fp = fingerprint(task["workdir"], writes)
            self.conn.execute(
                "INSERT INTO plans (id, task_id, version, kind, planner_agent, findings, writes_fingerprint, steps,"
                " writes, risks, rollback, estimate, context_refs, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, task["id"], version, "decompose" if env["kind"] == "decompose" else "execute", run["agent_id"],
                 findings, fp, steps, _dumps(writes), env.get("risks"), env.get("rollback"), env.get("estimate"),
                 _dumps(env.get("context_refs")), now()))
            self.conn.execute("UPDATE runs SET plan_id=? WHERE id=?", (pid, run_id))
            if env.get("acceptance") and not task["acceptance"]:
                self.conn.execute("UPDATE tasks SET acceptance=? WHERE id=?", (env["acceptance"], task["id"]))
            self._event("plan", pid, None, "awaiting_approval", f"v{version}", actor=run["agent_id"])
            self._stat(task["task_class"], plans=1)
            task = self._move(task["id"], "awaiting_approval", f"plan {pid} v{version}", actor=run["agent_id"])
        return {"task": task, "plan": self.get_plan(pid)}

    # ── approval (the human gate) ─────────────────────────────────────────
    def approve(self, plan_id: str, note: str | None = None, recurring: bool = False) -> dict:
        with write_txn(self.conn):
            plan = self.get_plan(plan_id)
            if plan["status"] != "awaiting_approval":
                raise TransitionError(f"plan {plan_id} is '{plan['status']}'")
            task = self.get_task(plan["task_id"])
            self.conn.execute(
                "UPDATE plans SET status='approved', review_note=?, decided_by=?, decided_at=?, recurring=? WHERE id=?",
                (note, self.actor, now(), 1 if recurring else 0, plan_id))
            self._event("plan", plan_id, "awaiting_approval", "approved", note)
            if plan["version"] == 1:
                self._stat(task["task_class"], approved_first_try=1)
            children = []
            if plan["kind"] == "decompose":
                self._move(task["id"], "waiting_on_subtasks", f"decomposed by {plan_id}")
                for c in json.loads(plan["steps"]):
                    cid, ts = new_id("task"), now()
                    self.conn.execute(
                        "INSERT INTO tasks (id, project_ref, parent_id, title, intent, acceptance, priority, source,"
                        " task_class, risk, workdir, max_attempts, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, task["project_ref"], task["id"], c["title"], c["intent"], c.get("acceptance"),
                         c.get("priority", task["priority"]), "agent", c.get("task_class", task["task_class"]),
                         c.get("risk", task["risk"]), c.get("workdir", task["workdir"]), task["max_attempts"], ts, ts))
                    self._event("task", cid, None, "inbox", f"child of {task['id']}")
                    children.append(cid)
            else:
                self._move(task["id"], "approved", f"plan {plan_id} approved")
        return {"plan": self.get_plan(plan_id), "task": self.get_task(task["id"]), "children": children}

    def reject(self, plan_id: str, note: str) -> dict:
        if not note or not note.strip():
            raise ValueError("a rejection needs a review note: it is what the planner learns from")
        with write_txn(self.conn):
            plan = self.get_plan(plan_id)
            if plan["status"] != "awaiting_approval":
                raise TransitionError(f"plan {plan_id} is '{plan['status']}'")
            task = self.get_task(plan["task_id"])
            self.conn.execute("UPDATE plans SET status='rejected', review_note=?, decided_by=?, decided_at=? WHERE id=?",
                              (note, self.actor, now(), plan_id))
            self._event("plan", plan_id, "awaiting_approval", "rejected", note)
            self._stat(task["task_class"], rejected=1)
            task = self._move(task["id"], "inbox", f"plan {plan_id} rejected: replan")
        return {"plan": self.get_plan(plan_id), "task": task}

    def answer(self, task_id: str, answer: str) -> dict:
        with write_txn(self.conn):
            task = self.get_task(task_id)
            self._message(sender=self.actor, to_ref="planner", task_id=task_id, body=answer)
            self.conn.execute("UPDATE messages SET acked_at=? WHERE task_id=? AND ack_required=1 AND acked_at IS NULL",
                              (now(), task_id))
            task = self._move(task_id, "inbox", "input provided", expect={"needs_input"})
        return task

    # ── execution ─────────────────────────────────────────────────────────
    def claim_for_execution(self, agent_id: str | None, driver: str | None = None,
                            lease_s: int = DEFAULT_LEASE_S["execute"], task_id: str | None = None
                            ) -> tuple[dict, dict, dict] | None:
        """Claim the next approved task. Plans whose findings changed since approval are
        marked superseded and their tasks return to the inbox for replanning."""
        while True:
            with write_txn(self.conn):
                if task_id:
                    row = self.conn.execute("SELECT id FROM tasks WHERE id=? AND status='approved'", (task_id,)).fetchone()
                else:
                    row = self.conn.execute(
                        "SELECT id FROM tasks WHERE status='approved' ORDER BY priority, created_at LIMIT 1").fetchone()
                if row is None:
                    return None
                task = self.get_task(row["id"])
                plan = self.latest_plan(task["id"], "approved")
                if plan is None:
                    raise TransitionError(f"approved task {task['id']} has no approved plan")
                if plan["writes_fingerprint"]:
                    current = fingerprint(task["workdir"], json.loads(plan["writes"] or "[]"))
                    if current != plan["writes_fingerprint"]:
                        self.conn.execute("UPDATE plans SET status='superseded' WHERE id=?", (plan["id"],))
                        self._event("plan", plan["id"], "approved", "superseded", "findings changed since approval")
                        self._move(task["id"], "inbox", "stale plan: findings changed", blocked_reason="stale_plan")
                        if task_id:
                            return None
                        continue
                task = self._move(task["id"], "executing", f"claimed by {agent_id}", actor=agent_id or self.actor)
                run = self._open_run(task["id"], plan["id"], "execute", agent_id, driver, lease_s)
                self._stat(task["task_class"], runs=1)
            return task, plan, run

    def submit_result(self, run_id: str, env: dict, exit_code: int | None = 0, **metrics: Any) -> dict:
        run = self.get_run(run_id)
        if run["kind"] != "execute":
            raise TransitionError(f"run {run_id} is a {run['kind']} run, not an execute run")
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
                task = self._move(task["id"], "verifying", f"result {env['outcome']}", actor=run["agent_id"])
        return {"task": task, "run": self.get_run(run_id), "result": self.result_for_run(run_id)}

    def _fail_or_retry(self, task: dict, run: dict, reason: str) -> dict:
        self._stat(task["task_class"], last_failure_at=1)
        attempts = self.conn.execute(
            "SELECT count(*) FROM runs WHERE task_id=? AND kind='execute' AND plan_id=?", (task["id"], run["plan_id"])
        ).fetchone()[0]
        if attempts < task["max_attempts"]:
            return self._move(task["id"], "approved", f"retry {attempts + 1}/{task['max_attempts']}: {reason}",
                              actor=run["agent_id"])
        task = self._move(task["id"], "failed", reason, actor=run["agent_id"], blocked_reason=reason)
        self._close_parent_if_settled(task)
        return task

    # ── verification ──────────────────────────────────────────────────────
    def claim_for_verification(self, agent_id: str | None, driver: str | None = None,
                               lease_s: int = DEFAULT_LEASE_S["verify"]) -> tuple[dict, dict] | None:
        with write_txn(self.conn):
            row = self.conn.execute(
                "SELECT t.id FROM tasks t WHERE t.status='verifying' AND NOT EXISTS ("
                " SELECT 1 FROM runs r WHERE r.task_id=t.id AND r.kind='verify' AND r.status='running')"
                " ORDER BY t.priority, t.updated_at LIMIT 1").fetchone()
            if row is None:
                return None
            task = self.get_task(row["id"])
            plan = self.latest_plan(task["id"], "approved")
            run = self._open_run(task["id"], plan["id"] if plan else None, "verify", agent_id, driver, lease_s)
        return task, run

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
                actor = vrun["agent_id"] or actor
                self._close_run(run_id, "succeeded")
            exec_run = self.conn.execute(
                "SELECT * FROM runs WHERE task_id=? AND kind='execute' ORDER BY started_at DESC LIMIT 1",
                (task_id,)).fetchone()
            self.conn.execute("UPDATE results SET verified=?, verifier_note=? WHERE run_id=?",
                              (1 if verdict == "pass" else 0, note, exec_run["id"]))
            if verdict == "pass":
                self._stat(task["task_class"], verified_success=1)
                task = self._move(task_id, "done", f"verified: {note}", actor=actor)
                self._close_parent_if_settled(task)
            else:
                task = self._fail_or_retry(task, dict(exec_run), f"verification failed: {note}")
        return task

    # ── operator controls ─────────────────────────────────────────────────
    def cancel(self, task_id: str, note: str) -> dict:
        with write_txn(self.conn):
            task = self._move(task_id, "cancelled", note)
            self.conn.execute("UPDATE plans SET status='superseded' WHERE task_id=? AND status='awaiting_approval'",
                              (task_id,))
            for r in self.conn.execute("SELECT id FROM runs WHERE task_id=? AND status='running'", (task_id,)).fetchall():
                self._close_run(r["id"], "abandoned")
            self._close_parent_if_settled(task)
        return task

    def retry(self, task_id: str, note: str = "operator retry") -> dict:
        with write_txn(self.conn):
            plan = self.latest_plan(task_id, "approved")
            if plan is None:
                raise TransitionError("no approved plan to retry; use replan")
            self.conn.execute("UPDATE tasks SET max_attempts=max_attempts+1 WHERE id=?", (task_id,))
            return self._move(task_id, "approved", note, expect={"failed"})

    def replan(self, task_id: str, note: str = "operator replan") -> dict:
        with write_txn(self.conn):
            self.conn.execute("UPDATE plans SET status='superseded' WHERE task_id=? AND status='approved'", (task_id,))
            return self._move(task_id, "inbox", note, expect={"failed", "approved"})

    def sweep_leases(self) -> list[dict]:
        """Recover work held by agents that died: expired planning returns to inbox,
        expired execution retries or fails, expired verification is reopened."""
        swept = []
        with write_txn(self.conn):
            expired = self.conn.execute(
                "SELECT * FROM runs WHERE status='running' AND lease_until < ?", (now(),)).fetchall()
            for run in expired:
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
            done = self._move(parent_id, "done", "all sub-tasks closed")
            self._close_parent_if_settled(done)
        else:
            self._move(parent_id, "cancelled", "all sub-tasks cancelled")

    # ── messages ──────────────────────────────────────────────────────────
    def _message(self, sender: str, body: str, to_ref: str | None = None, task_id: str | None = None,
                 ack_required: int = 0) -> str:
        mid = new_id("message")
        self.conn.execute(
            "INSERT INTO messages (id, ts, sender, to_ref, task_id, body, ack_required) VALUES (?,?,?,?,?,?,?)",
            (mid, now(), sender, to_ref, task_id, body, ack_required))
        return mid

    def post_message(self, body: str, to_ref: str | None = None, task_id: str | None = None) -> str:
        with write_txn(self.conn):
            return self._message(self.actor, body, to_ref, task_id)

    def messages(self, task_id: str | None = None, limit: int = 50) -> list[dict]:
        if task_id:
            rows = self.conn.execute("SELECT * FROM messages WHERE task_id=? ORDER BY ts LIMIT ?", (task_id, limit))
        else:
            rows = self.conn.execute("SELECT * FROM messages ORDER BY ts DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
