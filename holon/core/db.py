"""SQLite access + forward-only migrations.

Migrations live in holon/migrations/<db>/NNNN_name.sql and are applied in order,
each in its own transaction, recorded in `schema_migrations`.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Iterator

BUSY_TIMEOUT_MS = 15000


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_MS / 1000,
                               isolation_level=None)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


@contextmanager
def write_txn(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE: takes the write lock up front so claim-then-update is atomic."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def has_fts5(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE temp._fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def migrate(conn: sqlite3.Connection, db_name: str) -> list[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    pkg = resources.files("holon.migrations").joinpath(db_name)
    files = sorted(p for p in pkg.iterdir() if p.name.endswith(".sql"))
    applied = []
    fts = has_fts5(conn)
    for f in files:
        if f.name in done:
            continue
        sql = f.read_text()
        if ".fts." in f.name and not fts:
            sql = ""  # FTS migrations are optional; search falls back to LIKE
        script = f"BEGIN IMMEDIATE;\n{sql}\nINSERT INTO schema_migrations VALUES ('{f.name}', '{now()}');\nCOMMIT;"
        try:
            conn.executescript(script)
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(f.name)
    return applied
