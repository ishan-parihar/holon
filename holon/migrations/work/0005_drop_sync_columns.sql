-- tasks.ext_id / tasks.sync_dirty were reserved for a dashboard. The Notion mirror
-- (adapters/notion) keeps its own page map and diffs by hash, so nothing reads them.
ALTER TABLE tasks DROP COLUMN ext_id;
ALTER TABLE tasks DROP COLUMN sync_dirty;
