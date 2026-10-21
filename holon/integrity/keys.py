"""`holon keys gc` (B-2): collect Chair key files no live instance claims.

Every scratch instance mints a Chair key in the shared `~/.config/holon/keys`, and a scratch
instance's key outlives it: the kernel has no other record that says which instance is still
running, so the only signal is the local instance each key belongs to. An orphan is not a
credential anyone can reach — the key dir is 0700 and each file 0600, so no other user on the
machine can read them — but it is dead weight that no one can reason about, and it invites the
question "is this still live?" on every audit. `holon keys list --orphans` answered it by hand;
this collects them.

Never touched: the key of the instance you are running in (`--home`, or the default), and any
`--keep` name. Collected orphans move into `keys.quarantine/` beside the key dir, 0700, so a
recovery is a `mv` and nothing is unlinked unless the Chair passes `--force`. Dry run by default.
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


def plan(k, keep: list[str] | None = None) -> dict:
    """The key files, which belong to a live local instance, and which are orphans."""
    d = _key_dir(k)
    # The keep set is the running instance's own key (whatever `HOLON_CHAIR_KEY_FILE` names it,
    # which a scratch instance points at a bare filename beside it) plus the Chair's --keep names
    # — written as full filenames or as `<instance>.chair` stems, because both are natural to type.
    mine = Path(k.auth.chair_key_path())
    keep = {Path(n).name for n in (keep or [])}
    keep |= {mine.name, mine.stem + ".chair"}
    files = sorted(d.glob("*.chair")) if d.is_dir() else []
    rows: list[dict] = []
    for f in files:
        rows.append({
            "file": f.name,
            "instance": f.stem,
            "size": f.stat().st_size,
            "mtime": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()[:19],
            "this_instance": f.name in keep,
            "kept": f.name in keep,
        })
    return {"dir": str(d), "keys": rows,
            "orphans": [r for r in rows if not r["kept"]],
            "kept": [r for r in rows if r["kept"]],
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
