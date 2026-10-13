"""When employees wake up, and recurring work. One schedule table, one writer.

kind = 'wake'      an employee wakes and claims the next ready stage its roles allow (optionally one stage)
       'recurring' fire an approved recurring plan as fresh work that starts at execute
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from holon.core import vocab
from holon.core.db import now, write_txn
from holon.core.ids import new_id
from holon.execution.cron import Cron
from holon.execution.work import NotFound, TransitionError, Work

KINDS = vocab.SCHEDULE_KINDS


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Schedules:
    def __init__(self, conn: sqlite3.Connection, actor: str = "chair"):
        self.conn = conn
        self.work = Work(conn, actor)

    def add(self, kind: str, cron: str, *, employee_id: str | None = None, stage: str | None = None,
            template_id: str | None = None) -> dict:
        with write_txn(self.conn):
            sid = self.insert(kind, cron, employee_id=employee_id, stage=stage, template_id=template_id)
        return self.get(sid)

    def insert(self, kind: str, cron: str, *, employee_id: str | None = None, stage: str | None = None,
               template_id: str | None = None) -> str:
        """`add` inside the caller's transaction (an approval that creates a routine's schedule)."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        c = Cron(cron)
        if kind == "recurring":
            t = self.work.get(template_id) if template_id else None
            if t is None or t["stage"] != "plan" or not t["recurring"]:
                raise TransitionError("a recurring schedule needs a plan approved with --recurring")
        else:
            row = self.conn.execute("SELECT roles, status FROM employees WHERE id=?", (employee_id,)).fetchone() \
                if employee_id else None
            if row is None or row["status"] == "retired":
                raise NotFound("a wake schedule needs an active employee")
            stages = {s for r in json.loads(row["roles"] or "[]") for s in vocab.ROLE_STAGES[r]}
            if stage and stage not in stages:
                raise ValueError(f"employee {employee_id} has no role that performs the '{stage}' stage")
        sid = new_id("schedule")
        self.conn.execute(
            "INSERT INTO schedules (id, kind, cron, employee_id, stage, template_id, next_run_at, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (sid, kind, cron, employee_id, stage, template_id, _iso(c.next_after(datetime.now(timezone.utc))), now()))
        self.work._event("schedule", sid, None, "enabled", f"{kind} '{cron}'")
        return sid

    def get(self, sid: str) -> dict:
        r = self.conn.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
        if r is None:
            raise NotFound(f"no schedule {sid}")
        return dict(r)

    def list(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM schedules ORDER BY kind, created_at")]

    def set_enabled(self, sid: str, enabled: bool) -> dict:
        s = self.get(sid)
        with write_txn(self.conn):
            nxt = _iso(Cron(s["cron"]).next_after(datetime.now(timezone.utc))) if enabled else s["next_run_at"]
            self.conn.execute("UPDATE schedules SET enabled=?, next_run_at=? WHERE id=?", (1 if enabled else 0, nxt, sid))
            self.work._event("schedule", sid, None, "enabled" if enabled else "disabled")
        return self.get(sid)

    def take_due(self, at: datetime | None = None) -> list[dict]:
        """Atomically advance every due schedule to its next fire time and return them."""
        at = at or datetime.now(timezone.utc)
        due = []
        with write_txn(self.conn):
            for s in self.conn.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run_at <= ?",
                                       (_iso(at),)).fetchall():
                self.conn.execute("UPDATE schedules SET last_run_at=?, next_run_at=? WHERE id=?",
                                  (_iso(at), _iso(Cron(s["cron"]).next_after(at)), s["id"]))
                due.append(dict(s))
        return due

    def record_outcome(self, sid: str, ok: bool) -> None:
        with write_txn(self.conn):
            self.conn.execute("UPDATE schedules SET failure_streak=CASE WHEN ? THEN 0 ELSE failure_streak+1 END "
                              "WHERE id=?", (1 if ok else 0, sid))
