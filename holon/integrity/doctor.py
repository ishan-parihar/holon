"""`holon doctor`: checks an instance's live state. lint.py checks artifacts; this checks data.

Each finding is {severity: error | warning, check, message}. Errors mean the organization's
guarantees do not hold right now and need the Chair.
"""

from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path

from holon.core import vocab
from holon.core.db import connect, event_hash, now
from holon.execution import autonomy

# the strategy seam's versions this Holon reads (CONTRACT §9a); a store declaring another is
# reported, not guessed at
EXTERNAL_STORE_VERSIONS = ("1",)


def _isolation(k, add) -> None:
    """The trust boundary (docs/GENOME.md §11), checked from the agent's side: the runner is asked, as the
    agent user, who it is and what it can reach."""
    if not k.auth.configured:
        return
    if not k.isolation:
        add("warning", "isolation.off", "agents run as the kernel's user: one with a shell can read the Chair key and "
            "write work.db (set [isolation] agent_user; `holon isolation plan`)")
        return
    from holon.execution import isolation
    try:
        p = isolation.probe(k)
    except (isolation.IsolationError, OSError) as e:
        add("error", "isolation.runner", f"the runner does not answer ({e}): no agent can run")
        return
    if p["error"] or p["user"] != k.isolation["agent_user"]:
        add("error", "isolation.runner", f"the runner runs as {p['user']!r}, not {k.isolation['agent_user']!r}"
            + (f" ({p['error']})" if p["error"] else ""))
    for f in p["readable_secrets"]:
        add("error", "isolation.secrets", f"the agent user can read {f}")
    for f in p["unwritable"]:
        add("error", "isolation.reach", f"the agent user cannot write {f}, which is in its reach (holon isolation apply)")
    for f in p["unreadable"]:
        add("error", "isolation.reach", f"the agent user cannot read {f}, which it needs (holon isolation apply)")
    try:
        with isolation.connect(k.isolation["broker"], timeout=5):
            pass
    except isolation.IsolationError as e:
        add("error", "isolation.broker", f"{e}: agents cannot reach the organization (systemctl --user start "
            "holon-broker)")


