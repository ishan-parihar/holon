"""When employees wake up, and recurring work. One schedule table, one writer.

kind = 'wake'      an employee wakes and claims the next ready stage its roles allow (optionally one stage)
       'recurring' fire an approved recurring plan as fresh work that starts at execute
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from holon.core import vocab
from holon.core.db import now, write_txn
from holon.core.ids import new_id
from holon.execution.cron import Cron
from holon.execution.work import NotFound, TransitionError, Work

# The stages each lane's firing needs an active employee (besides the kernel) to perform. The
# script lane needs nobody: the kernel is its runner (2026-10-25 red-team).
LANE_STAGES = {"script": (), "fast": ("execute",), "full": ("plan", "execute", "verify")}


def unstaffed_stages(conn: sqlite3.Connection, dept_id: str | None, lane: str) -> set[str]:
    """The stages a lane's firing needs that no active employee in reach performs. Mirrors the
    claim's reach (`work.claim`'s pool branch) exactly: a claimant may pick up work in their OWN
    department, and an executive claimant additionally reaches no-dept work (every stage) and
    verify stages of any department — but NOT another department's plan or execute stages. One
    predicate shared by the proposal note, the enable gate and doctor's backstop — three
    consumers, or they would disagree."""
    need = set(LANE_STAGES.get(lane, LANE_STAGES["full"]))
    if not need:
        return set()
    exec_dept = conn.execute("SELECT id FROM departments WHERE slug=?", (vocab.EXECUTIVE,)).fetchone()
    covered: set[str] = set()
    for r in conn.execute("SELECT e.dept_id, e.roles FROM employees e WHERE e.status IN ('active','probation')"):
        own = r[0] == dept_id
        executive_reach = exec_dept and r[0] == exec_dept[0] and (dept_id is None or True)
        if not own and not executive_reach:
            continue
        stages = {s for role in json.loads(r[1] or "[]") for s in vocab.ROLE_STAGES.get(role, ())}
        if own:
            covered |= need & stages
        else:
            # an executive claimant reaches another department's verify stages only, and every
            # stage of no-dept work (the claim's pool branch, verbatim)
            covered |= need & ({"verify"} if dept_id is not None else stages)
    return need - covered


def staffing_note(conn: sqlite3.Connection, dept_id: str | None, lane: str) -> str:
    """The gate's one refusal message, shared by every consumer — a message that names the act
    that fixes it (the remediation-class lesson: a refusal must name a command that works)."""
    slug = conn.execute("SELECT slug FROM departments WHERE id=?", (dept_id,)).fetchone()
    missing = ", ".join(sorted(unstaffed_stages(conn, dept_id, lane)))
    return (f"no employee in {slug[0] if slug else dept_id} performs {missing}: the firing will wait with "
            f"nobody to claim it (hire: `holon hr hire --dept {slug[0] if slug else dept_id} --slot maker "
            "--driver <driver> …`, or --force for the Chair's explicit word)")

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
               template_id: str | None = None, event: str | None = None) -> str:
        """`add` inside the caller's transaction (an approval that creates a routine's schedule).
        `event` (H7): an event trigger — no cron, no clock; the runtime that sees a matching
        event fires it (`holon routine fire`), at most once per [events] cooldown_s."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if event:
            if kind != "recurring":
                raise ValueError("only a recurring schedule can fire on an event")
            cron = ""
        c = Cron(cron) if cron else None
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
            "INSERT INTO schedules (id, kind, cron, employee_id, stage, template_id, next_run_at, event, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, kind, cron, employee_id, stage, template_id,
             _iso(c.next_after(datetime.now(timezone.utc))) if c else None, event, now()))
        self.work._event("schedule", sid, None, "enabled", f"{kind} '{cron or event}'")
        return sid

    def get(self, sid: str) -> dict:
        r = self.conn.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
        if r is None:
            raise NotFound(f"no schedule {sid}")
        return dict(r)

    def list(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM schedules ORDER BY kind, created_at")]

    def set_enabled(self, sid: str, enabled: bool, *, force: bool = False) -> dict:
        s = self.get(sid)
        # The enable gate (2026-10-25 red-team): a recurring schedule whose lane needs a body,
        # in a department where no active employee performs the stages it needs, does not light.
        # Measured on a real imported instance: three departments, 26 pending routine approvals,
        # zero employees — `schedule enable` set next_run_at with no resistance, and the first
        # firing would have sat `ready` forever with nobody to claim it, invisible to every
        # surface until then. The script lane is exempt (the kernel is its runner), and the
        # Chair's --force is the escape, recorded in the event log.
        if enabled and not force and s["kind"] == "recurring":
            plan = self.work.get(s["template_id"])
            work = self.work.get(plan["parent_id"])
            body = (self.work.output(plan["id"]) or {"body": {}})["body"]
            if unstaffed_stages(self.conn, work["dept_id"], body.get("lane") or "full"):
                raise ValueError(staffing_note(self.conn, work["dept_id"], body.get("lane") or "full"))
        with write_txn(self.conn):
            nxt = (_iso(Cron(s["cron"]).next_after(datetime.now(timezone.utc)))
                   if enabled and s["cron"] else s["next_run_at"])
            self.conn.execute("UPDATE schedules SET enabled=?, next_run_at=? WHERE id=?", (1 if enabled else 0, nxt, sid))
            self.work._event("schedule", sid, None, "enabled" if enabled else "disabled",
                             "unstaffed lane enabled by the Chair's --force" if enabled and force and s["kind"] == "recurring"
                             and unstaffed_stages(self.conn, self._plan_dept(s), self._plan_lane(s)) else None)
        return self.get(sid)

    def _plan_dept(self, s: dict) -> str | None:
        return self.work.get(self.work.get(s["template_id"])["parent_id"])["dept_id"]

    def _plan_lane(self, s: dict) -> str:
        plan = self.work.get(s["template_id"])
        body = (self.work.output(plan["id"]) or {"body": {}})["body"]
        return body.get("lane") or "full"

    def arm_cooldown(self, sid: str, seconds: int) -> None:
        """An event trigger fired: it will not fire again until the cooldown passes (next_run_at is
        its clock; take_due ignores event rows, so it never fires by time)."""
        at = datetime.now(timezone.utc)
        with write_txn(self.conn):
            self.conn.execute("UPDATE schedules SET last_run_at=?, next_run_at=? WHERE id=?",
                              (_iso(at), _iso(at + timedelta(seconds=seconds)), sid))

    def take_due(self, at: datetime | None = None) -> list[dict]:
        """Atomically advance every due schedule to its next fire time and return them.
        Event triggers have no clock — they fire through `holon routine fire`, never by time."""
        at = at or datetime.now(timezone.utc)
        due = []
        with write_txn(self.conn):
            for s in self.conn.execute("SELECT * FROM schedules WHERE enabled=1 AND event IS NULL AND next_run_at <= ?",
                                        (_iso(at),)).fetchall():
                self.conn.execute("UPDATE schedules SET last_run_at=?, next_run_at=? WHERE id=?",
                                  (_iso(at), _iso(Cron(s["cron"]).next_after(at)), s["id"]))
                due.append(dict(s))
        return due

    def record_outcome(self, sid: str, ok: bool) -> None:
        with write_txn(self.conn):
            self.conn.execute("UPDATE schedules SET failure_streak=CASE WHEN ? THEN 0 ELSE failure_streak+1 END "
                              "WHERE id=?", (1 if ok else 0, sid))
