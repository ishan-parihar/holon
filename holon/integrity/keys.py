"""`holon keys gc` (B-2): collect Chair key files no live instance claims.

Every scratch instance mints a Chair key in the shared `~/.config/holon/keys`, and a scratch
instance's key outlives it: the kernel has no other record that says which instance is still
running, so the only signal is the local instance each key belongs to. An orphan is not a
credential anyone can reach — the key dir is 0700 and each file 0600, so no other user on the
machine can read them — but it is dead weight that no one can reason about, and it invites the
question "is this still live?" on every audit. `holon keys list --orphans` answered it by hand;
this collects them.

Never touched: the key of the instance you are running in (`--home`, or the default), any
`--keep` name, and any key whose own instance database still exists on this machine with a
recorded `chair_key_hash` — that instance is live whether or not it is the one running the sweep.
A scratch instance mints into the shared key dir, so a sweep from one home used to read every
other instance's key as an orphan: measured on the live instance 2026-10-25, that collected a
still-live scratch instance's key and left both systemd units naming a home whose key file no
longer existed, so every `holon tick` for the next 69 minutes was refused at the actor gate ("you
are anonymous") with nothing on any surface saying so. Collected orphans move into
`keys.quarantine/` beside the key dir, 0700, so a recovery is a `mv` and nothing is unlinked
unless the Chair passes `--force`. Dry run by default.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


def _key_dir(k) -> Path:
    """The dir this instance's own Chair key lives in — which `HOLON_CHAIR_KEY_FILE` redirects, so
    a scratch instance's sweep never reaches the operator's real key dir."""
    return Path(k.auth.chair_key_path()).expanduser().resolve().parent


def _live_instance_ids(k) -> dict[str, Path]:
    """Every Holon instance this machine still runs: its instance id -> its home.

    The id is recorded in each instance's own `settings.instance_id` and its Chair key is named for
    it, so a key file's stem is a handle — but it resolves only through a database. The homes a
    machine runs are found where they actually sit: the running instance, `$HOLON_HOME` when it
    names another, and the Holon instances beside it under the same directory. An instance whose
    database is present and claims a chair key hash is live; one whose database is missing is the
    orphan a sweep exists for.
    """
    import sqlite3
    live: dict[str, Path] = {}
    seen: set[str] = set()
    # The homes a machine runs: the running instance, `$HOLON_HOME` when it names another, and the
    # Holon instances sitting beside the running one — which is exactly where a scratch instance
    # lives (the operator's validation homes and the live one are siblings under one directory).
    roots: list[Path] = [Path(k.home.root)]
    default = Path(os.environ.get("HOLON_HOME") or "~/holon").expanduser()
    if default not in roots:
        roots.append(default)
    parent = Path(k.home.root).parent
    if parent.is_dir():
        try:
            roots.extend(sorted(p for p in parent.iterdir()
                                if p.is_dir() and (p / "execution" / "runtime" / "work.db").exists()))
        except OSError:
            pass
    for root in roots:
        db = root / "execution" / "runtime" / "work.db"
        if not db.exists() or str(db) in seen:
            continue
        seen.add(str(db))
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            rows = dict(conn.execute("SELECT key, value FROM settings WHERE key IN "
                                     "('instance_id', 'chair_key_hash')").fetchall())
            iid, h = rows.get("instance_id"), rows.get("chair_key_hash")
        except sqlite3.Error:
            conn.close()
            continue
        conn.close()
        # a database with no chair_key_hash was never `holon init`ed: it claims no key, so a key
        # file named for it is not keeping it alive
        if iid and h:
            live[iid.lower()] = root
    return live


def plan(k, keep: list[str] | None = None) -> dict:
    """The key files, which belong to a live local instance, and which are orphans."""
    d = _key_dir(k)
    # The keep set is the running instance's own key (whatever `HOLON_CHAIR_KEY_FILE` names it,
    # which a scratch instance points at a bare filename beside it) plus the Chair's --keep names
    # — written as full filenames or as `<instance>.chair` stems, because both are natural to type.
    mine = Path(k.auth.chair_key_path())
    keep = {Path(n).name for n in (keep or [])}
    keep |= {mine.name, mine.stem + ".chair"}
    live = _live_instance_ids(k)
    files = sorted(d.glob("*.chair")) if d.is_dir() else []
    rows: list[dict] = []
    for f in files:
        is_live = f.stem.lower() in live
        rows.append({
            "file": f.name,
            "instance": f.stem,
            "size": f.stat().st_size,
            "mtime": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()[:19],
            "this_instance": f.name in keep,
            "live_instance": is_live,
            "kept": f.name in keep or is_live,
        })
    return {"dir": str(d), "keys": rows,
            "orphans": [r for r in rows if not r["kept"]],
            "kept": [r for r in rows if r["kept"]],
            "live": [r["file"] for r in rows if r["live_instance"] and r["file"] not in keep],
            "dir_mode": oct(d.stat().st_mode & 0o777)[2:] if d.is_dir() else None}


def apply(k, p: dict, force: bool = False) -> dict:
    """Delete the orphans `plan` found, into a quarantine dir rather than into nothing.

    Deleting a key that a live instance still uses would lock the Chair out of it, and the
    kernel's only signal for "live" is the local instance. So the deleted files move to
    `keys.quarantine/` beside them and nothing is unlinked: a recovery is a `mv`, and the sweep
    says where it put them. `force=True` unlinks outright (the Chair's call)."""
    d = Path(p["dir"])
    doomed = {r["file"] for r in p["orphans"]}
    q = d / "keys.quarantine"
    removed, kept = [], []
    for f in sorted(d.glob("*.chair")):
        if f.name in doomed:
            if force:
                os.unlink(f)
            else:
                q.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.chmod(q, 0o700)
                shutil.move(str(f), str(q / f.name))
                os.chmod(q / f.name, 0o600)
            removed.append(f.name)
        else:
            kept.append(f.name)
    return {"dir": str(d), "removed": removed, "kept": kept, "quarantine": None if force else str(q),
            "dir_mode": oct(d.stat().st_mode & 0o777)[2:] if d.is_dir() else None}
