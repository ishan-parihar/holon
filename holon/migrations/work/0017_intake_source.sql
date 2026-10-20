-- 0017: an intake key can file work, so a work node may say an intake filed it (G4).
-- The source column's CHECK lists the ways work enters the graph; `intake` joins them.
--
-- The table is rebuilt because SQLite cannot alter a CHECK constraint in place. The
-- definition below is the current one, verbatim, with ONE change: the source CHECK
-- gains 'intake'. The rows are copied unchanged.
--
-- The read API's views over tasks are dropped FIRST: SQLite drops a view whose table
-- disappears, but the copy below reads the old table, so the views must be gone before
-- the table is replaced. api_board and api_approvals both read tasks.
DROP VIEW IF EXISTS api_board;
DROP VIEW IF EXISTS api_approvals;

CREATE TABLE "tasks_new" (
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
  source         TEXT NOT NULL CHECK (source IN ('chair','agent','audit','schedule','kernel','intake')),
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
  updated_at     TEXT NOT NULL, lane TEXT NOT NULL DEFAULT 'full' CHECK (lane IN ('script','fast','full')), objective_id TEXT REFERENCES tasks(id), playbook TEXT, playbook_version INTEGER);

INSERT INTO "tasks_new" SELECT
  id, root_id, parent_id, depth, stage, dept_id, assignee, approver, requested_by, source, title, intent,
  acceptance, task_class, project_ref, workdir, priority, risk, status, outcome, blocked_reason,
  attempt, max_attempts, grade, grade_floor, grade_proposed, grade_reasons, decided_by, decided_at,
  decision_note, recurring, due, created_at, updated_at, lane, objective_id, playbook, playbook_version
FROM tasks;
DROP TABLE tasks;
ALTER TABLE "tasks_new" RENAME TO tasks;

-- the read API's views, recreated with the definitions they had (0008_read_api_views.sql)
CREATE VIEW api_board AS
SELECT t.id, t.title, t.stage, t.status, t.lane, t.priority, t.risk,
       d.slug AS department, t.assignee, t.approver, t.grade, r.id AS run_id,
       r.lease_until, r.employee_id, r.started_at
FROM tasks t
LEFT JOIN departments d ON d.id = t.dept_id
LEFT JOIN runs r ON r.task_id = t.id AND r.status = 'running'
WHERE t.stage IS NOT NULL AND t.status IN ('ready', 'claimed');

-- pending approvals: the approve stage with the plan output that feeds it (the plan
-- stage is its one `task_needs` predecessor; the newest plan output of that node wins)
CREATE VIEW api_approvals AS
SELECT t.id, t.title, d.slug AS department, t.grade, t.approver, t.risk,
       o.body AS plan, t.created_at
FROM tasks t
LEFT JOIN departments d ON d.id = t.dept_id
LEFT JOIN outputs o ON o.task_id = (SELECT needs_id FROM task_needs WHERE task_id = t.id LIMIT 1)
     AND o.kind = 'plan'
     AND o.created_at = (SELECT MAX(o2.created_at) FROM outputs o2
                         WHERE o2.task_id = o.task_id AND o2.kind = 'plan')
WHERE t.stage = 'approve' AND t.status IN ('ready', 'claimed');
