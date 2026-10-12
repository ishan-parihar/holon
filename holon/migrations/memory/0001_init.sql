-- memory.db: project context. Single writer: holon.capabilities.memory.

CREATE TABLE items (
  id            TEXT PRIMARY KEY,
  scope         TEXT NOT NULL,
  kind          TEXT NOT NULL CHECK (kind IN ('fact','decision','constraint','preference','lesson',
                                              'open_question','glossary','artifact')),
  content       TEXT NOT NULL,
  provenance    TEXT NOT NULL,
  confidence    TEXT NOT NULL DEFAULT 'medium' CHECK (confidence IN ('low','medium','high')),
  fingerprint   TEXT NOT NULL,
  pinned        INTEGER NOT NULL DEFAULT 0,
  uses          INTEGER NOT NULL DEFAULT 0,
  valid_from    TEXT NOT NULL,
  superseded_by TEXT,                 -- successor item id, or 'retired'
  review_by     TEXT
);
CREATE INDEX idx_items_scope ON items(scope, kind);
CREATE UNIQUE INDEX idx_items_live_fp ON items(scope, fingerprint) WHERE superseded_by IS NULL;

CREATE TABLE episodes (
  id      TEXT PRIMARY KEY,
  scope   TEXT NOT NULL,
  task_id TEXT,
  run_id  TEXT,
  ts      TEXT NOT NULL,
  outcome TEXT,
  digest  TEXT NOT NULL
);
CREATE INDEX idx_episodes_scope ON episodes(scope, ts);
CREATE INDEX idx_episodes_task ON episodes(task_id);

CREATE TABLE project_state (
  scope          TEXT PRIMARY KEY,
  summary        TEXT,
  current_focus  TEXT,
  next_steps     TEXT,
  open_questions TEXT,
  updated_at     TEXT NOT NULL,
  updated_by_run TEXT
);

CREATE TABLE links (
  from_ref TEXT NOT NULL,
  to_ref   TEXT NOT NULL,
  rel      TEXT NOT NULL CHECK (rel IN ('depends_on','contradicts','derived_from','about','supersedes')),
  PRIMARY KEY (from_ref, to_ref, rel)
);

-- Fail-open degradation log (adopted from the operant context-injection contract):
-- a context bundle that had to drop or skip a source records it here, never blocks the run.
CREATE TABLE context_log (
  run_id    TEXT PRIMARY KEY,
  ts        TEXT NOT NULL,
  chars     INTEGER NOT NULL,
  budget    INTEGER NOT NULL,
  sections  TEXT NOT NULL,
  degraded  TEXT
);
