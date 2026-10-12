-- work.db: the organization and its work. Single writer: holon.execution.

-- ── the organization ────────────────────────────────────────────────────
CREATE TABLE departments (
  id         TEXT PRIMARY KEY,                 -- dept_<ulid>
  slug       TEXT NOT NULL UNIQUE,             -- e.g. executive, marketing, it
  name       TEXT NOT NULL,
  mission    TEXT,
  parent_id  TEXT REFERENCES departments(id),
  head_id    TEXT,                             -- employees.id of the HOD
  status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed')),
  created_at TEXT NOT NULL
);

CREATE TABLE employees (
  id              TEXT PRIMARY KEY,            -- emp_<ulid>
  name            TEXT NOT NULL,
  title           TEXT NOT NULL,
  dept_id         TEXT NOT NULL REFERENCES departments(id),
  reports_to      TEXT REFERENCES employees(id),
  job_description TEXT NOT NULL,               -- markdown: responsibilities, task classes, skills, KPIs
  duties          TEXT NOT NULL,               -- JSON subset of ["plan","execute","verify","review"]
  task_classes    TEXT,                        -- JSON list; null = any
  skills          TEXT,                        -- JSON list
  driver          TEXT NOT NULL,               -- harness driver name
  max_concurrency INTEGER NOT NULL DEFAULT 1,
  status          TEXT NOT NULL DEFAULT 'probation'
                  CHECK (status IN ('probation','active','paused','retired')),
  hired_at        TEXT NOT NULL,
  hired_reason    TEXT NOT NULL,
  retired_at      TEXT,
  retired_reason  TEXT,
  successor_id    TEXT REFERENCES employees(id)
);
CREATE INDEX idx_employees_dept ON employees(dept_id, status);

-- Who may approve plans of which grade. approver = 'chair' | 'emp:<id>'; task_class '*' = any.
CREATE TABLE authority (
  approver   TEXT NOT NULL,
  task_class TEXT NOT NULL DEFAULT '*',
  max_grade  INTEGER NOT NULL CHECK (max_grade BETWEEN -1 AND 4),   -- -1 = none
  updated_at TEXT NOT NULL,
  reason     TEXT NOT NULL,
  PRIMARY KEY (approver, task_class)
);

-- ── work ────────────────────────────────────────────────────────────────
CREATE TABLE tasks (
  id             TEXT PRIMARY KEY,
  project_ref    TEXT,
  parent_id      TEXT REFERENCES tasks(id),
  dept_id        TEXT REFERENCES departments(id),
  assignee       TEXT REFERENCES employees(id),
  requested_by   TEXT NOT NULL DEFAULT 'chair',
  title          TEXT NOT NULL,
  intent         TEXT NOT NULL,
  acceptance     TEXT,
  priority       INTEGER NOT NULL DEFAULT 3,
  source         TEXT NOT NULL CHECK (source IN ('chair','agent','audit','schedule')),
  task_class     TEXT,
  risk           TEXT NOT NULL DEFAULT 'normal' CHECK (risk IN ('low','normal','high','irreversible')),
  workdir        TEXT,
  status         TEXT NOT NULL DEFAULT 'inbox',
  blocked_reason TEXT,
  max_attempts   INTEGER NOT NULL DEFAULT 2,
  due            TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  ext_id         TEXT,
  sync_dirty     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_tasks_status ON tasks(status, priority, created_at);
CREATE INDEX idx_tasks_dept ON tasks(dept_id, status);
CREATE INDEX idx_tasks_assignee ON tasks(assignee, status);
CREATE INDEX idx_tasks_project ON tasks(project_ref);
CREATE INDEX idx_tasks_parent ON tasks(parent_id);

CREATE TABLE plans (
  id                 TEXT PRIMARY KEY,
  task_id            TEXT NOT NULL REFERENCES tasks(id),
  version            INTEGER NOT NULL,
  kind               TEXT NOT NULL DEFAULT 'execute' CHECK (kind IN ('execute','decompose')),
  author             TEXT,                       -- employee id (or actor) who planned
  findings           TEXT,
  writes_fingerprint TEXT,
  steps              TEXT NOT NULL,
  writes             TEXT,                       -- JSON: paths the plan will modify
  external_effects   TEXT,                       -- JSON: effects outside the machine (email, posts, payments…)
  assignee           TEXT REFERENCES employees(id),   -- who should execute
  risks              TEXT,
  rollback           TEXT,
  estimate           TEXT,
  context_refs       TEXT,
  grade_proposed     INTEGER,
  grade_floor        INTEGER NOT NULL DEFAULT 0,
  grade              INTEGER NOT NULL DEFAULT 4 CHECK (grade BETWEEN 0 AND 4),
  grade_reasons      TEXT,                       -- JSON list
  approver           TEXT NOT NULL DEFAULT 'chair',   -- 'chair' | 'emp:<id>' required to decide
  recurring          INTEGER NOT NULL DEFAULT 0,
  status             TEXT NOT NULL DEFAULT 'awaiting_approval'
                     CHECK (status IN ('awaiting_approval','approved','rejected','superseded')),
  review_note        TEXT,
  decided_by         TEXT,
  decided_at         TEXT,
  created_at         TEXT NOT NULL,
  UNIQUE (task_id, version)
);
CREATE INDEX idx_plans_status ON plans(status, approver);
CREATE INDEX idx_plans_decider ON plans(decided_by);

CREATE TABLE runs (
  id           TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL REFERENCES tasks(id),
  plan_id      TEXT REFERENCES plans(id),
  kind         TEXT NOT NULL CHECK (kind IN ('plan','execute','verify','review')),
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
  log_path     TEXT
);
CREATE INDEX idx_runs_task ON runs(task_id);
CREATE INDEX idx_runs_status ON runs(status, lease_until);
CREATE INDEX idx_runs_employee ON runs(employee_id, kind, status);

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
  verified_by   TEXT,
  created_at    TEXT NOT NULL
);

-- Holdout: a deterministic sample of employee approvals that the Chair re-judges.
CREATE TABLE audit_samples (
  plan_id    TEXT PRIMARY KEY REFERENCES plans(id),
  approver   TEXT NOT NULL,
  sampled_at TEXT NOT NULL,
  verdict    TEXT CHECK (verdict IN ('agree','disagree')),
  note       TEXT,
  judged_at  TEXT
);

CREATE TABLE schedules (
  id             TEXT PRIMARY KEY,
  kind           TEXT NOT NULL CHECK (kind IN ('plan','execute','verify','review','recurring')),
  cron           TEXT NOT NULL,
  employee_id    TEXT REFERENCES employees(id),
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

-- Communication between employees, departments, and the Chair.
CREATE TABLE messages (
  id           TEXT PRIMARY KEY,
  ts           TEXT NOT NULL,
  sender       TEXT NOT NULL,                  -- 'chair' | 'emp:<id>'
  to_ref       TEXT NOT NULL,                  -- 'chair' | 'emp:<id>' | 'dept:<slug>' | 'org'
  kind         TEXT NOT NULL DEFAULT 'dm' CHECK (kind IN ('dm','channel','request','question','answer')),
  task_id      TEXT REFERENCES tasks(id),
  thread_id    TEXT,
  body         TEXT NOT NULL,
  ack_required INTEGER NOT NULL DEFAULT 0,
  acked_at     TEXT
);
CREATE INDEX idx_messages_to ON messages(to_ref, ts);
CREATE INDEX idx_messages_task ON messages(task_id);
