"""work.db: the holarchic work graph. The only writer of tasks, task_needs, outputs and runs.

Every node is a task.
  work node   (stage IS NULL)  an outcome someone wants: blocked → open → (waiting) → done | failed | cancelled
  stage node  (stage = a role's step)  one unit of work by one role: ready → claimed → done | failed | cancelled

A work node's pipeline is the chain of its stage nodes, created one at a time as each finishes:

    research? → shape? → plan → approve → execute → verify      (retro on failure or rework)

`parent_id` is containment (the holarchy: a plan may decompose work into child work nodes, in any
department); `task_needs` is order (a node is ready only when everything it needs is done).
History is append-only: rework adds `execute#2`, a rejected plan adds `plan#2`.

Who may claim a stage (see `Claimant`): its assignee; else anyone in its department holding the
stage's role; executive members take department-less work. `approve` stages are claimable only by
their approver; `verify` never by the stage's executor. Genome decisions (which optional stages
run) come from `rules`, supplied by the kernel.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from holon.core import vocab
from holon.core.db import log_event, now, write_txn
from holon.core.ids import new_id
from holon.execution.staleness import fingerprint

TERMINAL = set(vocab.TERMINAL)


class TransitionError(RuntimeError):
    pass


class NotFound(LookupError):
    pass


@dataclass
class Claimant:
    """Who is claiming. With no id: an unaffiliated pull-mode worker that may claim any ready stage."""
    id: str | None = None
    dept_id: str | None = None
    is_head: bool = False
    is_ceo: bool = False
    in_executive: bool = False
    stages: tuple[str, ...] = vocab.STAGES

    @property
    def ref(self) -> str | None:
        return f"emp:{self.id}" if self.id else None


class Rules:
    """Genome decisions the graph asks about. The kernel subclasses this with the department's
    profile and track record; the defaults run the minimal pipeline plan → approve → execute → verify."""

    def first_stage(self, work: dict) -> str:
        return "plan"

    def after_research(self, work: dict) -> str:
        return "plan"

    def after_shape(self, work: dict, research_requested: bool) -> str:
        return "research" if research_requested else "plan"

    def on_finished(self, work: dict) -> None:
        """Called (inside the transaction) when a work node reaches done, failed or cancelled."""

    def on_rework(self, work: dict, stage: dict, why: str) -> None:
        """Called when a stage's result sends work back (failed verification, rejection, failed execution)."""


