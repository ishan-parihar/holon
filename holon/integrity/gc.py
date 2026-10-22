"""`holon gc` (H-14): collect what `[retention]` says is expired — run directories of closed
work and old messages — keeping what the organization still learns from (a gold standard's and
a judged run's result.json and trajectory.json). Dry run by default; `--apply` refuses unless
a backup newer than `[retention] backup_max_age_days` exists (`--force` overrides): it never
deletes un-backed data.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

KEEP_ALWAYS = ("result.json", "trajectory.json")   # the gold standard and the judge read these


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def plan(k) -> dict:
    """What gc would delete, and the state of the backups it requires before it will."""
    r = k.config.get("retention", {})
    runs_days, msgs_days = int(r.get("runs_days", 0)), int(r.get("messages_days", 0))
    now = datetime.now(timezone.utc)
    out: dict = {"runs_days": runs_days, "messages_days": msgs_days, "run_dirs": [], "messages": 0}
    backup_to = Path(k.config["healing"]["backup_to"] or str(k.home.root / "backups")).expanduser()
    backups = sorted(backup_to.glob("holon-backup-*")) if backup_to.is_dir() else []
    newest = backups[-1] if backups else None
    age_days = ((now - datetime.fromtimestamp(newest.stat().st_mtime, timezone.utc)).days
                if newest else None)
    out["backup"] = {"newest": str(newest) if newest else None, "age_days": age_days}
    out["backup_dir"] = str(backup_to)
    if runs_days > 0:
        cutoff = _iso(now - timedelta(days=runs_days))
        for run in k.wconn.execute(
                "SELECT r.id AS run_id, r.task_id, t.stage AS stage, w.id AS work_id, w.status AS work_status"
                " FROM runs r JOIN tasks t ON t.id = r.task_id"
                " JOIN tasks w ON w.id = t.parent_id"
                " WHERE r.ended_at IS NOT NULL AND r.ended_at < ? AND w.status IN ('done','cancelled')",
                (cutoff,)).fetchall():
            d = k.home.run_dir(run["run_id"])
            if not d.is_dir():
                continue
            judged = k.wconn.execute("SELECT 1 FROM judgments WHERE work_id=? LIMIT 1",
                                     (run["work_id"],)).fetchone()
            gold = k.work.gold_standard(k.work.get(run["work_id"])) or {}
            gold_run = gold.get("run_id")
            keep = (KEEP_ALWAYS if run["stage"] == "execute" and (judged or gold_run == run["run_id"])
                    else ())
            out["run_dirs"].append({"run": run["run_id"], "work": run["work_id"], "dir": str(d),
                                    "keep": list(keep)})
    if msgs_days > 0:
        out["messages"] = k.wconn.execute(
            "SELECT count(*) FROM messages WHERE ts < ?",
            (_iso(now - timedelta(days=msgs_days)),)).fetchone()[0]
    return out


def apply(k, p: dict, force: bool = False) -> dict:
    """Delete what `plan` said. Refuses without a recent backup — old data is the only copy
    of what the organization learned; `--force` is the Chair's call to make anyway."""
    import shutil
    max_age = int(k.config.get("retention", {}).get("backup_max_age_days", 7))
    age = p["backup"]["age_days"]
    if not force and (age is None or age > max_age):
        raise PermissionError(
            f"no backup newer than {max_age} days (newest: {p['backup']['newest'] or 'none'}, "
            f"{age if age is not None else 'never'} days old); run `holon backup --to …` first, or --force")
    removed, kept = 0, 0
    for item in p["run_dirs"]:
        d = Path(item["dir"])
        if item["keep"]:
            for f in list(d.iterdir()):
                if f.name not in item["keep"]:
                    f.unlink() if f.is_file() else shutil.rmtree(f, ignore_errors=True)
            kept += 1
        else:
            shutil.rmtree(d, ignore_errors=True)
        removed += 1
    msgs = 0
    if p["messages_days"] > 0 and p["messages"]:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=p["messages_days"]))
        cur = k.wconn.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
        msgs = cur.rowcount
    k.auth._put("gc:last_run", _iso(datetime.now(timezone.utc)))   # doctor nudges when this goes stale
    from holon.integrity import backup as backup_mod
    pruned = backup_mod.prune(str(p["backup_dir"]),
                              int(k.config.get("retention", {}).get("backup_keep", 3)))
    return {"run_dirs_removed": removed, "run_dirs_trimmed_to_kept_files": kept,
            "messages_deleted": msgs,
            "backups_pruned": [{"path": str(Path(r["path"]).name), "bytes": r["bytes"]} for r in pruned]}
