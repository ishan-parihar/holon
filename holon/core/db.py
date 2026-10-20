"""SQLite access + forward-only migrations.

Migrations live in holon/migrations/<db>/NNNN_name.sql and are applied in order,
each in its own transaction, recorded in `schema_migrations`.
"""

from __future__ import annotations

import hashlib
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


def migration_files(db_name: str) -> list:
    pkg = resources.files("holon.migrations").joinpath(db_name)
    return sorted((p for p in pkg.iterdir() if p.name.endswith((".sql", ".py")) and p.name[0].isdigit()),
                  key=lambda p: p.name)


def checksum(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def migrate(conn: sqlite3.Connection, db_name: str) -> list[str]:
    """Apply pending migrations in order, each in its own transaction. `.sql` files are scripts;
    `.py` files define `up(conn)` for conversions SQL cannot express. Checksums are recorded so
    `holon lint` can prove no applied migration was edited."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
    if "checksum" not in {r[1] for r in conn.execute("PRAGMA table_info(schema_migrations)")}:
        conn.execute("ALTER TABLE schema_migrations ADD COLUMN checksum TEXT")
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    shipped = {f.name for f in migration_files(db_name)}
    unknown = sorted(done - shipped)
    if unknown:
        raise RuntimeError(f"this instance was migrated by a newer Holon (unknown {db_name} migrations: "
                           f"{', '.join(unknown)}); run the checkout that owns it, or upgrade this one")
    # migrations applied before checksums existed: adopt the shipped file as the baseline, once
    unsummed = {r[0] for r in conn.execute("SELECT name FROM schema_migrations WHERE checksum IS NULL")}
    for f in migration_files(db_name):
        if f.name in unsummed:
            conn.execute("UPDATE schema_migrations SET checksum=? WHERE name=?", (checksum(f.read_text()), f.name))
    applied = []
    fts = has_fts5(conn)
    for f in migration_files(db_name):
        if f.name in done:
            continue
        text = f.read_text()
        try:
            if f.name.endswith(".py"):
                ns: dict = {}
                exec(compile(text, f.name, "exec"), ns)
                conn.execute("PRAGMA foreign_keys=OFF")
                conn.execute("BEGIN IMMEDIATE")
                ns["up"](conn)
                conn.execute("INSERT INTO schema_migrations VALUES (?,?,?)", (f.name, now(), checksum(text)))
                conn.execute("COMMIT")
                conn.execute("PRAGMA foreign_keys=ON")
            else:
                sql = "" if ".fts." in f.name and not fts else text  # FTS is optional; search falls back to LIKE
                # A .sql migration may rebuild a table others reference (SQLite cannot alter a CHECK
                # in place), and its DROP fails the FK checks at that statement. Deferring them for
                # the transaction is what a .py migration does with foreign_keys=OFF. The pragma must
                # NOT be prepended to this script: a migration's trigger bodies contain their own
                # `BEGIN … END`, so a leading BEGIN IMMEDIATE would nest inside one and fail. It is
                # set on the connection, and re-set after, so it covers the statements that need it.
                conn.execute("PRAGMA defer_foreign_keys = ON")
                try:
                    conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nINSERT INTO schema_migrations VALUES "
                                       f"('{f.name}', '{now()}', '{checksum(text)}');\nCOMMIT;")
                finally:
                    conn.execute("PRAGMA defer_foreign_keys = OFF")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.execute("PRAGMA foreign_keys=ON")
            raise
        applied.append(f.name)
    return applied


def log_event(conn: sqlite3.Connection, entity: str, entity_id: str, frm: str | None, to: str | None,
              actor: str | None, note: str | None = None) -> None:
    """Append to the audit trail. Each event carries the hash of the previous one, so any edit or
    deletion breaks the chain (`holon doctor` verifies it). Call inside a write transaction."""
    prev = conn.execute("SELECT hash FROM events WHERE hash IS NOT NULL ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = prev[0] if prev else ""
    ts = now()
    h = event_hash(prev_hash, ts, entity, entity_id, frm, to, actor, note)
    conn.execute("INSERT INTO events (ts, entity, entity_id, from_status, to_status, actor, note, prev_hash, hash)"
                 " VALUES (?,?,?,?,?,?,?,?,?)", (ts, entity, entity_id, frm, to, actor, note, prev_hash, h))


def event_hash(prev_hash: str, ts: str, entity: str, entity_id: str, frm: str | None, to: str | None,
               actor: str | None, note: str | None) -> str:
    fields = [prev_hash, ts, entity, entity_id, frm or "", to or "", actor or "", note or ""]
    return hashlib.sha256("\x1f".join(fields).encode()).hexdigest()
