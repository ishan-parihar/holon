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


def in_home_workdirs(k) -> list[str]:
    """The instance-relative paths that a work names as its workdir, minus the ones a
    fixed tree already carries.

    Measured 2026-10-25 on the live instance: 19 tasks keep their workdir INSIDE
    `$HOLON_HOME` -- the recurring routines' own trees (`notes-digest`,
    `routine-disk-usage`, `routine-notes-digest`, `routines/disk-usage`,
    `routines/notes-digest`, `kb`). The fixed tree set (`TREES`) did not name the
    routine trees, so the bytes those routines wrote did not ride the backup, on the one
    machine where the organization exists nowhere else.

    The rule: the workdir's OWN relative path, not its top-level ancestor. A workdir at
    `routines/disk-usage` carries that path, not the whole `routines` tree -- and a
    workdir inside `execution/runtime/runs/` carries that one run directory, so the
    `with_runs` switch still means what it says.

    A workdir outside the instance is the department's own tree (a repository, a
    workspace) and is deliberately NOT copied: it is another program's files, and a
    restore would overwrite them."""
    root = k.home.root.resolve()
    out: set[str] = set()
    for (wd,) in k.wconn.execute("SELECT DISTINCT workdir FROM tasks WHERE workdir IS NOT NULL AND workdir<>''"):
        p = Path(wd).expanduser().resolve()
        if not p.is_relative_to(root) or p == root:
            continue
        rel = p.relative_to(root)
        if rel.parts[0] in TREES:
            continue  # a fixed tree already carries it
        out.add(str(rel))
    return sorted(out)


def _trees(k, with_runs: bool = False) -> list[str]:
    """The trees a backup carries: the fixed set, plus every in-home workdir, plus the
    run directories when asked. The in-home paths are added even when they are not in
    `TREES`, because a work naming them makes them instance state.

    A workdir already inside a carried tree is not carried twice: the tree already holds
    it. That keeps `with_runs` meaningful -- a workdir inside `execution/runtime/runs/`
    rides the runs tree, and does not add a second entry for the same bytes."""
    trees = list(TREES)
    if with_runs:
        trees.append("execution/runtime/runs")
    covered = [Path(t) for t in trees]
    for t in in_home_workdirs(k):
        p = Path(t)
        if any(p.is_relative_to(c) for c in covered):
            continue
        covered.append(p)
        trees.append(t)
    return trees


def backups(dest: str, keep: int | None = None) -> list[Path]:
    """The backup directories under `dest`, oldest first. `keep` returns only the newest `keep`."""
    found = sorted(d for d in Path(dest).expanduser().resolve().glob("holon-backup-*")
                   if d.is_dir() and (d / "manifest.json").exists())
    return found if keep is None else found[-keep:]


def prune(dest: str, keep: int = 3) -> list[dict]:
    """Drop the older backups, keeping the newest `keep`, and report what was removed.

    A backup that fills the disk it protects is worthless, and nothing had ever removed one: the
    live instance held 443 MB in six directories, 123 MB a day, on the one machine where the
    organization exists nowhere else. The removal is reported rather than silent, so a run that
    lost older history says so.
    """
    found = backups(dest)
    if len(found) <= keep:
        return []
    removed = []
    for old in found[:-keep]:
        size = sum(f.stat().st_size for f in old.rglob("*") if f.is_file())
        shutil.rmtree(old)
        removed.append({"path": str(old), "bytes": size})
    return removed


def backup(k, dest: str, with_runs: bool = False) -> dict:
    root = Path(dest).expanduser().resolve()
    stamp = now()[:19].replace(":", "").replace("-", "")
    out, n = root / f"holon-backup-{stamp}", 0
    while out.exists():
        # two backups in the same second (a manual one beside the heartbeat's, or a re-run)
        # shared a path, and `mkdir` failed — losing the second while reporting a backup taken
        n += 1
        out = root / f"holon-backup-{stamp}-{n}"
    out.mkdir(parents=True)
    for name, conn in (("work.db", k.wconn), ("memory.db", k.mconn)):
        target = sqlite3.connect(out / name)
        conn.backup(target)
        target.close()
    if k.home.config_file.exists():
        shutil.copy2(k.home.config_file, out / "holon.toml")
    trees = _trees(k, with_runs)
    for t in trees:
        src = k.home.root / t
        if src.is_dir():
            shutil.copytree(src, out / t, ignore=shutil.ignore_patterns(*SKIP_FILES))
    files = sorted(p for p in out.rglob("*") if p.is_file())
    manifest = {
        "instance_id": k.auth.instance_id, "created_at": now(), "with_runs": with_runs,
        "migrations": {db: [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY name")]
                       for db, conn in (("work", k.wconn), ("memory", k.mconn))},
        "trees": trees,
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
    # parents before children: a restore that handled `routines` before `routines/disk-usage`
    # would wipe the child it had just restored.
    trees = sorted(set((check["manifest"].get("trees") or list(TREES)) + ["execution/runtime/runs"]),
                   key=lambda t: (t.count("/"), t))
    for t in trees:
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
