-- 0004: repair api_memory where an out-of-tree writer created it wrong (found on the live
-- workstation instance, 2026-10-17: the view selected `created_at`, a column no migration
-- ever defined — `items` carries `valid_from` — so `holon lint --strict` died with
-- "no such column: created_at" instead of checking the read API).
--
-- The shipped definition (0003) is authoritative and correct; this migration re-asserts it
-- on any database whose view drifted, fresh ones included (a no-op there — the view already
-- matches). Idempotent, and it adds no column: a v3/v4 consumer reading by name is unchanged.

DROP VIEW IF EXISTS api_memory;

CREATE VIEW api_memory AS
SELECT id, scope, kind, content, provenance, confidence, pinned, valid_from
FROM items WHERE superseded_by IS NULL;
