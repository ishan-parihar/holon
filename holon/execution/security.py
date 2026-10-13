"""Who is acting: the Chair, an employee, or nobody in particular.

The kernel's Python API trusts its caller. Everything that enters through the CLI is authenticated:

  run token     minted per claim, bound to (employee, run, stage). Push mode exports it as
                HOLON_RUN_TOKEN; pull mode prints it with the claim. It authenticates that employee for
                the life of the run and lets it submit/heartbeat only that run.
  employee key  issued by the Chair (`holon hr key <emp>`) for pull-mode sessions: HOLON_KEY.
  Chair key     created by `holon init`, stored OUTSIDE the instance (default
                ~/.config/holon/keys/<instance>.chair, mode 0600) so harness profiles that deny ~/.config
                (or run as another user) can never read it. HOLON_CHAIR_KEY overrides the file.

Only hashes are stored in work.db. With no Chair key configured the instance runs in legacy open
mode (the CLI acts as the Chair) and `holon doctor` reports it as an error.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from holon.core.db import now, write_txn
from holon.core.ids import ulid
from holon.core.paths import Home
from holon.core.vocab import CHAIR


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    t = "hrt_" + secrets.token_urlsafe(24)
    return t, digest(t)


class AuthError(PermissionError):
    pass


@dataclass
class Principal:
    kind: str                       # chair | employee | anonymous
    employee_id: str | None = None
    run_id: str | None = None       # set when authenticated by a run token
    legacy: bool = False            # no Chair key configured: open mode

    @property
    def actor(self) -> str:
        if self.kind == "chair":
            return CHAIR
        if self.kind == "employee":
            return f"emp:{self.employee_id}"
        return "anonymous"


class Auth:
    def __init__(self, conn: sqlite3.Connection, home: Home):
        self.conn = conn
        self.home = home

    # ── settings ──────────────────────────────────────────────────────────
    def _get(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def _put(self, key: str, value: str) -> None:
        with write_txn(self.conn):
            self.conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))

    @property
    def instance_id(self) -> str:
        iid = self._get("instance_id")
        if iid is None:
            iid = ulid().lower()
            self._put("instance_id", iid)
        return iid

    @property
    def configured(self) -> bool:
        return self._get("chair_key_hash") is not None

    def chair_key_path(self) -> Path:
        env = os.environ.get("HOLON_CHAIR_KEY_FILE")
        if env:
            return Path(env).expanduser()
        return Path("~/.config/holon/keys").expanduser() / f"{self.instance_id}.chair"

    # ── the Chair key ─────────────────────────────────────────────────────
    def init_chair_key(self, rotate: bool = False) -> Path | None:
        """Create the Chair key (once, or again with rotate=True). Returns its path, or None if it exists."""
        if self.configured and not rotate:
            return None
        key = "hck_" + secrets.token_urlsafe(32)
        path = self.chair_key_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(key + "\n")
        self._put("chair_key_hash", digest(key))
        self._put("chair_key_created_at", now())
        return path

    def _chair_key(self) -> str | None:
        if os.environ.get("HOLON_CHAIR_KEY"):
            return os.environ["HOLON_CHAIR_KEY"].strip()
        try:
            return self.chair_key_path().read_text().strip()
        except OSError:
            return None

    # ── employee keys ─────────────────────────────────────────────────────
    def issue_employee_key(self, employee_id: str) -> str:
        key = "hek_" + secrets.token_urlsafe(32)
        with write_txn(self.conn):
            n = self.conn.execute("UPDATE employees SET key_hash=? WHERE id=? AND status<>'retired'",
                                  (digest(key), employee_id)).rowcount
        if not n:
            raise LookupError(f"no active employee {employee_id}")
        return key

    def revoke_employee_key(self, employee_id: str) -> None:
        with write_txn(self.conn):
            self.conn.execute("UPDATE employees SET key_hash=NULL WHERE id=?", (employee_id,))

    # ── resolution ────────────────────────────────────────────────────────
    def agent_principal(self, token: str | None, key: str | None) -> Principal:
        """Who an agent is, from what it sent the broker: a run token or an employee key, else nobody.
        Never the Chair: the broker runs as the kernel's user, where the Chair key file is readable."""
        if token:
            return self.principal(token)
        if key:
            r = self.conn.execute("SELECT id FROM employees WHERE key_hash=? AND status IN ('active','probation')",
                                  (digest(key.strip()),)).fetchone()
            if r is None:
                raise AuthError("employee key is unknown, revoked, or its employee is inactive")
            return Principal("employee", r["id"])
        return Principal("anonymous")

    def principal(self, token: str | None = None) -> Principal:
        token = token or os.environ.get("HOLON_RUN_TOKEN")
        if token:
            r = self.conn.execute("SELECT id, employee_id, status FROM runs WHERE token_hash=?",
                                  (digest(token),)).fetchone()
            if r is None or r["status"] != "running":
                raise AuthError("run token is unknown or its run has ended")
            return Principal("employee", r["employee_id"], r["id"])
        key = os.environ.get("HOLON_KEY")
        if key:
            r = self.conn.execute("SELECT id FROM employees WHERE key_hash=? AND status IN ('active','probation')",
                                  (digest(key.strip()),)).fetchone()
            if r is None:
                raise AuthError("employee key is unknown, revoked, or its employee is inactive")
            return Principal("employee", r["id"])
        stored = self._get("chair_key_hash")
        if stored is None:
            return Principal("chair", legacy=True)
        chair = self._chair_key()
        if chair and hmac.compare_digest(digest(chair), stored):
            return Principal("chair")
        return Principal("anonymous")
