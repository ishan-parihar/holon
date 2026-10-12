"""0004: the holarchic work graph.

Before: one task row per piece of work with a 10-state machine, plus `plans` and `results`.
After:  every node is a task. A work node (stage IS NULL) holds a pipeline of stage nodes
        (research, shape, plan, approve, execute, verify, retro) and/or child work nodes.
        `task_needs` orders nodes; `outputs` holds what each stage produced.

Enumerations are written out literally: a migration is history and must not change when the
vocabulary does (`holon lint` checks the live schema against holon.core.vocab instead).
Existing work is converted so every task keeps its id and every plan id becomes its plan stage.
"""

import hashlib
import json
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE tasks_g (
  id             TEXT PRIMARY KEY,
  root_id        TEXT NOT NULL,
  parent_id      TEXT REFERENCES tasks(id),
  depth          INTEGER NOT NULL DEFAULT 0,
  stage          TEXT CHECK (stage IS NULL OR stage IN
                   ('research','shape','plan','approve','execute','verify','retro')),
  dept_id        TEXT REFERENCES departments(id),
  assignee       TEXT REFERENCES employees(id),
  approver       TEXT,
  requested_by   TEXT NOT NULL DEFAULT 'chair',
  source         TEXT NOT NULL CHECK (source IN ('chair','agent','audit','schedule','kernel')),
  title          TEXT NOT NULL,
  intent         TEXT NOT NULL,
  acceptance     TEXT,
  task_class     TEXT,
  project_ref    TEXT,
  workdir        TEXT,
  priority       INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 0 AND 4),
  risk           TEXT NOT NULL DEFAULT 'normal' CHECK (risk IN ('low','normal','high','irreversible')),
  status         TEXT NOT NULL CHECK (
                   (stage IS NULL AND status IN ('blocked','open','waiting','needs_input','done','failed','cancelled'))
                   OR (stage IS NOT NULL AND status IN ('ready','claimed','done','failed','cancelled'))),
  outcome        TEXT,
  blocked_reason TEXT,
  attempt        INTEGER NOT NULL DEFAULT 1,
  max_attempts   INTEGER NOT NULL DEFAULT 2,
  grade          INTEGER CHECK (grade IS NULL OR grade BETWEEN 0 AND 4),
  grade_floor    INTEGER,
  grade_proposed INTEGER,
  grade_reasons  TEXT,
  decided_by     TEXT,
  decided_at     TEXT,
  decision_note  TEXT,
  recurring      INTEGER NOT NULL DEFAULT 0,
  due            TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  ext_id         TEXT,
  sync_dirty     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE task_needs (
  task_id  TEXT NOT NULL REFERENCES tasks(id),
  needs_id TEXT NOT NULL REFERENCES tasks(id),
  PRIMARY KEY (task_id, needs_id),
  CHECK (task_id <> needs_id)
);
CREATE INDEX idx_needs_needs ON task_needs(needs_id);

CREATE TABLE outputs (
  id            TEXT PRIMARY KEY,
  task_id       TEXT NOT NULL REFERENCES tasks(id),
  run_id        TEXT REFERENCES runs(id),
  kind          TEXT NOT NULL,
  body          TEXT NOT NULL,
  summary       TEXT,
  documentation TEXT,
  verified      INTEGER,
  verified_by   TEXT,
  verifier_note TEXT,
  created_at    TEXT NOT NULL
);
CREATE INDEX idx_outputs_task ON outputs(task_id);

CREATE TABLE runs_g (
  id           TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL REFERENCES tasks(id),
  kind         TEXT NOT NULL,
  employee_id  TEXT REFERENCES employees(id),
  driver       TEXT,
  attempt      INTEGER NOT NULL DEFAULT 1,
  lease_until  TEXT,
  heartbeat_at TEXT,
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  status       TEXT NOT NULL DEFAULT 'running'
               CHECK (status IN ('running','succeeded','failed','timeout','abandoned')),
  tokens_in    INTEGER,
  tokens_out   INTEGER,
  cost         REAL,
  exit_code    INTEGER,
  brief_path   TEXT,
  log_path     TEXT,
  token_hash   TEXT
);

CREATE TABLE audit_samples_g (
  task_id    TEXT PRIMARY KEY REFERENCES tasks(id),
  approver   TEXT NOT NULL,
  sampled_at TEXT NOT NULL,
  verdict    TEXT CHECK (verdict IN ('agree','disagree')),
  note       TEXT,
  judged_at  TEXT
);

