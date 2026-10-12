"""`holon doctor`: checks an instance's live state. lint.py checks artifacts; this checks data.

Each finding is {severity: error | warning, check, message}. Errors mean the organization's
guarantees do not hold right now and need the Chair.
"""

from __future__ import annotations

import json
import stat

from holon.core.db import event_hash, now
from holon.execution import autonomy


def run(k) -> list[dict]:
    out: list[dict] = []

    def add(severity: str, check: str, message: str) -> None:
        out.append({"severity": severity, "check": check, "message": message})

    w = k.wconn
    q = lambda sql, *a: w.execute(sql, a).fetchall()  # noqa: E731

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
    stale = k.stale_skill_exports()
    if stale:
        add("warning", "skills.export_stale", f"{len(stale)} exported skill files differ from the library "
                                              f"(e.g. {stale[0]}): `holon skill export` again, or let a skill change land")
    if k.config["planner"].get("trust_harness_read_only"):
        add("warning", "config.read_only", "[planner] trust_harness_read_only = true: the kernel's read-only "
                                           "guard is skipped for harnesses that claim to enforce it")
    return out
