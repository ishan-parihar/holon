-- Optional full-text search (skipped when SQLite lacks FTS5; search falls back to LIKE).

CREATE VIRTUAL TABLE tasks_fts USING fts5(title, intent, content='tasks', content_rowid='rowid');
CREATE TRIGGER tasks_fts_ai AFTER INSERT ON tasks BEGIN
  INSERT INTO tasks_fts(rowid, title, intent) VALUES (new.rowid, new.title, new.intent);
END;
CREATE TRIGGER tasks_fts_ad AFTER DELETE ON tasks BEGIN
  INSERT INTO tasks_fts(tasks_fts, rowid, title, intent) VALUES ('delete', old.rowid, old.title, old.intent);
END;
CREATE TRIGGER tasks_fts_au AFTER UPDATE OF title, intent ON tasks BEGIN
  INSERT INTO tasks_fts(tasks_fts, rowid, title, intent) VALUES ('delete', old.rowid, old.title, old.intent);
  INSERT INTO tasks_fts(rowid, title, intent) VALUES (new.rowid, new.title, new.intent);
END;

CREATE VIRTUAL TABLE results_fts USING fts5(summary, documentation, content='results', content_rowid='rowid');
CREATE TRIGGER results_fts_ai AFTER INSERT ON results BEGIN
  INSERT INTO results_fts(rowid, summary, documentation) VALUES (new.rowid, new.summary, new.documentation);
END;