def _lease(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def _dumps(v: Any) -> str | None:
    if v is None:
        return None
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


class Work:
    def __init__(self, conn: sqlite3.Connection, actor: str = vocab.CHAIR, rules: Rules | None = None):
        self.conn = conn
        self.actor = actor
        self.rules = rules or Rules()
        # kernel-applied decisions: a proposal's plan output carries {"apply": {"kind": k, ...}}; approving
        # it calls appliers[k](payload) inside the approval transaction, so decision and effect commit together
        self.appliers: dict[str, Callable[[dict], None]] = {}

    # ── primitives ────────────────────────────────────────────────────────
    def _event(self, entity: str, entity_id: str, frm: str | None, to: str | None, note: str | None = None,
               actor: str | None = None) -> None:
        log_event(self.conn, entity, entity_id, frm, to, actor or self.actor or "kernel", note)

    def _set(self, node_id: str, to: str, note: str | None = None, actor: str | None = None, **cols: Any) -> dict:
        node = self.get(node_id)
        allowed = vocab.STAGE_STATUSES if node["stage"] else vocab.WORK_STATUSES
        if to not in allowed:
            raise TransitionError(f"{to!r} is not a {'stage' if node['stage'] else 'work'} status")
        if node["status"] in TERMINAL and to != node["status"]:
            raise TransitionError(f"{node_id} is already {node['status']}")
        sets = "".join(f", {k}=?" for k in cols)
        self.conn.execute(f"UPDATE tasks SET status=?, updated_at=?{sets} WHERE id=?",
                          (to, now(), *cols.values(), node_id))
        self._event("task", node_id, node["status"], to, note, actor)
        node.update(status=to, **cols)
        return node

    def _dept_id(self, ref: str | None) -> str | None:
        if not ref:
            return None
        r = self.conn.execute("SELECT id FROM departments WHERE id=? OR slug=?", (ref, ref)).fetchone()
        if r is None:
            raise NotFound(f"no department {ref}")
        return r["id"]

    # ── reads ─────────────────────────────────────────────────────────────
    def get(self, node_id: str) -> dict:
        t = _row(self.conn.execute("SELECT * FROM tasks WHERE id=?", (node_id,)).fetchone())
        if t is None:
            raise NotFound(f"no task {node_id}")
        return t

    get_task = get

    def work_of(self, node: dict | str) -> dict:
        node = self.get(node) if isinstance(node, str) else node
        return node if node["stage"] is None else self.get(node["parent_id"])

    def get_run(self, run_id: str) -> dict:
        r = _row(self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
        if r is None:
            raise NotFound(f"no run {run_id}")
        return r

    def stages(self, work_id: str, stage: str | None = None, status: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM tasks WHERE parent_id=? AND stage IS NOT NULL", [work_id]
        if stage:
            q += " AND stage=?"
            args.append(stage)
        if status:
            q += " AND status=?"
            args.append(status)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY created_at, rowid", args)]

    def latest(self, work_id: str, stage: str, status: str | None = None) -> dict | None:
        s = self.stages(work_id, stage, status)
        return s[-1] if s else None

    def children(self, work_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM tasks WHERE parent_id=? AND stage IS NULL ORDER BY created_at, rowid", (work_id,))]

    def needs(self, node_id: str) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT needs_id FROM task_needs WHERE task_id=?", (node_id,))]

    def output(self, node_id: str) -> dict | None:
        o = _row(self.conn.execute("SELECT * FROM outputs WHERE task_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                                   (node_id,)).fetchone())
        if o:
            o["body"] = json.loads(o["body"])
        return o

    def approved_plan(self, work_id: str) -> dict | None:
        """The latest plan stage whose approval succeeded, with its output and approval."""
        for a in reversed(self.stages(work_id, "approve", "done")):
            if a["outcome"] == "approve":
                plan = self.get(self.needs(a["id"])[0])
                return {**plan, "output": self.output(plan["id"]), "approval": a}
        return None

    def pending_approval(self, work_id: str) -> dict | None:
        return self.latest(work_id, "approve", "ready") or self.latest(work_id, "approve", "claimed")

    def plan_for_approval(self, approve_id: str) -> dict:
        plan = self.get(self.needs(approve_id)[0])
        return {**plan, "output": self.output(plan["id"])}

    def resolve_approval(self, ref: str) -> dict:
        """An approve stage from its own id, its plan stage's id, or its work node's id."""
        node = self.get(ref)
        if node["stage"] == "approve":
            return node
        if node["stage"] == "plan":
            r = self.conn.execute("SELECT t.* FROM task_needs n JOIN tasks t ON t.id=n.task_id "
                                  "WHERE n.needs_id=? AND t.stage='approve'", (ref,)).fetchone()
            if r:
                return dict(r)
        if node["stage"] is None:
            p = self.pending_approval(node["id"])
            if p:
                return p
        raise NotFound(f"{ref} has no pending approval")

    def last_execute_output(self, work_id: str) -> dict | None:
        for s in reversed(self.stages(work_id, "execute")):
            o = self.output(s["id"])
            if o:
                return {**o, "stage": s}
        return None

    def executor_of(self, work_id: str) -> str | None:
        r = self.conn.execute("SELECT r.employee_id FROM runs r JOIN tasks t ON t.id=r.task_id "
                              "WHERE t.parent_id=? AND t.stage='execute' ORDER BY r.started_at DESC LIMIT 1",
                              (work_id,)).fetchone()
        return r[0] if r else None

    def list(self, *, kind: str = "work", status: str | None = None, stage: str | None = None,
             dept_id: str | None = None, assignee: str | None = None, approver: str | None = None,
             parent_id: str | None = None, root_id: str | None = None, project_ref: str | None = None,
             limit: int = 50) -> list[dict]:
        q, args = "SELECT * FROM tasks WHERE 1=1", []
        if kind == "work":
            q += " AND stage IS NULL"
        elif kind == "stage":
            q += " AND stage IS NOT NULL"
        if status:
            q += " AND status IN (%s)" % ",".join("?" * len(status.split(",")))
            args += status.split(",")
        for col, val in (("stage", stage), ("dept_id", dept_id), ("assignee", assignee), ("approver", approver),
                         ("parent_id", parent_id), ("root_id", root_id), ("project_ref", project_ref)):
            if val:
                q += f" AND {col}=?"
                args.append(val)
        q += " ORDER BY priority, created_at, rowid LIMIT ?"
        return [dict(r) for r in self.conn.execute(q, [*args, limit])]

    def awaiting_approval(self, approver: str | None = vocab.CHAIR) -> list[dict]:
        q = ("SELECT a.*, w.title AS work_title FROM tasks a JOIN tasks w ON w.id=a.parent_id "
             "WHERE a.stage='approve' AND a.status IN ('ready','claimed')")
        args: list = []
        if approver:
            q += " AND a.approver=?"
            args.append(approver)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY a.grade DESC, a.priority, a.created_at", args)]

    def events(self, entity_id: str | None = None, limit: int = 50) -> list[dict]:
        if entity_id:
            rows = self.conn.execute("SELECT * FROM events WHERE entity_id=? ORDER BY seq DESC LIMIT ?", (entity_id, limit))
        else:
            rows = self.conn.execute("SELECT * FROM events ORDER BY seq DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def counts(self) -> dict[str, dict[str, int]]:
        work = {r[0]: r[1] for r in self.conn.execute(
            "SELECT status, count(*) FROM tasks WHERE stage IS NULL GROUP BY status")}
        ready = {r[0]: r[1] for r in self.conn.execute(
            "SELECT stage, count(*) FROM tasks WHERE stage IS NOT NULL AND status='ready' GROUP BY stage")}
        return {"work": work, "ready_stages": ready}

    def ancestry(self, node_id: str) -> list[dict]:
        chain, cur = [], self.work_of(node_id)
        while cur.get("parent_id"):
            cur = self.get(cur["parent_id"])
            chain.append(cur)
        return chain

    # ── intake ────────────────────────────────────────────────────────────
    def add_work(self, title: str, intent: str, **kw: Any) -> dict:
        with write_txn(self.conn):
            w = self._add_work(title, intent, **kw)
        return self.get(w["id"])

    def _add_work(self, title: str, intent: str, *, acceptance=None, project_ref=None, parent_id=None, dept=None,
                  assignee=None, requested_by=None, priority=3, source=vocab.CHAIR, task_class=None, risk="normal",
                  workdir=None, due=None, max_attempts=2, needs=None, expand=True) -> dict:
        if not title or not title.strip() or not intent or not intent.strip():
            raise ValueError("title and intent are required")
        if source not in vocab.SOURCES:
            raise ValueError(f"source must be one of {vocab.SOURCES}")
        if risk not in vocab.RISKS:
            raise ValueError(f"risk must be one of {vocab.RISKS}")
        if priority not in vocab.PRIORITIES:
            raise ValueError(f"priority must be one of {vocab.PRIORITIES}")
        parent = self.get(parent_id) if parent_id else None
        if parent and parent["stage"]:
            raise TransitionError("work can only be nested under work, not under a stage")
        dept_id = self._dept_id(dept)
        if assignee:
            emp = self.conn.execute("SELECT dept_id, status FROM employees WHERE id=?", (assignee,)).fetchone()
            if emp is None or emp["status"] == "retired":
                raise NotFound(f"no active employee {assignee}")
            dept_id = dept_id or emp["dept_id"]
        tid, ts = new_id("task"), now()
        needs = needs or []
        pending = [n for n in needs if self.get(n)["status"] != "done"]
        status = "blocked" if pending else "open"
        self.conn.execute(
            "INSERT INTO tasks (id, root_id, parent_id, depth, dept_id, assignee, requested_by, source, title, intent,"
            " acceptance, task_class, project_ref, workdir, priority, risk, status, max_attempts, due, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, parent["root_id"] if parent else tid, parent_id, (parent["depth"] + 1) if parent else 0, dept_id,
             assignee, requested_by or self.actor, source, title.strip(), intent.strip(), acceptance, task_class,
             project_ref or (parent["project_ref"] if parent else None),
             workdir or (parent["workdir"] if parent else None), priority, risk, status, max_attempts, due, ts, ts))
        for n in needs:
            self.conn.execute("INSERT INTO task_needs VALUES (?,?)", (tid, n))
        self._event("task", tid, None, status, f"work from {source}" + (f", blocked on {len(pending)}" if pending else ""))
        work = self.get(tid)
        if status == "open" and expand:
            self._expand(work)
        return work

    def _expand(self, work: dict) -> dict:
        return self._add_stage(work, self.rules.first_stage(work), [])

    def _add_stage(self, work: dict, stage: str, needs: list[str], *, assignee: str | None = None,
                   approver: str | None = None, attempt: int = 1, **cols: Any) -> dict:
        if stage not in vocab.STAGES:
            raise ValueError(f"unknown stage {stage}")
        sid, ts = new_id("task"), now()
        n = len(self.stages(work["id"], stage)) + 1
        if assignee is None and work["assignee"] and self._performs(work["assignee"], stage):
            assignee = work["assignee"]  # the work's assignee takes every stage their roles perform
        elif assignee and not self._performs(assignee, stage):
            assignee = None  # e.g. a planner named as executor: leave it to the department
        self.conn.execute(
            "INSERT INTO tasks (id, root_id, parent_id, depth, stage, dept_id, assignee, approver, requested_by, source,"
            " title, intent, acceptance, task_class, project_ref, workdir, priority, risk, status, attempt,"
            " max_attempts, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, work["root_id"], work["id"], work["depth"] + 1, stage, work["dept_id"], assignee, approver,
             work["requested_by"], "kernel", f"{stage}{'#' + str(n) if n > 1 else ''}: {work['title']}",
             work["intent"], work["acceptance"], work["task_class"], work["project_ref"], work["workdir"],
             work["priority"], work["risk"], "ready", attempt, work["max_attempts"], ts, ts))
        for x in needs:
            self.conn.execute("INSERT INTO task_needs VALUES (?,?)", (sid, x))
        if cols:
            self.conn.execute(f"UPDATE tasks SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*cols.values(), sid))
        self._event("task", sid, None, "ready", f"{stage} stage of {work['id']}")
        return self.get(sid)

    def _performs(self, employee_id: str, stage: str) -> bool:
        r = self.conn.execute("SELECT roles FROM employees WHERE id=?", (employee_id,)).fetchone()
        return bool(r) and vocab.STAGE_ROLE[stage] in json.loads(r["roles"] or "[]")

    def _output(self, node_id: str, run_id: str | None, kind: str, body: dict, summary: str | None = None,
                documentation: str | None = None) -> str:
        oid = new_id("output")
        self.conn.execute("INSERT INTO outputs (id, task_id, run_id, kind, body, summary, documentation, created_at)"
                          " VALUES (?,?,?,?,?,?,?,?)",
                          (oid, node_id, run_id, kind, json.dumps(body, ensure_ascii=False), summary, documentation,
                           now()))
        return oid

    # ── claiming ──────────────────────────────────────────────────────────
    def claim(self, who: Claimant, stages: tuple[str, ...] | list[str] | None = None, driver: str | None = None,
              task_id: str | None = None, token_hash: str | None = None,
              exclude_depts: list[str] | None = None, max_running: int | None = None,
              exclude_tasks: list[str] | None = None) -> tuple[dict, dict] | None:
        """Atomically claim the next ready stage this employee may do. A stale execute stage (its plan's
        written paths changed since approval) is cancelled and a fresh plan stage queued instead.
        `max_running`: refuse if the employee already has that many runs in flight (checked atomically)."""
        stages = [s for s in (stages or who.stages) if s in who.stages]
        if not stages:
            return None
        while True:
            with write_txn(self.conn):
                if max_running is not None and who.id and self.conn.execute(
                        "SELECT count(*) FROM runs WHERE employee_id=? AND status='running'",
                        (who.id,)).fetchone()[0] >= max_running:
                    return None
                q = f"SELECT t.* FROM tasks t WHERE t.status='ready' AND t.stage IN ({','.join('?' * len(stages))})"
                args: list = list(stages)
                if who.id:
                    q += (" AND ((t.stage='approve' AND t.approver=?) OR (t.stage<>'approve' AND (t.assignee=? OR"
                          " (t.assignee IS NULL AND (t.dept_id=? OR (t.dept_id IS NULL AND ?)"
                          "  OR (t.stage='verify' AND ?))))))"
                          " AND NOT (t.stage='verify' AND EXISTS (SELECT 1 FROM task_needs n JOIN runs r"
                          "  ON r.task_id=n.needs_id WHERE n.task_id=t.id AND r.kind='execute' AND r.employee_id=?))")
                    args += [who.ref, who.id, who.dept_id, 1 if who.in_executive else 0,
                             1 if who.in_executive else 0, who.id]
                else:
                    q += " AND t.stage<>'approve'"
                if task_id:
                    q += " AND (t.id=? OR t.parent_id=?)"
                    args += [task_id, task_id]
                if exclude_depts:
                    q += f" AND coalesce(t.dept_id, '') NOT IN ({','.join('?' * len(exclude_depts))})"
                    args += list(exclude_depts)
                if exclude_tasks:
                    q += f" AND t.id NOT IN ({','.join('?' * len(exclude_tasks))})"
                    args += list(exclude_tasks)
                row = self.conn.execute(q + " ORDER BY t.priority, t.created_at, t.rowid LIMIT 1", args).fetchone()
                if row is None:
                    return None
                node = dict(row)
                if node["stage"] == "execute" and self._stale(node):
                    work = self.get(node["parent_id"])
                    self._set(node["id"], "cancelled", "stale plan: written paths changed since approval")
                    self._add_stage(work, "plan", [node["id"]], blocked_reason="stale_plan")
                    self.rules.on_rework(work, node, "stale plan")
                    if task_id:
                        return None
                    continue
                node = self._set(node["id"], "claimed", f"claimed by {who.ref or self.actor}", actor=who.ref)
                run = self._open_run(node, who, driver, token_hash)
            return node, run

    def _stale(self, execute: dict) -> bool:
        plan = self.approved_plan(execute["parent_id"])
        if not plan or not plan["output"]:
            return False
        body = plan["output"]["body"]
        if not body.get("writes_fingerprint"):
            return False
        return fingerprint(execute["workdir"], body.get("writes") or []) != body["writes_fingerprint"]

    def _open_run(self, node: dict, who: Claimant, driver: str | None, token_hash: str | None) -> dict:
        rid, ts = new_id("run"), now()
        self.conn.execute(
            "INSERT INTO runs (id, task_id, kind, employee_id, driver, attempt, lease_until, heartbeat_at, started_at,"
            " token_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, node["id"], node["stage"], who.id, driver, node["attempt"], _lease(vocab.LEASE_S[node["stage"]]),
             ts, ts, token_hash))
        self._event("run", rid, None, "running", f"{node['stage']} {node['id']}", actor=who.ref or self.actor)
        return self.get_run(rid)

    def _close_run(self, run_id: str, status: str, exit_code: int | None = None, **metrics: Any) -> dict:
        run = self.get_run(run_id)
        if run["status"] != "running":
            raise TransitionError(f"run {run_id} is already '{run['status']}'")
        self.conn.execute(
            "UPDATE runs SET status=?, ended_at=?, exit_code=?, tokens_in=coalesce(?, tokens_in),"
            " tokens_out=coalesce(?, tokens_out), cost=coalesce(?, cost) WHERE id=?",
            (status, now(), exit_code, metrics.get("tokens_in"), metrics.get("tokens_out"), metrics.get("cost"), run_id))
        self._event("run", run_id, "running", status, actor=f"emp:{run['employee_id']}" if run["employee_id"] else None)
        return run

    def heartbeat(self, run_id: str) -> dict:
        run = self.get_run(run_id)
        if run["status"] != "running":
            raise TransitionError(f"run {run_id} is '{run['status']}'")
        with write_txn(self.conn):
            self.conn.execute("UPDATE runs SET heartbeat_at=?, lease_until=? WHERE id=?",
                              (now(), _lease(vocab.LEASE_S[run["kind"]]), run_id))
        return self.get_run(run_id)

    def set_run_paths(self, run_id: str, brief_path: str, log_path: str) -> None:
        with write_txn(self.conn):
            self.conn.execute("UPDATE runs SET brief_path=?, log_path=? WHERE id=?", (brief_path, log_path, run_id))

    def set_run_usage(self, run_id: str, tokens_in: int | None, tokens_out: int | None, cost: float | None) -> None:
        """What the harness reported a run consumed (budgets and cost per verified result read it)."""
        with write_txn(self.conn):
            self.conn.execute("UPDATE runs SET tokens_in=coalesce(?, tokens_in), tokens_out=coalesce(?, tokens_out),"
                              " cost=coalesce(?, cost) WHERE id=?", (tokens_in, tokens_out, cost, run_id))

    def _begin_submit(self, run_id: str, stage: str, exit_code: int | None, status: str = "succeeded",
                      **metrics: Any) -> tuple[dict, dict, dict, str | None]:
        run = self.get_run(run_id)
        if run["kind"] != stage:
            raise TransitionError(f"run {run_id} is a {run['kind']} run, not {stage}")
        node = self.get(run["task_id"])
        if node["status"] != "claimed":
            raise TransitionError(f"{node['id']} is '{node['status']}', not claimed")
        self._close_run(run_id, status, exit_code, **metrics)
        actor = f"emp:{run['employee_id']}" if run["employee_id"] else None
        return run, node, self.get(node["parent_id"]), actor

    # ── stage results ─────────────────────────────────────────────────────
    def submit_research(self, run_id: str, env: dict, exit_code: int | None = 0) -> dict:
        with write_txn(self.conn):
            run, node, work, actor = self._begin_submit(run_id, "research", exit_code)
            self._output(node["id"], run_id, "research", env, env.get("summary"))
            self._set(node["id"], "done", "terrain mapped", actor, outcome="mapped")
            nxt = self._add_stage(work, self.rules.after_research(work), [node["id"]])
        return {"node": self.get(node["id"]), "work": self.get(work["id"]), "next": nxt}

    def submit_shape(self, run_id: str, env: dict, exit_code: int | None = 0) -> dict:
        with write_txn(self.conn):
            run, node, work, actor = self._begin_submit(run_id, "shape", exit_code)
            self._output(node["id"], run_id, "shape", env, env.get("note"))
            decision = env["decision"]
            self._set(node["id"], "done", f"shaped: {decision}", actor, outcome=decision)
            nxt = None
            if decision == "cancel":
                self._finish(work, "cancelled", f"cancelled when shaped: {env.get('note')}", actor)
            else:
                updates = {k: env[k] for k in ("title", "intent", "acceptance", "task_class", "risk", "priority",
                                               "assignee") if env.get(k) not in (None, "")}
                if decision == "redirect":
                    updates["dept_id"] = self._dept_id(env["dept"])
                    updates["assignee"] = None
                if updates:
                    self.conn.execute(f"UPDATE tasks SET {', '.join(f'{k}=?' for k in updates)}, updated_at=? "
                                      "WHERE id=?", (*updates.values(), now(), work["id"]))
                    self._event("task", work["id"], None, None, f"shaped: {sorted(updates)}", actor)
                work = self.get(work["id"])
                if decision == "redirect":
                    nxt = self._add_stage(work, "shape", [node["id"]])
                else:
                    nxt = self._add_stage(work, self.rules.after_shape(work, bool(env.get("research"))), [node["id"]])
        return {"node": self.get(node["id"]), "work": self.get(work["id"]), "next": nxt}

    def submit_plan(self, run_id: str, env: dict, grading: dict | None = None, approver: str = vocab.CHAIR,
                    exit_code: int | None = 0) -> dict:
        """A validated plan, decomposition or question. Plans are graded (by the caller) and an approve
        stage is routed to `approver`."""
        with write_txn(self.conn):
            run, node, work, actor = self._begin_submit(run_id, "plan", exit_code)
            if env["kind"] == "needs_input":
                self._output(node["id"], run_id, "plan", env, env["question"])
                self._set(node["id"], "done", "planner needs input", actor, outcome="needs_input")
                self._message(sender=actor or "planner", to_ref=vocab.CHAIR, kind="question", task_id=work["id"],
                              body=env["question"], ack_required=1)
                self._set(work["id"], "needs_input", "planner needs input", actor, blocked_reason=env["question"])
                return {"node": self.get(node["id"]), "work": self.get(work["id"]), "question": env["question"]}
            g = grading or {"proposed": None, "floor": 4, "grade": 4, "reasons": ["ungraded"]}
            body = {**env, "author": run["employee_id"]}
            if env["kind"] == "plan":
                body["writes_fingerprint"] = fingerprint(work["workdir"], env.get("writes") or [])
            if env.get("acceptance") and not work["acceptance"]:
                self.conn.execute("UPDATE tasks SET acceptance=? WHERE id=?", (env["acceptance"], work["id"]))
            self._output(node["id"], run_id, "plan", body, env.get("findings") or env.get("rationale"))
            self._set(node["id"], "done", f"{env['kind']} G{g['grade']} -> {approver}", actor, outcome=env["kind"],
                      grade=g["grade"], grade_floor=g["floor"], grade_proposed=g["proposed"],
                      grade_reasons=_dumps(g["reasons"]))
            approval = self._add_stage(work, "approve", [node["id"]], approver=approver,
                                       assignee=approver[4:] if approver.startswith("emp:") else None, grade=g["grade"])
        return {"node": self.get(node["id"]), "work": self.get(work["id"]), "approval": approval}

    def set_grade(self, approve_id: str, grade: int, approver: str, note: str) -> dict:
        with write_txn(self.conn):
            a = self.get(approve_id)
            if a["stage"] != "approve" or a["status"] != "ready":
                raise TransitionError(f"{approve_id} is not a pending approval")
            self.conn.execute("UPDATE tasks SET grade=?, approver=?, assignee=? WHERE id=?",
                              (grade, approver, approver[4:] if approver.startswith("emp:") else None, approve_id))
            self.conn.execute("UPDATE tasks SET grade=? WHERE id=?", (grade, self.needs(approve_id)[0]))
            self._event("task", approve_id, f"G{a['grade']}", f"G{grade}", f"regraded -> {approver}: {note}")
        return self.get(approve_id)

    def _decider(self, approval: dict, review_run: str | None) -> str:
        decider = self.actor
        if review_run:
            run = self.get_run(review_run)
            if run["task_id"] != approval["id"]:
                raise TransitionError(f"run {review_run} is not a review of {approval['id']}")
            decider = f"emp:{run['employee_id']}"
            self._close_run(review_run, "succeeded")
        if decider != vocab.CHAIR and decider != approval["approver"]:
            raise TransitionError(f"{decider} may not decide {approval['id']} (requires {approval['approver']})")
        author = self.output(self.needs(approval["id"])[0])["body"].get("author")
        if author and decider == f"emp:{author}":
            raise TransitionError("nobody decides their own plan")
        return decider

    def decide(self, ref: str, decision: str, note: str | None = None, recurring: bool = False,
               review_run: str | None = None) -> dict:
        """Approve or reject a plan. The Chair may decide any; an employee only what is routed to them."""
        if decision not in vocab.OUTCOMES["approve"]:
            raise ValueError("decision must be approve or reject")
        if decision == "reject" and not (note and note.strip()):
            raise ValueError("a rejection needs a note: it is what the planner learns from")
        children: list[str] = []
        with write_txn(self.conn):
            approval = self.resolve_approval(ref)
            if approval["status"] not in ("ready", "claimed"):
                raise TransitionError(f"{approval['id']} is '{approval['status']}'")
            decider = self._decider(approval, review_run)
            work = self.get(approval["parent_id"])
            plan = self.plan_for_approval(approval["id"])
            body = plan["output"]["body"]
            self._set(approval["id"], "done", note, decider, outcome=decision, decided_by=decider, decided_at=now(),
                      decision_note=note)
            if decision == "reject":
                if body.get("apply"):
                    self._finish(work, "cancelled", f"proposal rejected: {note}", decider)
                else:
                    self._add_stage(work, "plan", [approval["id"]], assignee=body.get("author"))
                    self.rules.on_rework(work, approval, f"plan rejected: {note}")
            else:
                if recurring:
                    self.conn.execute("UPDATE tasks SET recurring=1 WHERE id=?", (plan["id"],))
                if body["kind"] == "decompose":
                    self._set(work["id"], "waiting", f"split by {plan['id']}", decider)
                    for c in body["children"]:
                        needs = [children[i] for i in c.get("after") or [] if isinstance(i, int) and 0 <= i < len(children)]
                        child = self._add_work(
                            c["title"], c["intent"], acceptance=c.get("acceptance"), parent_id=work["id"],
                            dept=c.get("dept") or work["dept_id"], assignee=c.get("assignee"),
                            requested_by=f"emp:{body['author']}" if body.get("author") else decider,
                            priority=c.get("priority", work["priority"]), source="agent",
                            task_class=c.get("task_class", work["task_class"]), risk=c.get("risk", work["risk"]),
                            workdir=c.get("workdir"), max_attempts=work["max_attempts"], needs=needs)
                        children.append(child["id"])
                elif body.get("apply"):
                    self.appliers[body["apply"]["kind"]](body["apply"])
                    self._finish(work, "done", "applied by the kernel on approval", decider)
                else:
                    self._add_stage(work, "execute", [approval["id"]], assignee=body.get("assignee") or work["assignee"])
        return {"approval": self.get(approval["id"]), "plan": self.get(plan["id"]), "work": self.get(work["id"]),
                "children": children, "decider": decider}

    def submit_execute(self, run_id: str, env: dict, exit_code: int | None = 0, **metrics: Any) -> dict:
        with write_txn(self.conn):
            failed = env["outcome"] == "failure"
            run, node, work, actor = self._begin_submit(run_id, "execute", exit_code,
                                                        "failed" if failed else "succeeded", **metrics)
            body = {k: v for k, v in env.items() if k != "documentation"}
            self._output(node["id"], run_id, "execute", body, env["summary"], env["documentation"])
            self._set(node["id"], "failed" if failed else "done", f"result {env['outcome']}", actor,
                      outcome=env["outcome"])
            if failed:
                self._retry_or_fail(work, self.get(node["id"]), f"execution failed: {env['summary'][:200]}", actor)
            else:
                self._add_stage(work, "verify", [node["id"]])
        return {"node": self.get(node["id"]), "work": self.get(work["id"]), "run": self.get_run(run_id),
                "result": self.output(node["id"])}

    def _retry_or_fail(self, work: dict, failed_stage: dict, reason: str, actor: str | None) -> None:
        last_exec = self.latest(work["id"], "execute") or {"attempt": 1, "assignee": None}
        self.rules.on_rework(work, failed_stage, reason)
        if last_exec["attempt"] < work["max_attempts"]:
            self._add_stage(work, "execute", [failed_stage["id"]], attempt=last_exec["attempt"] + 1,
                            assignee=last_exec["assignee"] or self.executor_of(work["id"]))
        else:
            self._finish(work, "failed", reason, actor, blocked_reason=reason)

    def verify(self, ref: str, verdict: str, note: str, run_id: str | None = None) -> dict:
        if verdict not in vocab.OUTCOMES["verify"]:
            raise ValueError("verdict must be pass or fail")
        with write_txn(self.conn):
            if run_id:
                run, node, work, actor = self._begin_submit(run_id, "verify", 0)
            else:
                node = self.get(ref)
                if node["stage"] is None:
                    node = self.latest(node["id"], "verify", "ready") or self.latest(node["id"], "verify", "claimed")
                    if node is None:
                        raise TransitionError(f"{ref} has no verification pending")
                if node["stage"] != "verify" or node["status"] not in ("ready", "claimed"):
                    raise TransitionError(f"{node['id']} is not a pending verification")
                work, actor = self.get(node["parent_id"]), self.actor
            ex = self.get(self.needs(node["id"])[0])
            executor = self.conn.execute("SELECT employee_id FROM runs WHERE task_id=? AND kind='execute' "
                                         "ORDER BY started_at DESC LIMIT 1", (ex["id"],)).fetchone()
            if actor != vocab.CHAIR and executor and executor[0] and actor == f"emp:{executor[0]}":
                raise TransitionError("an employee may not verify their own work")
            self._output(node["id"], run_id, "verify", {"verdict": verdict, "note": note}, note)
            self.conn.execute("UPDATE outputs SET verified=?, verifier_note=?, verified_by=? WHERE task_id=? "
                              "AND kind='execute'", (1 if verdict == "pass" else 0, note, actor, ex["id"]))
            self._set(node["id"], "done", note, actor, outcome=verdict)
            if verdict == "pass":
                self._finish(work, "done", f"verified: {note}", actor)
            else:
                self._retry_or_fail(work, self.get(node["id"]), f"verification failed: {note}", actor)
        return {"node": self.get(node["id"]), "work": self.get(work["id"])}

    def skip_verification(self, work_id: str, note: str) -> dict:
        """Close trusted work without a verifier (sampled out). The result stays unverified (verified IS NULL),
        so it never counts toward anyone's earned authority or trust."""
        with write_txn(self.conn):
            v = self.latest(work_id, "verify", "ready")
            if v is None:
                raise TransitionError(f"{work_id} has no verification pending")
            self.conn.execute("UPDATE outputs SET verifier_note=?, verified_by='sampling' WHERE task_id=? "
                              "AND kind='execute'", (note, self.needs(v["id"])[0]))
            self._set(v["id"], "cancelled", note, "kernel")
            self._finish(self.get(work_id), "done", note, "kernel")
        return self.get(work_id)

    def submit_retro(self, run_id: str, env: dict, exit_code: int | None = 0) -> dict:
        """A retro's actions become new work in the department: changes to playbooks, skills, staffing or
        the genome go through the same pipeline as any other work."""
        created = []
        with write_txn(self.conn):
            run, node, work, actor = self._begin_submit(run_id, "retro", exit_code)
            self._output(node["id"], run_id, "retro", env, env.get("summary"))
            self._set(node["id"], "done", "retro done", actor, outcome="done")
            for p in env.get("actions") or []:
                created.append(self._add_work(
                    p["title"], p["intent"], acceptance=p.get("acceptance"), dept=p.get("dept") or work["dept_id"],
                    requested_by=actor, source="audit", task_class=p.get("task_class") or "improvement",
                    priority=p.get("priority", 3))["id"])
            if work["status"] == "open" and not self._active_stages(work["id"]):
                self._finish(work, "done", "retro complete", actor)
        return {"node": self.get(node["id"]), "work": self.get(work["id"]), "created": created}

    def add_retro(self, work_id: str, why: str) -> dict | None:
        """Queue a retro on a work node (once per reason while one is pending)."""
        work = self.get(work_id)
        pending = [s for s in self.stages(work_id, "retro") if s["status"] in ("ready", "claimed")]
        if pending:
            return None
        prev = self.stages(work_id)
        r = self._add_stage(work, "retro", [prev[-1]["id"]] if prev else [], blocked_reason=why[:500])
        return r

    def _active_stages(self, work_id: str) -> list[dict]:
        return [s for s in self.stages(work_id) if s["status"] in ("ready", "claimed")]

    # ── settling: completion flows up (parents) and across (dependents) ───
    def _finish(self, work: dict, status: str, note: str | None, actor: str | None = None, **cols: Any) -> None:
        work = self.get(work["id"])
        if work["status"] in TERMINAL:
            return
        for s in self._active_stages(work["id"]):
            if s["stage"] != "retro":
                self._abandon(s, f"work {status}")
        if status == "cancelled":
            for c in self.children(work["id"]):
                if c["status"] not in TERMINAL:
                    self._finish(c, "cancelled", "parent cancelled", actor)
            if self.get(work["id"])["status"] in TERMINAL:  # settled by its last child
                return
        self._set(work["id"], status, note, actor, **cols)
        work = self.get(work["id"])
        self.rules.on_finished(work)
        for r in self.conn.execute("SELECT task_id FROM task_needs WHERE needs_id=?", (work["id"],)).fetchall():
            dep = self.get(r[0])
            if dep["stage"] is not None or dep["status"] != "blocked":
                continue
            if status != "done":
                self._finish(dep, "cancelled", f"dependency {work['id']} {status}", actor)
            elif all(self.get(n)["status"] == "done" for n in self.needs(dep["id"])):
                self._set(dep["id"], "open", "dependencies done", actor)
                self._expand(self.get(dep["id"]))
        if work["parent_id"]:
            self._settle_parent(self.get(work["parent_id"]))

    def _settle_parent(self, parent: dict) -> None:
        if parent["status"] != "waiting":
            return
        statuses = [c["status"] for c in self.children(parent["id"])]
        if any(s not in TERMINAL for s in statuses):
            return
        if "failed" in statuses:
            self._set(parent["id"], "needs_input", "a sub-task failed", blocked_reason="sub-task failed")
            self.rules.on_rework(parent, parent, "a sub-task failed")
        elif "done" in statuses:
            self._finish(parent, "done", "all sub-tasks closed")
        else:
            self._finish(parent, "cancelled", "all sub-tasks cancelled")

    def _abandon(self, stage: dict, why: str) -> None:
        for r in self.conn.execute("SELECT id FROM runs WHERE task_id=? AND status='running'", (stage["id"],)).fetchall():
            self._close_run(r["id"], "abandoned")
        self._set(stage["id"], "cancelled", why)

    # ── Chair controls ────────────────────────────────────────────────────
    def cancel(self, ref: str, note: str) -> dict:
        with write_txn(self.conn):
            work = self.work_of(ref)
            self._finish(work, "cancelled", note)
        return self.get(work["id"])

    def retry(self, ref: str, note: str = "chair retry") -> dict:
        with write_txn(self.conn):
            work = self.work_of(ref)
            if work["status"] != "failed":
                raise TransitionError(f"{work['id']} is '{work['status']}', not failed")
            plan = self.approved_plan(work["id"])
            if plan is None:
                raise TransitionError("no approved plan to retry; use replan")
            last = self.latest(work["id"], "execute")
            self.conn.execute("UPDATE tasks SET max_attempts=max_attempts+1, status='open', blocked_reason=NULL "
                              "WHERE id=?", (work["id"],))
            self._event("task", work["id"], "failed", "open", note)
            work = self.get(work["id"])
            prev = self.stages(work["id"])[-1]
            self._add_stage(work, "execute", [prev["id"]], attempt=(last["attempt"] if last else 0) + 1,
                            assignee=plan["output"]["body"].get("assignee") or work["assignee"])
        return self.get(work["id"])

    def replan(self, ref: str, note: str = "chair replan") -> dict:
        with write_txn(self.conn):
            work = self.work_of(ref)
            if work["status"] not in ("open", "failed", "needs_input"):
                raise TransitionError(f"{work['id']} is '{work['status']}'")
            for s in self._active_stages(work["id"]):
                self._abandon(s, note)
            if work["status"] != "open":
                self.conn.execute("UPDATE tasks SET status='open', blocked_reason=NULL WHERE id=?", (work["id"],))
                self._event("task", work["id"], work["status"], "open", note)
            last = self.stages(work["id"])
            self._add_stage(self.get(work["id"]), "plan", [last[-1]["id"]] if last else [])
        return self.get(work["id"])

    def answer(self, ref: str, text: str) -> dict:
        with write_txn(self.conn):
            work = self.work_of(ref)
            if work["status"] != "needs_input":
                raise TransitionError(f"{work['id']} is '{work['status']}', not needs_input")
            self._message(sender=self.actor, to_ref=f"task:{work['id']}", kind="answer", task_id=work["id"], body=text)
            self.conn.execute("UPDATE messages SET acked_at=? WHERE task_id=? AND kind='question' AND acked_at IS NULL",
                              (now(), work["id"]))
            self._set(work["id"], "open", "answered", blocked_reason=None)
            last = self.stages(work["id"])
            self._add_stage(self.get(work["id"]), "plan", [last[-1]["id"]] if last else [])
        return self.get(work["id"])

    # ── kernel proposals (decisions the organization applies itself once approved) ──
    def propose(self, *, title: str, intent: str, steps: str, apply: dict, approver: str, grade: int,
                task_class: str, dept: str | None = None, author: str | None = None) -> dict:
        """A decision for an approver that needs no executor: the kernel applies `apply` on approval.
        Returns the approve stage. Idempotent by title while one is pending."""
        if apply.get("kind") not in self.appliers:
            raise ValueError(f"no applier for {apply.get('kind')!r}")
        with write_txn(self.conn):
            r = self.conn.execute("SELECT a.id FROM tasks a JOIN tasks w ON w.id=a.parent_id WHERE w.title=? AND "
                                  "a.stage='approve' AND a.status IN ('ready','claimed')", (title,)).fetchone()
            if r:
                return self.get(r["id"])
            work = self._add_work(title, intent, acceptance="applied by the kernel on approval", dept=dept,
                                  requested_by=author or self.actor, priority=2, source="kernel",
                                  task_class=task_class, expand=False)
            plan = self._add_stage(work, "plan", [])
            self._output(plan["id"], None, "plan", {"kind": "plan", "findings": intent, "steps": [steps],
                                                    "writes": [], "apply": apply, "author": None}, intent)
            self._set(plan["id"], "done", "kernel proposal", "kernel", outcome="plan", grade=grade, grade_floor=grade,
                      grade_proposed=grade, grade_reasons=_dumps([f"{task_class}: routed by the autonomy policy"]))
            approval = self._add_stage(work, "approve", [plan["id"]], approver=approver,
                                       assignee=approver[4:] if approver.startswith("emp:") else None, grade=grade)
        return approval

    # ── recurring work ────────────────────────────────────────────────────
    def instantiate_recurring(self, plan_id: str, label: str | None = None) -> dict:
        """One firing of a recurring plan: a fresh work node carrying a copy of the approved plan, entering
        its pipeline at execute. The approval it inherits is recorded."""
        plan = self.get(plan_id)
        if plan["stage"] != "plan" or not plan["recurring"]:
            raise TransitionError(f"{plan_id} is not an approved recurring plan")
        src, body = self.get(plan["parent_id"]), self.output(plan_id)["body"]
        with write_txn(self.conn):
            work = self._add_work(f"{src['title']} — {label or now()[:16]}", src["intent"],
                                  acceptance=src["acceptance"], project_ref=src["project_ref"], dept=src["dept_id"],
                                  assignee=src["assignee"], requested_by=f"recurring:{plan_id}", priority=src["priority"],
                                  source="schedule", task_class=src["task_class"], risk=src["risk"],
                                  workdir=src["workdir"], max_attempts=src["max_attempts"], expand=False)
            p = self._add_stage(work, "plan", [])
            self._output(p["id"], None, "plan", {**body, "writes_fingerprint": fingerprint(
                work["workdir"], body.get("writes") or [])}, f"copy of recurring plan {plan_id}")
            self._set(p["id"], "done", f"copy of {plan_id}", "kernel", outcome="plan", grade=plan["grade"],
                      grade_floor=plan["grade_floor"], grade_reasons=plan["grade_reasons"])
            a = self._add_stage(work, "approve", [p["id"]], approver=f"recurring:{plan_id}", grade=plan["grade"])
            self._set(a["id"], "done", "inherits a recurring approval", "kernel", outcome="approve",
                      decided_by=f"recurring:{plan_id}", decided_at=now())
            self._add_stage(work, "execute", [a["id"]], assignee=body.get("assignee") or src["assignee"])
        return self.get(work["id"])

    # ── trust ─────────────────────────────────────────────────────────────
    def executor_streak(self, employee_id: str, task_class: str | None) -> int:
        """Consecutive passed verifications of this executor's results in a task class, newest first."""
        streak = 0
        for r in self.conn.execute(
                "SELECT o.verified FROM outputs o JOIN runs u ON u.id=o.run_id JOIN tasks t ON t.id=o.task_id "
                "WHERE o.kind='execute' AND u.employee_id=? AND coalesce(t.task_class,'*')=? AND o.verified IS NOT NULL "
                "ORDER BY o.created_at DESC", (employee_id, task_class or "*")):
            if r[0] != 1:
                break
            streak += 1
        return streak

    def verified_in_class(self, task_class: str | None) -> int:
        if not task_class:
            return 0
        return self.conn.execute(
            "SELECT count(DISTINCT t.parent_id) FROM outputs o JOIN tasks t ON t.id=o.task_id "
            "WHERE o.kind='execute' AND o.verified=1 AND t.task_class=?", (task_class,)).fetchone()[0]

    # ── self-healing ──────────────────────────────────────────────────────
    def sweep_leases(self) -> list[dict]:
        """Recover stages held by employees whose runs died: the stage is ready again; an execute stage
        counts as a failed attempt."""
        swept = []
        with write_txn(self.conn):
            for run in self.conn.execute("SELECT * FROM runs WHERE status='running' AND lease_until < ?",
                                         (now(),)).fetchall():
                run = dict(run)
                self._close_run(run["id"], "timeout")
                node = self.get(run["task_id"])
                if node["status"] == "claimed":
                    if node["stage"] == "execute":
                        self._set(node["id"], "failed", f"executor lease expired ({run['id']})")
                        self._retry_or_fail(self.get(node["parent_id"]), self.get(node["id"]),
                                            "executor lease expired", None)
                    else:
                        self._set(node["id"], "ready", f"lease expired ({run['id']})")
                swept.append({"run": run["id"], "task": node["id"], "stage": node["stage"],
                              "employee": run["employee_id"]})
        return swept

    def release(self, run_id: str, status: str, note: str) -> dict:
        """A run ended without a valid result (contract breach, crash): the stage returns to ready."""
        with write_txn(self.conn):
            run = self._close_run(run_id, status)
            node = self.get(run["task_id"])
            if node["status"] == "claimed":
                self._set(node["id"], "ready", note, f"emp:{run['employee_id']}" if run["employee_id"] else None)
        return self.get(node["id"])

    # ── messages ──────────────────────────────────────────────────────────
    def _message(self, sender: str, body: str, to_ref: str, kind: str = "dm", task_id: str | None = None,
                 thread_id: str | None = None, ack_required: int = 0) -> str:
        if kind not in vocab.MESSAGE_KINDS:
            raise ValueError(f"message kind must be one of {vocab.MESSAGE_KINDS}")
        mid = new_id("message")
        self.conn.execute(
            "INSERT INTO messages (id, ts, sender, to_ref, kind, task_id, thread_id, body, ack_required)"
            " VALUES (?,?,?,?,?,?,?,?,?)", (mid, now(), sender, to_ref, kind, task_id, thread_id or mid, body, ack_required))
        return mid

    def post_message(self, sender: str, to_ref: str, body: str, kind: str = "dm", task_id: str | None = None,
                     thread_id: str | None = None, ack_required: int = 0) -> str:
        with write_txn(self.conn):
            return self._message(sender, body, to_ref, kind, task_id, thread_id, ack_required)

    def messages(self, task_id: str | None = None, to_refs: list[str] | None = None, kind: str | None = None,
                 unacked: bool = False, limit: int = 50) -> list[dict]:
        q, args = "SELECT * FROM messages WHERE 1=1", []
        if task_id:
            q += " AND task_id=?"
            args.append(task_id)
        if to_refs:
            marks = ",".join("?" * len(to_refs))
            q += f" AND (to_ref IN ({marks}) OR sender IN ({marks}))"
            args += [*to_refs, *to_refs]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if unacked:
            q += " AND acked_at IS NULL"
        order = " ORDER BY ts" if task_id else " ORDER BY ts DESC"
        return [dict(r) for r in self.conn.execute(q + order + " LIMIT ?", [*args, limit])]

    def ack(self, message_ids: list[str]) -> None:
        if message_ids:
            with write_txn(self.conn):
                self.conn.execute(f"UPDATE messages SET acked_at=? WHERE id IN ({','.join('?' * len(message_ids))})",
                                  (now(), *message_ids))
