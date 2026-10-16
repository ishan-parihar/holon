-- 0014: K-2 trust path (2026-10-16 red team). A guard's `cmd` was the fixer's own shell — the
-- heartbeat ran an agent-supplied command as its user, which can read the Chair key file when
-- isolation is off. A guard now NAMES a [checks.class.<name>] entry: the Chair authored the
-- command in config, registration refuses a name that does not resolve, and the heartbeat runs
-- the referenced class's commands. The column says what it holds.

ALTER TABLE guards RENAME COLUMN cmd TO "check";