def run(k) -> list[dict]:
    out: list[dict] = []

    def add(severity: str, check: str, message: str) -> None:
        out.append({"severity": severity, "check": check, "message": message})

    w = k.wconn
    q = lambda sql, *a: w.execute(sql, a).fetchall()  # noqa: E731

    # the dormant strategy seam (R-6, CONTRACT §9a): a present store must declare a version the
    # kernel knows; an absent one is fine (the bundle's joins fail open and log it)
    for name, path in (("trajectory.db", k.home.trajectory_db), ("logbook.db", k.home.logbook_db)):
        if not path.exists():
            continue
        try:
            conn = connect(path, readonly=True)
            row = conn.execute("SELECT value FROM meta WHERE key='api_version'").fetchone()
            conn.close()
        except sqlite3.Error as e:
            add("warning", "extstore.version", f"{name} exists but does not declare an api_version ({e}); "
                "the bundle joins its schema blind (CONTRACT §9a)")
            continue
        if row is None or row[0] not in EXTERNAL_STORE_VERSIONS:
            v = row[0] if row else "none"
            add("warning", "extstore.version", f"{name} api_version {v!r} is not one this Holon knows "
                f"({', '.join(EXTERNAL_STORE_VERSIONS)}); the bundle may read it wrong")

    _isolation(k, add)
    # which checkout owns this instance (H-09's silent half): two checkouts sharing one
    # launcher made a stale checkout the live installation without a word
    recorded = None
    try:
        recorded = k.wconn.execute("SELECT value FROM settings WHERE key='code_root'").fetchone()
    except sqlite3.OperationalError:
        recorded = None
    if recorded:
        running = str(Path(__file__).resolve().parents[1])
        if recorded[0] != running:
            add("warning", "code.root",
                f"this instance was last migrated from {recorded[0]}, but is running from {running} — "
                "one of them is stale (install.py --repoint, or merge that checkout)")
    for r in k.smoke_results():
        if not r["ok"]:
            add("error" if r["critical"] else "warning", "smoke.critical" if r["critical"] else "smoke.check",
                f"{r['dept'] or 'org'}: {r['name']} failed (exit {r['exit_code']})"
                + (f" — {r['reason']}" if r["reason"] else "") + (f": {r['tail'][-200:]}" if r["tail"] else "")
                + (" — its agents wait until it passes" if r["critical"] else ""))

    # K-2: a closed incident with neither a registered guard nor a waiver
    for t in q("SELECT id, title FROM tasks WHERE status='done' AND task_class='incident' AND stage IS NULL"):
        if q("SELECT 1 FROM guards WHERE work_id=?", t["id"]):
            continue
        if k._setting(f"incident:{t['id']}:waived"):
            continue
        add("warning", "incident.guard",
                f"{t['title']}: closed without a guard or a waiver — the fix leaves nothing that "
                "re-checks it (a `guard` field on the fix's result, or `holon incident waive {t['id']}`)")

    # H-14: collection is the Chair's manual act — say so when configured retention is overdue
    r = k.config.get("retention", {})
    windows = [int(r.get(k, 0)) for k in ("runs_days", "messages_days")]
    if any(w > 0 for w in windows):
        from datetime import datetime, timedelta, timezone
        window = min(w for w in windows if w > 0)
        last = k._setting("gc:last_run")
        if not last or last < (datetime.now(timezone.utc) - timedelta(days=window)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"):
            add("warning", "retention.overdue",
                f"no `holon gc --apply` in over {window} days (last: {last or 'never'}) — run directories and "
                "messages past [retention] sit uncollected; dry-run `holon gc` first, back up, then apply")

    # U1/U3: the genome's synced view must not be older than its source, nor the registry than the sync
    from holon.genome import units as units_mod
    manifests, _ = units_mod.load(k.home)
    if manifests:
        newest = max(u["mtime"] for u in manifests.values())
        row = w.execute("SELECT max(synced_at) FROM units").fetchone()
        if not row[0] or row[0] < newest:
            add("warning", "genome.stale",
                f"a unit manifest changed after the last `holon genome sync` ({newest[:19]}): the tables, "
                "pooled reach and registry are a stale view of the genome")
        reg = k.home.genome_registry
        if row[0] and reg.exists():
            from datetime import datetime, timezone
            reg_at = datetime.fromtimestamp(reg.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            if reg_at < row[0]:
                add("warning", "genome.registry",
                    "genome/registry.md is older than the last sync: `holon genome emit` regenerates it")

    # C3: a surface whose health probe fails is down for everyone who pools it — name the
    # departments, so a planner routes around the dead tier before spending anything. The
    # convention: a surface's health runs as a smoke check named "<unit>:<service>"
    # ([smoke.dept.platform-ops] runs each surface's health on the fleet's interval).
    failed = {r["name"]: r for r in k.smoke_results() if not r["ok"]}
    for slug, u in units_mod.load(k.home)[0].items():
        if u["category"] != "surface":
            continue
        for s in u["services"]:
            r = failed.get(f"{slug}:{s['id']}")
            if r:
                who = "every department" if "*" in u["pool_of"] else ", ".join(u["pool_of"] or ["nobody"])
                add("warning", "surface.down",
                    f"{slug}:{s['id']}: its health probe failed (exit {r['exit_code']}) — pooled by: {who}"
                    " — briefs mark the route (down); route around it")

    # databases
    for name, conn in (("work.db", k.wconn), ("memory.db", k.mconn)):
        r = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if r != "ok":
            add("error", "db.integrity", f"{name}: {r}")
        for row in conn.execute("PRAGMA foreign_key_check").fetchall()[:20]:
            add("error", "db.foreign_keys", f"{name}: {tuple(row)}")

    # graph invariants
    for r in q("SELECT t.id FROM tasks t WHERE t.status='claimed' AND (SELECT count(*) FROM runs r "
               "WHERE r.task_id=t.id AND r.status='running') <> 1"):
        add("error", "graph.claimed_run", f"{r[0]} is claimed but does not have exactly one running run")
    for r in q("SELECT r.id, t.id, t.status FROM runs r JOIN tasks t ON t.id=r.task_id "
               "WHERE r.status='running' AND t.status<>'claimed'"):
        add("error", "graph.claimed_run", f"run {r[0]} is running but its stage {r[1]} is {r[2]}")
    for r in q("SELECT s.id FROM tasks s LEFT JOIN tasks w ON w.id=s.parent_id "
               "WHERE s.stage IS NOT NULL AND (w.id IS NULL OR w.stage IS NOT NULL)"):
        add("error", "graph.stage_parent", f"stage {r[0]} is not inside a work node")
    for r in q("SELECT w.id, w.title FROM tasks w WHERE w.stage IS NULL AND w.status='open' AND NOT EXISTS "
               "(SELECT 1 FROM tasks s WHERE s.parent_id=w.id AND s.status IN ('ready','claimed'))"):
        add("error", "graph.stuck", f"open work {r[0]} ({r[1]}) has no ready or claimed stage")
    for r in q("SELECT w.id FROM tasks w WHERE w.stage IS NULL AND w.status='waiting' AND NOT EXISTS "
               "(SELECT 1 FROM tasks c WHERE c.parent_id=w.id AND c.stage IS NULL)"):
        add("error", "graph.stuck", f"work {r[0]} waits on sub-tasks but has none")
    for r in q("SELECT w.id FROM tasks w WHERE w.status='blocked' AND NOT EXISTS (SELECT 1 FROM task_needs n "
               "JOIN tasks d ON d.id=n.needs_id WHERE n.task_id=w.id AND d.status<>'done')"):
        add("error", "graph.stuck", f"work {r[0]} is blocked though everything it needs is done")
    for r in q("SELECT a.id, a.approver FROM tasks a WHERE a.stage='approve' AND a.status IN ('ready','claimed') "
               "AND a.approver LIKE 'emp:%' AND NOT EXISTS (SELECT 1 FROM employees e WHERE 'emp:' || e.id = "
               "a.approver AND e.status IN ('active','probation'))"):
        add("error", "graph.approver", f"approval {r[0]} waits on {r[1]}, who is not active")
    # a ready stage nobody can claim waits forever: its assignee is out, or no one in reach performs it
    staff: dict[str | None, set[str]] = {}
    for e in q("SELECT dept_id, roles FROM employees WHERE status IN ('active','probation')"):
        staff.setdefault(e[0], set()).update(s for role in json.loads(e[1] or "[]") for s in vocab.ROLE_STAGES[role])
    working = {r[0] for r in q("SELECT id FROM employees WHERE status IN ('active','probation')")}
    exec_dept = (q("SELECT id FROM departments WHERE slug=?", vocab.EXECUTIVE) or [[None]])[0][0]
    for r in q("SELECT id, stage, dept_id, assignee FROM tasks WHERE status='ready' AND stage IS NOT NULL "
               "AND stage<>'approve'"):
        if r[3]:
            if r[3] not in working:
                add("error", "graph.unstaffed", f"{r[1]} stage {r[0]} is assigned to {r[3]}, who is not active")
            continue
        reach = staff.get(r[2], set()) | (staff.get(exec_dept, set()) if r[2] is None or r[1] == "verify" else set())
        if r[1] not in reach:
            add("error", "graph.unstaffed", f"{r[1]} stage {r[0]}: no active employee who can claim it performs '{r[1]}'")

    # separation of duties, in history
    for r in q("SELECT a.id, a.decided_by FROM tasks a JOIN task_needs n ON n.task_id=a.id JOIN outputs o "
               "ON o.task_id=n.needs_id WHERE a.stage='approve' AND a.decided_by IS NOT NULL"):
        author = json.loads(w.execute("SELECT body FROM outputs WHERE task_id=(SELECT needs_id FROM task_needs "
                                      "WHERE task_id=?)", (r[0],)).fetchone()[0]).get("author")
        if author and r[1] == f"emp:{author}":
            add("error", "separation.approve", f"{r[0]}: {r[1]} approved their own plan")
    for r in q("SELECT o.task_id, o.verified_by, u.employee_id FROM outputs o JOIN runs u ON u.id=o.run_id "
               "WHERE o.kind='execute' AND o.verified_by = 'emp:' || u.employee_id"):
        add("error", "separation.verify", f"{r[0]}: {r[1]} verified their own work")
    for r in q("SELECT approver, task_class, max_grade FROM authority WHERE max_grade NOT BETWEEN -1 AND 4"):
        add("error", "authority.range", f"{r[0]} on {r[1]}: G{r[2]}")

    # budgets
    for d in k.org.depts():
        if d["id"] in k.over_budget():
            waiting = w.execute("SELECT count(*) FROM tasks WHERE dept_id=? AND stage IS NOT NULL AND status='ready'",
                                (d["id"],)).fetchone()[0]
            if waiting:
                add("warning", "budget.cap", f"{d['slug']} is at its budget cap with {waiting} ready stages waiting")

    # leases
    n = w.execute("SELECT count(*) FROM runs WHERE status='running' AND lease_until < ?", (now(),)).fetchone()[0]
    if n:
        add("warning", "runs.expired", f"{n} runs past their lease: the heartbeat (`holon tick`) is not running")

    # the audit trail
    prev = ""
    for e in q("SELECT * FROM events WHERE hash IS NOT NULL ORDER BY seq"):
        expected = event_hash(e["prev_hash"] or "", e["ts"], e["entity"], e["entity_id"], e["from_status"],
                              e["to_status"], e["actor"], e["note"])
        if e["prev_hash"] != prev or e["hash"] != expected:
            add("error", "audit.chain", f"event {e['seq']} breaks the hash chain (edited, deleted, or inserted)")
            break
        prev = e["hash"]

    # security
    if not k.auth.configured:
        add("error", "security.auth", "no Chair key: the CLI runs in open mode (anyone acts as the Chair); "
                                      "run `holon init`")
    else:
        path = k.auth.chair_key_path()
        if path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
            add("error", "security.chair_key", f"{path} is readable by others (chmod 600)")
        try:
            path.relative_to(k.home.root)
            add("error", "security.chair_key", "the Chair key lives inside the instance, where agents may read it")
        except ValueError:
            pass
    mode = stat.S_IMODE(k.home.root.stat().st_mode)
    if mode & 0o002:
        add("error", "security.home", f"{k.home.root} is world-writable")
    try:
        autonomy.policy(k.config)
    except ValueError as e:
        add("error", "config.autonomy", str(e))
    from holon.execution import drivers
    try:
        for d in drivers.available(k.home).values():
            if d.scope_fingerprint and d.scope_fingerprint != k.scope_fingerprint():
                add("error", "scope.drift", f"driver {d.name} was installed for another [scope] "
                                            f"({d.scope_fingerprint} ≠ {k.scope_fingerprint()}): re-run its installer")
    except drivers.DriverError as e:
        add("error", "drivers", str(e))
    if k.config.get("classes"):
        for r in w.execute("SELECT t.id, t.task_class, d.slug FROM tasks t LEFT JOIN departments d ON d.id=t.dept_id "
                           "WHERE t.stage IS NULL AND t.task_class IS NOT NULL AND t.status NOT IN "
                           "('done','failed','cancelled')").fetchall():
            if k.classes_for(r["slug"]) and r["task_class"] not in k.classes_for(r["slug"]):
                add("warning", "classes.unknown", f"{r['id']} has task class {r['task_class']!r}, outside "
                                                  f"{r['slug'] or 'the organization'}'s taxonomy")
    stale = k.stale_skill_exports()

    if stale:
        add("warning", "skills.export_stale", f"{len(stale)} exported skill files differ from the library "
                                              f"(e.g. {stale[0]}): `holon skill export` again, or let a skill change land")
    if k.config["planner"].get("trust_harness_read_only"):
        add("warning", "config.read_only", "[planner] trust_harness_read_only = true: the kernel's read-only "
                                           "guard is skipped for harnesses that claim to enforce it")
    return out
