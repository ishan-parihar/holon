-- 0018: a result can be superseded (2026-10-22 readiness audit). Doctor's `artifacts.dangling`
-- names the remedy — "supersede the result" — and until now that was a command that did not
-- exist: the only answers to a rotted artifact pointer were restoring a /var/tmp scratchpad
-- cleaned by 30-day tmpfiles or writing SQL by hand. `holon task supersede` marks the execute
-- output row withdrawn: the work's history stays as it was, the record's standing changes.
--
-- A nullable column added to an existing table needs no rebuild (SQLite's ADD COLUMN is
-- fine; no CHECK, view or index touches it).

ALTER TABLE outputs ADD COLUMN superseded INTEGER NOT NULL DEFAULT 0;
