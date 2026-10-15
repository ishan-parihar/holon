-- 0012: K-2 (the incident→guard loop). An incident's verified fix may register one guard:
-- the fixer's `guard` field on the execute result (a name and a shell command). The heartbeat
-- runs every registered guard like a smoke check; a failure reopens the incident (a new
-- incident task with the provenance). A closed incident with neither a guard nor a waiver
-- (`incident:<work_id>:waived` in settings) is a doctor warning: a fix that leaves nothing
-- that re-checks it is a lesson, not a lock.

CREATE TABLE guards (
  work_id    TEXT PRIMARY KEY REFERENCES tasks(id),  -- the incident whose verified fix this guards
  name       TEXT NOT NULL,
  cmd        TEXT NOT NULL,
  timeout    INTEGER,
  created_at TEXT NOT NULL
);
