-- 0003: api_memory (read API v3, K-4): the organization's live decisions and knowledge, by
-- scope, on the memory database itself (SQLite views cannot reach across attached databases,
-- so this view lives here; consumers open or attach memory.db read-only). The memory capability
-- remains the only writer; the read API is a view over items, exactly as api_board is over tasks.

CREATE VIEW api_memory AS
SELECT id, scope, kind, content, provenance, confidence, pinned, valid_from
FROM items WHERE superseded_by IS NULL;
