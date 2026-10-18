-- 0005: recall_log (R-2's sensor): every `holon recall` records its question and per-source hit
-- counts, so cross-domain recall misses become measurable instead of narrated. The memory
-- capability stays the only writer; reads are free.

CREATE TABLE recall_log (
  id        TEXT PRIMARY KEY,
  ts        TEXT NOT NULL,
  question  TEXT NOT NULL,
  items     INTEGER NOT NULL DEFAULT 0,
  episodes  INTEGER NOT NULL DEFAULT 0,
  skills    INTEGER NOT NULL DEFAULT 0,
  playbooks INTEGER NOT NULL DEFAULT 0,
  gold      INTEGER NOT NULL DEFAULT 0
);
