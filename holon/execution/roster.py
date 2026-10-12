"""Agents and schedules (work.db). Replaces employees.json, AgentSpec files, and
legacy jobs.json entries: one roster, one schedule table, one writer."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from holon.core.db import now, write_txn
from holon.core.ids import new_id
from holon.execution.cron import Cron
from holon.execution.staleness import fingerprint
from holon.execution.work import NotFound, TransitionError, Work

ROLES = ("planner", "executor", "verifier")
SCHEDULE_KINDS = ("planner", "executor", "verifier", "recurring")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


class Roster:
    def __init__(self, conn: sqlite3.Connection, actor: str = "human:cli"):
        self.conn = conn
        self.actor = actor
        self.work = Work(conn, actor)

    # ── agents ────────────────────────────────────────────────────────────
    def add_agent(self, role: str, driver: str, *, agent_id: str | None = None, skills: list[str] | None = None,
                  spec: dict | None = None, task_classes: list[str] | None = None, max_concurrency: int = 1) -> dict:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        aid = agent_id or new_id("agent")
        with write_txn(self.conn):
            self.conn.execute(
                "INSERT INTO agents (id, role, driver, skills, spec, task_classes, max_concurrency, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (aid, role, driver, json.dumps(skills) if skills else None, json.dumps(spec) if spec else None,
                 json.dumps(task_classes) if task_classes else None, max_concurrency, now()))
            self.work._event("agent", aid, None, "active", f"{role} via {driver}")
        return self.get_agent(aid)

    def get_agent(self, agent_id: str) -> dict:
        r = self.conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if r is None:
            raise NotFound(f"no agent {agent_id}")
        return dict(r)

    def list_agents(self, role: str | None = None, status: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM agents WHERE 1=1", []
        if role:
            q += " AND role=?"
            args.append(role)
        if status:
            q += " AND status=?"
            args.append(status)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY role, created_at", args)]

    def set_agent_status(self, agent_id: str, status: str, reason: str | None = None) -> dict:
        agent = self.get_agent(agent_id)
        with write_txn(self.conn):
            self.conn.execute("UPDATE agents SET status=?, retired_reason=? WHERE id=?",
                              (status, reason if status == "retired" else agent["retired_reason"], agent_id))
            self.work._event("agent", agent_id, agent["status"], status, reason)
        return self.get_agent(agent_id)

    def running_count(self, agent_id: str) -> int:
        return self.conn.execute("SELECT count(*) FROM runs WHERE agent_id=? AND status='running'",
                                 (agent_id,)).fetchone()[0]

    # ── schedules ─────────────────────────────────────────────────────────
    def add_schedule(self, kind: str, cron: str, *, agent_id: str | None = None, plan_id: str | None = None) -> dict:
        if kind not in SCHEDULE_KINDS:
            raise ValueError(f"kind must be one of {SCHEDULE_KINDS}")
        c = Cron(cron)
        if kind == "recurring":
            plan = self.work.get_plan(plan_id) if plan_id else None
            if plan is None or plan["status"] != "approved" or not plan["recurring"]:
                raise TransitionError("a recurring schedule needs a plan approved with --recurring")
        else:
            agent = self.get_agent(agent_id) if agent_id else None
            if agent is None or agent["role"] != kind:
                raise ValueError(f"a {kind} schedule needs an agent with role {kind}")
        sid = new_id("schedule")
        with write_txn(self.conn):
            self.conn.execute(
                "INSERT INTO schedules (id, kind, cron, agent_id, plan_id, next_run_at, created_at) VALUES (?,?,?,?,?,?,?)",
                (sid, kind, cron, agent_id, plan_id, _iso(c.next_after(datetime.now(timezone.utc))), now()))
            self.work._event("schedule", sid, None, "enabled", f"{kind} '{cron}'")
        return self.get_schedule(sid)

    def get_schedule(self, sid: str) -> dict:
        r = self.conn.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
        if r is None:
            raise NotFound(f"no schedule {sid}")
        return dict(r)

    def list_schedules(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM schedules ORDER BY kind, created_at")]

    def set_schedule_enabled(self, sid: str, enabled: bool) -> dict:
        s = self.get_schedule(sid)
        with write_txn(self.conn):
            nxt = _iso(Cron(s["cron"]).next_after(datetime.now(timezone.utc))) if enabled else s["next_run_at"]
            self.conn.execute("UPDATE schedules SET enabled=?, next_run_at=? WHERE id=?", (1 if enabled else 0, nxt, sid))
            self.work._event("schedule", sid, None, "enabled" if enabled else "disabled")
        return self.get_schedule(sid)

    def take_due(self, at: datetime | None = None) -> list[dict]:
        """Atomically advance every due schedule to its next fire time and return them."""
        at = at or datetime.now(timezone.utc)
        due = []
        with write_txn(self.conn):
            for s in self.conn.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run_at <= ?",
                                       (_iso(at),)).fetchall():
                nxt = Cron(s["cron"]).next_after(at)
                self.conn.execute("UPDATE schedules SET last_run_at=?, next_run_at=? WHERE id=?",
                                  (_iso(at), _iso(nxt), s["id"]))
                due.append(dict(s))
        return due

    def record_schedule_outcome(self, sid: str, ok: bool) -> None:
        with write_txn(self.conn):
            if ok:
                self.conn.execute("UPDATE schedules SET failure_streak=0 WHERE id=?", (sid,))
            else:
                self.conn.execute("UPDATE schedules SET failure_streak=failure_streak+1 WHERE id=?", (sid,))

    # ── recurring plans ────────────────────────────────────────────────────
    def instantiate_recurring(self, plan_id: str, label: str | None = None) -> dict:
        """One firing of a recurring plan: a fresh task with a copy of the approved plan,
        entering the pipeline at 'approved'. The approval it inherits is recorded."""
        tmpl = self.work.get_plan(plan_id)
        if tmpl["status"] != "approved" or not tmpl["recurring"]:
            raise TransitionError(f"plan {plan_id} is not an approved recurring plan")
        src = self.work.get_task(tmpl["task_id"])
        tid, pid, ts = new_id("task"), new_id("plan"), now()
        title = f"{src['title']} — {label or ts[:16]}"
        with write_txn(self.conn):
            self.conn.execute(
                "INSERT INTO tasks (id, project_ref, title, intent, acceptance, priority, source, task_class, risk,"
                " workdir, status, max_attempts, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, src["project_ref"], title, src["intent"], src["acceptance"], src["priority"], "schedule",
                 src["task_class"], src["risk"], src["workdir"], "approved", src["max_attempts"], ts, ts))
            writes = json.loads(tmpl["writes"] or "[]")
            self.conn.execute(
                "INSERT INTO plans (id, task_id, version, kind, planner_agent, findings, writes_fingerprint, steps,"
                " writes, risks, rollback, estimate, context_refs, recurring, status, review_note, decided_by,"
                " decided_at, created_at) VALUES (?,?,1,'execute',?,?,?,?,?,?,?,?,?,0,'approved',?,?,?,?)",
                (pid, tid, tmpl["planner_agent"], tmpl["findings"], fingerprint(src["workdir"], writes), tmpl["steps"],
                 tmpl["writes"], tmpl["risks"], tmpl["rollback"], tmpl["estimate"], tmpl["context_refs"],
                 tmpl["review_note"], f"recurring:{plan_id}", ts, ts))
            self.work._event("task", tid, None, "approved", f"recurring plan {plan_id}")
        return self.work.get_task(tid)
