"""When employees wake up, and recurring plans. One schedule table, one writer.

kind = a duty ('plan' | 'execute' | 'verify' | 'review') performed by an employee, or
'recurring' = fire an approved recurring plan as a fresh, pre-approved task.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from holon.core.db import now, write_txn
from holon.core.ids import new_id
from holon.execution.cron import Cron
from holon.execution.staleness import fingerprint
from holon.execution.work import NotFound, TransitionError, Work

KINDS = ("plan", "execute", "verify", "review", "recurring")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Schedules:
    def __init__(self, conn: sqlite3.Connection, actor: str = "chair"):
        self.conn = conn
        self.work = Work(conn, actor)

    def add(self, kind: str, cron: str, *, employee_id: str | None = None, plan_id: str | None = None) -> dict:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        c = Cron(cron)
        if kind == "recurring":
            plan = self.work.get_plan(plan_id) if plan_id else None
            if plan is None or plan["status"] != "approved" or not plan["recurring"]:
                raise TransitionError("a recurring schedule needs a plan approved with --recurring")
        else:
            row = self.conn.execute("SELECT duties, status FROM employees WHERE id=?", (employee_id,)).fetchone() \
                if employee_id else None
            if row is None or row["status"] == "retired":
                raise NotFound(f"a {kind} schedule needs an active employee")
            if kind not in json.loads(row["duties"]):
                raise ValueError(f"employee {employee_id} does not have the '{kind}' duty")
        sid = new_id("schedule")
        with write_txn(self.conn):
            self.conn.execute(
                "INSERT INTO schedules (id, kind, cron, employee_id, plan_id, next_run_at, created_at) VALUES (?,?,?,?,?,?,?)",
                (sid, kind, cron, employee_id, plan_id, _iso(c.next_after(datetime.now(timezone.utc))), now()))
            self.work._event("schedule", sid, None, "enabled", f"{kind} '{cron}'")
        return self.get(sid)

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

    def instantiate_recurring(self, plan_id: str, label: str | None = None) -> dict:
        """One firing of a recurring plan: a fresh task carrying a copy of the approved plan,
        entering the pipeline at 'approved'. The approval it inherits is recorded."""
        tmpl = self.work.get_plan(plan_id)
        if tmpl["status"] != "approved" or not tmpl["recurring"]:
            raise TransitionError(f"plan {plan_id} is not an approved recurring plan")
        src = self.work.get_task(tmpl["task_id"])
        tid, pid, ts = new_id("task"), new_id("plan"), now()
        with write_txn(self.conn):
            self.conn.execute(
                "INSERT INTO tasks (id, project_ref, dept_id, assignee, requested_by, title, intent, acceptance, priority,"
                " source, task_class, risk, workdir, status, max_attempts, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, src["project_ref"], src["dept_id"], src["assignee"], f"recurring:{plan_id}",
                 f"{src['title']} — {label or ts[:16]}", src["intent"], src["acceptance"], src["priority"], "schedule",
                 src["task_class"], src["risk"], src["workdir"], "approved", src["max_attempts"], ts, ts))
            writes = json.loads(tmpl["writes"] or "[]")
            self.conn.execute(
                "INSERT INTO plans (id, task_id, version, kind, author, findings, writes_fingerprint, steps, writes,"
                " external_effects, assignee, risks, rollback, estimate, context_refs, grade_proposed, grade_floor,"
                " grade, grade_reasons, approver, status, review_note, decided_by, decided_at, created_at)"
                " VALUES (?,?,1,'execute',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'approved',?,?,?,?)",
                (pid, tid, tmpl["author"], tmpl["findings"], fingerprint(src["workdir"], writes), tmpl["steps"],
                 tmpl["writes"], tmpl["external_effects"], tmpl["assignee"], tmpl["risks"], tmpl["rollback"],
                 tmpl["estimate"], tmpl["context_refs"], tmpl["grade_proposed"], tmpl["grade_floor"], tmpl["grade"],
                 tmpl["grade_reasons"], tmpl["approver"], tmpl["review_note"], f"recurring:{plan_id}", ts, ts))
            self.work._event("task", tid, None, "approved", f"recurring plan {plan_id}")
        return self.work.get_task(tid)