CREATE TABLE schedules_g (
  id             TEXT PRIMARY KEY,
  kind           TEXT NOT NULL CHECK (kind IN ('wake','recurring')),
  cron           TEXT NOT NULL,
  employee_id    TEXT REFERENCES employees(id),
  stage          TEXT,
  template_id    TEXT REFERENCES tasks(id),
  enabled        INTEGER NOT NULL DEFAULT 1,
  last_run_at    TEXT,
  next_run_at    TEXT,
  failure_streak INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL
);

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE messages_g (
  id           TEXT PRIMARY KEY,
  ts           TEXT NOT NULL,
  sender       TEXT NOT NULL,
  to_ref       TEXT NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'dm'
               CHECK (kind IN ('dm','channel','request','question','answer','proposal')),
  task_id      TEXT REFERENCES tasks(id),
  thread_id    TEXT,
  body         TEXT NOT NULL,
  ack_required INTEGER NOT NULL DEFAULT 0,
  acked_at     TEXT
);
"""

INDEXES = """
CREATE INDEX idx_tasks_board ON tasks(status, stage, priority, created_at);
CREATE INDEX idx_tasks_dept ON tasks(dept_id, status);
CREATE INDEX idx_tasks_assignee ON tasks(assignee, status);
CREATE INDEX idx_tasks_approver ON tasks(approver, status);
CREATE INDEX idx_tasks_parent ON tasks(parent_id);
CREATE INDEX idx_tasks_root ON tasks(root_id);
CREATE INDEX idx_tasks_project ON tasks(project_ref);
CREATE INDEX idx_runs_task ON runs(task_id);
CREATE INDEX idx_runs_status ON runs(status, lease_until);
CREATE INDEX idx_runs_employee ON runs(employee_id, kind, status);
CREATE INDEX idx_messages_to ON messages(to_ref, ts);
CREATE INDEX idx_messages_task ON messages(task_id);
"""

FTS = """
CREATE VIRTUAL TABLE tasks_fts USING fts5(title, intent, content='tasks', content_rowid='rowid');
CREATE TRIGGER tasks_fts_ai AFTER INSERT ON tasks BEGIN
  INSERT INTO tasks_fts(rowid, title, intent) VALUES (new.rowid, new.title, new.intent);
END;
CREATE TRIGGER tasks_fts_ad AFTER DELETE ON tasks BEGIN
  INSERT INTO tasks_fts(tasks_fts, rowid, title, intent) VALUES ('delete', old.rowid, old.title, old.intent);
END;
CREATE TRIGGER tasks_fts_au AFTER UPDATE OF title, intent ON tasks BEGIN
  INSERT INTO tasks_fts(tasks_fts, rowid, title, intent) VALUES ('delete', old.rowid, old.title, old.intent);
  INSERT INTO tasks_fts(rowid, title, intent) VALUES (new.rowid, new.title, new.intent);
END;
CREATE VIRTUAL TABLE outputs_fts USING fts5(summary, documentation, content='outputs', content_rowid='rowid');
CREATE TRIGGER outputs_fts_ai AFTER INSERT ON outputs BEGIN
  INSERT INTO outputs_fts(rowid, summary, documentation) VALUES (new.rowid, new.summary, new.documentation);
END;
CREATE TRIGGER outputs_fts_ad AFTER DELETE ON outputs BEGIN
  INSERT INTO outputs_fts(outputs_fts, rowid, summary, documentation)
    VALUES ('delete', old.rowid, old.summary, old.documentation);
END;
"""

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _id(prefix, seed):
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return prefix + "_" + "".join(_ALPHABET[(h >> (5 * i)) & 31] for i in range(26))


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _script(conn, sql):
    """Run a multi-statement script inside the caller's transaction (executescript would commit it)."""
    import sqlite3
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            conn.execute(buf)
            buf = ""
    if buf.strip():
        raise RuntimeError(f"incomplete SQL statement: {buf[:80]}")


