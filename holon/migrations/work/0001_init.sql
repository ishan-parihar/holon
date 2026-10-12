-- work.db: delegated work. Single writer: holon.execution.

CREATE TABLE tasks (
  id            TEXT PRIMARY KEY,
  project_ref   TEXT,
  parent_id     TEXT REFERENCES tasks(id),
  title         TEXT NOT NULL,
  intent        TEXT NOT NULL,
  acceptance    TEXT,
  priority      INTEGER NOT NULL DEFAULT 3,
  source        TEXT NOT NULL CHECK (source IN ('human','observatory','agent','schedule')),
  task_class    TEXT,
  risk          TEXT NOT NULL DEFAULT 'normal' CHECK (risk IN ('low','normal','high','irreversible')),
  workdir       TEXT,
  status        TEXT NOT NULL DEFAULT 'inbox',
  blocked_reason TEXT,
  max_attempts  INTEGER NOT NULL DEFAULT 2,
  due           TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  ext_id        TEXT,
  sync_dirty    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_tasks_status ON tasks(status, priority, created_at);
CREATE INDEX idx_tasks_project ON tasks(project_ref);
CREATE INDEX idx_tasks_parent ON tasks(parent_id);

CREATE TABLE plans (
  id                  TEXT PRIMARY KEY,
  task_id             TEXT NOT NULL REFERENCES tasks(id),
  version             INTEGER NOT NULL,
  kind                TEXT NOT NULL DEFAULT 'execute' CHECK (kind IN ('execute','decompose')),
  planner_agent       TEXT,
  findings             TEXT,
  writes_fingerprint TEXT,
  steps               TEXT NOT NULL,
  writes             TEXT,
  risks               TEXT,
  rollback            TEXT,
  estimate            TEXT,
  context_refs        TEXT,
  recurring            INTEGER NOT NULL DEFAULT 0,
  status              TEXT NOT NULL DEFAULT 'awaiting_approval'
                      CHECK (status IN ('awaiting_approval','approved','rejected','superseded')),
  review_note         TEXT,
  decided_by          TEXT,
  decided_at          TEXT,
  created_at          TEXT NOT NULL,
  UNIQUE (task_id, version)
);
CREATE INDEX idx_plans_status ON plans(status);

CREATE TABLE runs (
  id            TEXT PRIMARY KEY,
  task_id       TEXT NOT NULL REFERENCES tasks(id),
  plan_id       TEXT REFERENCES plans(id),
  kind          TEXT NOT NULL CHECK (kind IN ('plan','execute','verify')),
  agent_id      TEXT,
  driver        TEXT,
  attempt       INTEGER NOT NULL DEFAULT 1,
  lease_until   TEXT,
  heartbeat_at  TEXT,
  started_at    TEXT NOT NULL,
  ended_at      TEXT,
  status        TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running','succeeded','failed','timeout','abandoned')),
  tokens_in     INTEGER,
  tokens_out    INTEGER,
  cost          REAL,
  exit_code     INTEGER,
  brief_path   TEXT,
  log_path      TEXT
);
CREATE INDEX idx_runs_task ON runs(task_id);
CREATE INDEX idx_runs_status ON runs(status, lease_until);

CREATE TABLE results (
  run_id        TEXT PRIMARY KEY REFERENCES runs(id),
  outcome       TEXT NOT NULL CHECK (outcome IN ('success','partial','failure','noop')),
  summary       TEXT NOT NULL,
  documentation TEXT NOT NULL,
  artifacts     TEXT,
  learnings     TEXT,
  decisions     TEXT,
  blockers      TEXT,
  proposals     TEXT,
  next_intent   TEXT,
  verified      INTEGER,
  verifier_note TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE agents (
  id              TEXT PRIMARY KEY,
  role            TEXT NOT NULL,
  driver          TEXT NOT NULL,
  skills          TEXT,
  spec            TEXT,
  task_classes    TEXT,
  max_concurrency INTEGER NOT NULL DEFAULT 1,
  status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','retired')),
  created_at      TEXT NOT NULL,
  retired_reason  TEXT
);

CREATE TABLE schedules (
  id             TEXT PRIMARY KEY,
  kind           TEXT NOT NULL CHECK (kind IN ('planner','executor','verifier','recurring')),
  cron           TEXT NOT NULL,
  agent_id       TEXT REFERENCES agents(id),
  plan_id        TEXT REFERENCES plans(id),
  enabled        INTEGER NOT NULL DEFAULT 1,
  last_run_at    TEXT,
  next_run_at    TEXT,
  failure_streak INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL
);

CREATE TABLE events (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT NOT NULL,
  entity      TEXT NOT NULL,
  entity_id   TEXT NOT NULL,
  from_status TEXT,
  to_status   TEXT,
  actor       TEXT NOT NULL,
  note        TEXT
);
CREATE INDEX idx_events_entity ON events(entity, entity_id);

CREATE TABLE messages (
  id           TEXT PRIMARY KEY,
  ts           TEXT NOT NULL,
  sender       TEXT NOT NULL,
  to_ref       TEXT,
  task_id      TEXT REFERENCES tasks(id),
  body         TEXT NOT NULL,
  ack_required INTEGER NOT NULL DEFAULT 0,
  acked_at     TEXT
);
CREATE INDEX idx_messages_task ON messages(task_id);

CREATE TABLE task_class_stats (
  task_class          TEXT PRIMARY KEY,
  plans               INTEGER NOT NULL DEFAULT 0,
  approved_first_try  INTEGER NOT NULL DEFAULT 0,
  rejected            INTEGER NOT NULL DEFAULT 0,
  runs                INTEGER NOT NULL DEFAULT 0,
  verified_success    INTEGER NOT NULL DEFAULT 0,
  last_failure_at     TEXT,
  autonomy_level      INTEGER NOT NULL DEFAULT 0
);
