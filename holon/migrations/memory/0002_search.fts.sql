-- Optional full-text search over memory (skipped when SQLite lacks FTS5).

CREATE VIRTUAL TABLE items_fts USING fts5(content, content='items', content_rowid='rowid', tokenize='porter unicode61');
CREATE TRIGGER items_fts_ai AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER items_fts_ad AFTER DELETE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER items_fts_au AFTER UPDATE OF content ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
  INSERT INTO items_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE VIRTUAL TABLE episodes_fts USING fts5(digest, content='episodes', content_rowid='rowid', tokenize='porter unicode61');
CREATE TRIGGER episodes_fts_ai AFTER INSERT ON episodes BEGIN
  INSERT INTO episodes_fts(rowid, digest) VALUES (new.rowid, new.digest);
END;
