"""`holon backup` / `holon restore`: a consistent copy of an instance, and putting it back.

Databases are copied with SQLite's online backup API, so a backup taken while employees work is
still consistent. The copy holds work.db, memory.db, holon.toml, drivers, tailored skills,
bylaws profiles, the adapter homes (each body's isolated harness state and working memory),
and a manifest with checksums; run directories only with --with-runs. Secrets
are never included: the Chair key lives outside the instance, and employee keys are hashes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from holon.core.db import now

TREES = ("config", "skills", "bylaws", "adapters", "kb")

# A body's harness home holds the body's own binaries (`aft/aft`, a ~100 MB AFT executable).
# An adapter installer re-downloads them (`install_lib.pin_binary`), so they are not instance state:
# a backup that carries one per retained version fills the disk for nothing and `holon restore`
# would resurrect a binary older than the installed one. Config, logs and memory banks ride along.
SKIP_FILES = frozenset({"aft"})


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def backup(k, dest: str, with_runs: bool = False) -> dict:
    out = Path(dest).expanduser().resolve() / f"holon-backup-{now()[:19].replace(':', '').replace('-', '')}"
    out.mkdir(parents=True)
    for name, conn in (("work.db", k.wconn), ("memory.db", k.mconn)):
        target = sqlite3.connect(out / name)
        conn.backup(target)
        target.close()
    if k.home.config_file.exists():
        shutil.copy2(k.home.config_file, out / "holon.toml")
    trees = list(TREES) + (["execution/runtime/runs"] if with_runs else [])
    for t in trees:
        src = k.home.root / t
        if src.is_dir():
            shutil.copytree(src, out / t, ignore=shutil.ignore_patterns(*SKIP_FILES))
    files = sorted(p for p in out.rglob("*") if p.is_file())
    manifest = {
        "instance_id": k.auth.instance_id, "created_at": now(), "with_runs": with_runs,
        "migrations": {db: [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY name")]
                       for db, conn in (("work", k.wconn), ("memory", k.mconn))},
        "files": {str(p.relative_to(out)): _sha(p) for p in files},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return {"path": str(out), "files": len(files)}


def verify(src: str) -> dict:
    src_p = Path(src).expanduser().resolve()
    manifest = json.loads((src_p / "manifest.json").read_text())
    bad = [f for f, h in manifest["files"].items() if not (src_p / f).exists() or _sha(src_p / f) != h]
    return {"manifest": manifest, "corrupt": bad}


def restore(k, src: str) -> dict:
    """Restore into the open instance. Databases are restored online into the live connections."""
    src_p = Path(src).expanduser().resolve()
    check = verify(src)
    if check["corrupt"]:
        raise ValueError(f"backup is corrupt: {check['corrupt'][:5]}")
    for name, conn in (("work.db", k.wconn), ("memory.db", k.mconn)):
        source = sqlite3.connect(src_p / name)
        source.backup(conn)
        source.close()
    if (src_p / "holon.toml").exists():
        shutil.copy2(src_p / "holon.toml", k.home.config_file)
    for t in TREES + ("execution/runtime/runs",):
        if (src_p / t).is_dir():
            dst = k.home.root / t
            if dst.exists():
                # Keep what the backup does not carry: a body binary the installer owns is deliberately
                # absent, so a plain replace would leave a department whose harness cannot start.
                keep = {p for p in dst.rglob("*") if p.name in SKIP_FILES and p.is_file()}
                others = [p for p in dst.rglob("*") if p.is_file() and p not in keep]
                for p in others:
                    p.unlink()
            shutil.copytree(src_p / t, dst, ignore=shutil.ignore_patterns(*SKIP_FILES),
                            dirs_exist_ok=True)
    return {"restored_from": str(src_p), "instance_id": check["manifest"]["instance_id"]}