def _has(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def up(conn):
    fts = _has(conn, "tasks_fts")
    for t in ("tasks_fts", "results_fts"):
        if _has(conn, t):
            conn.execute(f"DROP TABLE {t}")
    for trig in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")]:
        conn.execute(f"DROP TRIGGER {trig}")
    _script(conn, SCHEMA)
    conn.execute("ALTER TABLE events ADD COLUMN prev_hash TEXT")
    conn.execute("ALTER TABLE events ADD COLUMN hash TEXT")
    conn.execute("ALTER TABLE employees ADD COLUMN key_hash TEXT")
    _convert(conn)
    _roles(conn)
    for old, new in (("tasks", "tasks_g"), ("runs", "runs_g"), ("audit_samples", "audit_samples_g"),
                     ("schedules", "schedules_g"), ("messages", "messages_g")):
        conn.execute(f"DROP TABLE {old}")
        conn.execute(f"ALTER TABLE {new} RENAME TO {old}")
    conn.execute("DROP TABLE results")
    conn.execute("DROP TABLE plans")
    _script(conn, INDEXES)
    if fts:
        _script(conn, FTS)
        conn.execute("INSERT INTO tasks_fts(tasks_fts) VALUES ('rebuild')")
        conn.execute("INSERT INTO outputs_fts(outputs_fts) VALUES ('rebuild')")
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise RuntimeError(f"0004: foreign key violations after conversion: {[tuple(r) for r in bad[:5]]}")


def _convert(conn):
    ts = _now()
    tasks = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM tasks")}

    def root_and_depth(tid):
        depth, cur = 0, tasks[tid]
        while cur["parent_id"] and cur["parent_id"] in tasks:
            cur = tasks[cur["parent_id"]]
            depth += 1
        return cur["id"], depth

    work_status = {"inbox": "open", "planning": "open", "awaiting_approval": "open", "approved": "open",
                   "executing": "open", "verifying": "open", "needs_input": "needs_input",
                   "waiting_on_subtasks": "waiting", "done": "done", "failed": "failed", "cancelled": "cancelled"}
    cols = ("id, root_id, parent_id, depth, stage, dept_id, assignee, approver, requested_by, source, title, intent,"
            " acceptance, task_class, project_ref, workdir, priority, risk, status, outcome, blocked_reason, attempt,"
            " max_attempts, grade, grade_floor, grade_proposed, grade_reasons, decided_by, decided_at, decision_note,"
            " recurring, due, created_at, updated_at, ext_id, sync_dirty")
    marks = ",".join("?" * len(cols.split(",")))

    defaults = {"attempt": 1, "max_attempts": 2, "recurring": 0, "sync_dirty": 1, "priority": 3, "risk": "normal",
                "requested_by": "chair"}

    def insert(**v):
        v = {**defaults, **{k: x for k, x in v.items() if x is not None}}
        row = [v.get(c.strip()) for c in cols.split(",")]
        conn.execute(f"INSERT INTO tasks_g ({cols}) VALUES ({marks})", row)

    def stage_node(work, sid, stage, status, created, **extra):
        insert(id=sid, root_id=work["_root"], parent_id=work["id"], depth=work["_depth"] + 1, stage=stage,
               dept_id=work["dept_id"], requested_by=work["requested_by"], source="kernel",
               title=f"{stage}: {work['title']}", intent=work["intent"], acceptance=work["acceptance"],
               task_class=work["task_class"], project_ref=work["project_ref"], workdir=work["workdir"],
               priority=work["priority"], risk=work["risk"], status=status, attempt=extra.pop("attempt", 1),
               max_attempts=work["max_attempts"], recurring=extra.pop("recurring", 0), created_at=created,
               updated_at=ts, sync_dirty=1, **extra)

    # 1. work nodes, keeping their ids
    for t in tasks.values():
        t["_root"], t["_depth"] = root_and_depth(t["id"])
        insert(id=t["id"], root_id=t["_root"], parent_id=t["parent_id"], depth=t["_depth"], stage=None,
               dept_id=t["dept_id"], assignee=t["assignee"], requested_by=t["requested_by"],
               source=t["source"], title=t["title"], intent=t["intent"], acceptance=t["acceptance"],
               task_class=t["task_class"], project_ref=t["project_ref"], workdir=t["workdir"],
               priority=t["priority"], risk=t["risk"], status=work_status.get(t["status"], "open"),
               blocked_reason=t["blocked_reason"], max_attempts=t["max_attempts"], due=t["due"],
               created_at=t["created_at"], updated_at=t["updated_at"], ext_id=t["ext_id"], sync_dirty=1)

    # 2. plans -> plan stages (same id) + approve stages
    approve_of: dict[str, str] = {}
    last_node: dict[str, str] = {}
    for p in conn.execute("SELECT * FROM plans ORDER BY task_id, version"):
        p = dict(p)
        work = tasks[p["task_id"]]
        body = {"kind": p["kind"] if p["kind"] == "decompose" else "plan", "findings": p["findings"],
                "steps": _load(p["steps"]), "writes": _load(p["writes"]) or [],
                "external_effects": _load(p["external_effects"]) or [], "assignee": p["assignee"],
                "risks": p["risks"], "rollback": p["rollback"], "estimate": p["estimate"],
                "context_refs": _load(p["context_refs"]), "writes_fingerprint": p["writes_fingerprint"],
                "author": p["author"]}
        if p["kind"] == "decompose":
            body = {"kind": "decompose", "rationale": p["findings"], "children": _load(p["steps"]),
                    "author": p["author"]}
        superseded = p["status"] == "superseded"
        stage_node(work, p["id"], "plan", "cancelled" if superseded else "done", p["created_at"],
                   outcome=body["kind"], grade=p["grade"], grade_floor=p["grade_floor"],
                   grade_proposed=p["grade_proposed"], grade_reasons=p["grade_reasons"],
                   assignee=p["author"], recurring=p["recurring"],
                   blocked_reason="superseded" if superseded else None)
        if work["id"] in last_node:
            conn.execute("INSERT INTO task_needs VALUES (?,?)", (p["id"], last_node[work["id"]]))
        conn.execute("INSERT INTO outputs (id, task_id, kind, body, summary, created_at) VALUES (?,?,?,?,?,?)",
                     (_id("out", "plan" + p["id"]), p["id"], "plan", json.dumps(body), (p["findings"] or "")[:500],
                      p["created_at"]))
        last_node[work["id"]] = p["id"]
        if superseded:
            continue
        aid = _id("tsk", "approve" + p["id"])
        status = {"awaiting_approval": "ready", "approved": "done", "rejected": "done"}[p["status"]]
        approver = p["approver"]
        stage_node(work, aid, "approve", status, p["created_at"], approver=approver,
                   assignee=approver[4:] if approver.startswith("emp:") else None,
                   outcome={"approved": "approve", "rejected": "reject"}.get(p["status"]),
                   grade=p["grade"], decided_by=p["decided_by"], decided_at=p["decided_at"],
                   decision_note=p["review_note"])
        conn.execute("INSERT INTO task_needs VALUES (?,?)", (aid, p["id"]))
        approve_of[p["id"]] = aid
        last_node[work["id"]] = aid

    # 3. runs, results -> execute / verify stages and outputs
    run_node: dict[str, str] = {}
    for r in conn.execute("SELECT * FROM runs ORDER BY started_at"):
        r = dict(r)
        work = tasks[r["task_id"]]
        target = work["id"]
        if r["kind"] == "plan" and r["plan_id"]:
            target = r["plan_id"]
        elif r["kind"] == "review" and r["plan_id"] in approve_of:
            target = approve_of[r["plan_id"]]
        elif r["kind"] == "execute":
            res = conn.execute("SELECT * FROM results WHERE run_id=?", (r["id"],)).fetchone()
            if res is not None:
                res = dict(res)
                eid = _id("tsk", "execute" + r["id"])
                stage_node(work, eid, "execute", "done" if res["outcome"] != "failure" else "failed",
                           r["started_at"], assignee=r["employee_id"], outcome=res["outcome"],
                           attempt=r["attempt"])
                if work["id"] in last_node:
                    conn.execute("INSERT INTO task_needs VALUES (?,?)", (eid, last_node[work["id"]]))
                body = {k: _load(res[k]) for k in ("artifacts", "learnings", "decisions", "blockers", "proposals")}
                body.update(outcome=res["outcome"], summary=res["summary"], next_intent=res["next_intent"])
                conn.execute(
                    "INSERT INTO outputs (id, task_id, run_id, kind, body, summary, documentation, verified,"
                    " verified_by, verifier_note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (_id("out", "exec" + r["id"]), eid, r["id"], "execute", json.dumps(body), res["summary"],
                     res["documentation"], res["verified"], res["verified_by"], res["verifier_note"],
                     res["created_at"]))
                last_node[work["id"]] = eid
                target = eid
                if res["verified"] is not None and res["verified_by"] != "sampling":
                    vid = _id("tsk", "verify" + r["id"])
                    stage_node(work, vid, "verify", "done", res["created_at"],
                               outcome="pass" if res["verified"] else "fail",
                               assignee=res["verified_by"][4:] if (res["verified_by"] or "").startswith("emp:") else None)
                    conn.execute("INSERT INTO task_needs VALUES (?,?)", (vid, eid))
                    last_node[work["id"]] = vid
        run_node[r["id"]] = target
        conn.execute(
            "INSERT INTO runs_g (id, task_id, kind, employee_id, driver, attempt, lease_until, heartbeat_at, started_at,"
            " ended_at, status, tokens_in, tokens_out, cost, exit_code, brief_path, log_path)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["id"], target, {"review": "approve"}.get(r["kind"], r["kind"]), r["employee_id"], r["driver"],
             r["attempt"], r["lease_until"], r["heartbeat_at"], r["started_at"], r["ended_at"] or ts,
             "abandoned" if r["status"] == "running" else r["status"], r["tokens_in"], r["tokens_out"], r["cost"],
             r["exit_code"], r["brief_path"], r["log_path"]))

    # 4. the next stage each open work node is waiting for
    for t in tasks.values():
        prev = last_node.get(t["id"])
        nxt = {"inbox": "plan", "planning": "plan", "approved": "execute", "executing": "execute",
               "verifying": "verify", "failed": None}.get(t["status"])
        if t["status"] == "approved" or t["status"] == "executing":
            plan = conn.execute("SELECT id FROM tasks_g WHERE parent_id=? AND stage='plan' AND status='done' "
                                "ORDER BY created_at DESC LIMIT 1", (t["id"],)).fetchone()
            assignee = None
            if plan:
                body = json.loads(conn.execute("SELECT body FROM outputs WHERE task_id=?", (plan[0],)).fetchone()[0])
                assignee = body.get("assignee")
            assignee = assignee or t["assignee"]
        else:
            assignee = t["assignee"] if nxt == "plan" else None
        if nxt:
            sid = _id("tsk", nxt + "-open-" + t["id"])
            stage_node(t, sid, nxt, "ready", ts, assignee=assignee)
            if prev:
                conn.execute("INSERT INTO task_needs VALUES (?,?)", (sid, prev))

    # 5. the rest
    for a in conn.execute("SELECT * FROM audit_samples"):
        if a["plan_id"] in approve_of:
            conn.execute("INSERT INTO audit_samples_g VALUES (?,?,?,?,?,?)",
                         (approve_of[a["plan_id"]], a["approver"], a["sampled_at"], a["verdict"], a["note"],
                          a["judged_at"]))
    for s in conn.execute("SELECT * FROM schedules"):
        s = dict(s)
        recurring = s["kind"] == "recurring"
        conn.execute(
            "INSERT INTO schedules_g (id, kind, cron, employee_id, stage, template_id, enabled, last_run_at,"
            " next_run_at, failure_streak, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (s["id"], "recurring" if recurring else "wake", s["cron"], s["employee_id"],
             None if recurring else {"review": "approve"}.get(s["kind"], s["kind"]),
             s["plan_id"] if recurring else None, s["enabled"], s["last_run_at"], s["next_run_at"],
             s["failure_streak"], s["created_at"]))
    conn.execute("INSERT INTO messages_g SELECT * FROM messages")


def _roles(conn):
    """Employees hired before roles existed get roles from their duties; duties then retire."""
    import sqlite3
    if sqlite3.sqlite_version_info < (3, 35):
        raise RuntimeError("Holon needs SQLite >= 3.35 (ALTER TABLE ... DROP COLUMN)")
    role_of = {"plan": "planner", "execute": "executor", "verify": "verifier"}
    for e in conn.execute("SELECT e.id, e.duties, e.roles, (d.head_id = e.id) AS is_head FROM employees e "
                          "JOIN departments d ON d.id = e.dept_id").fetchall():
        if e["roles"]:
            continue
        roles = {role_of[d] for d in json.loads(e["duties"] or "[]") if d in role_of}
        if e["is_head"]:
            roles.add("head")
        conn.execute("UPDATE employees SET roles=? WHERE id=?", (json.dumps(sorted(roles)), e["id"]))
    conn.execute("ALTER TABLE employees DROP COLUMN duties")


def _load(v):
    if v is None:
        return None
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v
