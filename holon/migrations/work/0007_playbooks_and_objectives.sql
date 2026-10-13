-- Playbooks and objectives (docs/GENOME.md §12). `playbook` names the department skill a work follows
-- ("routine-<slug>" or "class-<task_class>") and `playbook_version` the version it ran under, so each
-- judgment can be traced to the protocol and rubric it was made against. `objective_id` ties work (and
-- every firing of a routine) to the objective it serves.
ALTER TABLE tasks ADD COLUMN objective_id TEXT REFERENCES tasks(id);
ALTER TABLE tasks ADD COLUMN playbook TEXT;
ALTER TABLE tasks ADD COLUMN playbook_version INTEGER;

-- One row per rubric criterion per judged result: the evidence playbook refinement learns from.
CREATE TABLE judgments (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id    TEXT NOT NULL REFERENCES tasks(id),
  playbook   TEXT,
  version    INTEGER,
  criterion  TEXT NOT NULL,
  p          REAL NOT NULL,
  passed     INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_judgments_playbook ON judgments(playbook, version);
