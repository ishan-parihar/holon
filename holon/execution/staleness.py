"""Findings fingerprints: detect that the world moved between plan approval and execution.

The planner declares which paths its plan will touch. The kernel hashes their state at
plan submission and again when an executor claims the approved plan; a mismatch marks the
plan stale and sends the task back to planning. Non-path entries (URLs, external systems)
are skipped; with no hashable paths the check is fail-open (fingerprint = None).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_WALK_FILES = 5000


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _hash_file(p: Path) -> str:
    st = p.stat()
    if st.st_size > MAX_FILE_BYTES:
        return f"big:{st.st_size}:{st.st_mtime_ns}"
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _hash_dir(p: Path) -> str:
    head = _git(["rev-parse", "HEAD"], p)
    if head is not None:
        status = _git(["status", "--porcelain", "--", "."], p) or ""
        return "git:" + hashlib.sha256((head + status).encode()).hexdigest()
    h = hashlib.sha256()
    count = 0
    for root, dirs, files in os.walk(p):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "__pycache__", "node_modules"))
        for name in sorted(files):
            fp = Path(root) / name
            try:
                st = fp.stat()
            except OSError:
                continue
            h.update(f"{fp.relative_to(p)}:{st.st_size}:{st.st_mtime_ns}\n".encode())
            count += 1
            if count >= MAX_WALK_FILES:
                return "walk-capped:" + h.hexdigest()
    return "walk:" + h.hexdigest()


def fingerprint(workdir: str | None, writes: list[str] | None) -> str | None:
    if not writes:
        return None
    base = Path(workdir).expanduser() if workdir else None
    parts = []
    for t in sorted(set(writes)):
        if not isinstance(t, str) or "://" in t:
            continue
        p = Path(t).expanduser()
        if not p.is_absolute():
            if base is None:
                continue
            p = base / p
        if not p.exists():
            parts.append(f"{t}=absent")
        elif p.is_file():
            parts.append(f"{t}={_hash_file(p)}")
        elif p.is_dir():
            parts.append(f"{t}={_hash_dir(p)}")
    if not parts:
        return None
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()
