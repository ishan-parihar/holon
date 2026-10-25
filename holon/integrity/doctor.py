"""`holon doctor`: checks an instance's live state. lint.py checks artifacts; this checks data.

Each finding is {severity: error | warning, check, message}. Errors mean the organization's
guarantees do not hold right now and need the Chair.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

from holon.cli import _distribution_root   # the same rule `holon init` records, so the two agree
from holon.core import vocab
from holon.core.db import connect, event_hash, now
from holon.execution import autonomy
import time

# the strategy seam's versions this Holon reads (CONTRACT §9a); a store declaring another is
# reported, not guessed at
EXTERNAL_STORE_VERSIONS = ("1",)


def _isolation(k, add) -> None:
    """The trust boundary (docs/BYLAWS.md §11), checked from the agent's side: the runner is asked, as the
    agent user, who it is and what it can reach."""
    if not k.isolation:
        # Absent, not merely off. Every body this instance dispatches runs as the kernel's own user,
        # so one of them with a shell can read the Chair key and write work.db — the exact failure
        # the boundary exists to prevent, and the one the heartbeat must not sleep through. It is an
        # error, not a warning: the heartbeat turns only errors into incidents
        # (`service.py`: `if f["severity"] == "error"`), and measured 2026-10-23 a live instance
        # carried this as a warning, so the exposure reached nobody. The kernel already rates a
        # promised-but-runnerless boundary an error (`isolation.runner`); an absent one is the
        # same exposure a step wider, and paying sentence for it is a sentence the organization's
        # own databases pay.
        add("error", "isolation.off", "agents run as the kernel's user: one with a shell can read the Chair key and "
            "write work.db (set [isolation] agent_user; `holon isolation plan`)")
        return
    # Half-built boundary: the config names the user but the root step never ran. The Chair's
    # live run 2026-10-25 found the sequence's own step 1 missing (`sh: /tmp/holon-isolation.sh: No
    # such file or directory`): every step after it fails on a machine that *looks* configured,
    # because the config row exists and the user does not. The message must name the regeneration
    # command, which is durable by default now: the script lives at
    # $HOLON_HOME/isolation/setup.sh and `holon isolation plan` rewrites it.
    import pwd
    agent = k.isolation["agent_user"]
    try:
        pwd.getpwnam(agent)
    except KeyError:
        script = k.home.root / "isolation" / "setup.sh"
        add("error", "isolation.half", f"[isolation] names agent user {agent!r}, which does not exist: the "
            f"root step never ran. `holon isolation plan` regenerates the script at {script}, "
            f"then run it as root (`sudo sh {script}`)")
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
        add("error", "isolation.reach", f"the agent user cannot write {f}, which is in its reach "
                                        "(holon isolation apply --force)")
    for f in p["unreadable"]:
        add("error", "isolation.reach", f"the agent user cannot read {f}, which it needs "
                                        "(holon isolation apply --force)")
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
        # the same root `holon init` records, decided by the same rule: counting parents differs
        # by one hop between `holon/cli.py` and this file, and two different roots meant every
        # healthy cold start warned about a stale checkout that did not exist.
        running = str(_distribution_root())
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
                f"no `holon gc --apply --force` in over {window} days (last: {last or 'never'}) — run directories and "
                "messages past [retention] sit uncollected; dry-run `holon gc` first, back up, then apply")

    # U1/U3: the bylaws's synced view must not be older than its source, nor the registry than the sync
    from holon.bylaws import units as units_mod
    manifests, _ = units_mod.load(k.home)
    # A manifest outside the manifest directory is invisible to load() and to lint: the next
    # `bylaws sync` would empty the capability layer with no error. Name it before it happens.
    for orphan in units_mod.orphan_manifests(k.home):
        add("error", "bylaws.orphan",
            f"{orphan}: a unit manifest outside {k.home.bylaws_units_dir.name}/ — the kernel cannot see "
            f"it, and `holon bylaws sync` would empty the units tables; move it into "
            f"{k.home.bylaws_units_dir}/")
    if manifests:
        newest = max(u["mtime"] for u in manifests.values())
        row = w.execute("SELECT max(synced_at) FROM units").fetchone()
        if not row[0] or row[0] < newest:
            add("warning", "bylaws.stale",
                f"a unit manifest changed after the last `holon bylaws sync` ({newest[:19]}): the tables, "
                "pooled reach and registry are a stale view of the bylaws")
        reg = k.home.bylaws_registry
        if row[0] and reg.exists():
            from datetime import datetime, timezone
            reg_at = datetime.fromtimestamp(reg.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            if reg_at < row[0]:
                add("warning", "bylaws.registry",
                    "bylaws/registry.md is older than the last sync: `holon bylaws emit` regenerates it")

    # C3: a unit whose health probe fails is down for everyone who pools it — name the
    # departments, so a planner routes around the dead tier before spending anything.
    #
    # The convention is that a unit's health runs as a smoke check named "<unit>:<service>".
    # That name is WRITTEN by `execution/service.smoke_checks` from the manifest's own `health`
    # field (added 2026-10-24 by the plan's red-team, attack 5); before it, this check and
    # `capabilities/context`'s `(down)` marker both read that name and nothing emitted it, so
    # a unit declaring `health = "…"` reported nothing and no duty ever ran the probe.
    #
    # Every category, not only surfaces: the check is about a declared health probe failing,
    # which is category-independent. It was gated on `category == "surface"` until 2026-10-24,
    # which left every `service`-category unit — a whole pooled foundation — invisible to the only
    # mechanism that reports a dead capability. Measured then with a control clone that differed
    # only in that field: the surface clone warned, the `service` original said nothing, same
    # exit-127 command. A surface keeps its historical `surface.down` key (operator tooling
    # matches it); every other category reports `unit.down`.
    failed = {r["name"]: r for r in k.smoke_results() if not r["ok"]}
    for slug, u in units_mod.load(k.home)[0].items():
        for s in u["services"]:
            r = failed.get(f"{slug}:{s['id']}")
            if r:
                who = "every department" if "*" in u["pool_of"] else ", ".join(u["pool_of"] or ["nobody"])
                check = "surface.down" if u["category"] == "surface" else "unit.down"
                add("warning", check,
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
    # an employee's record may narrow them to task classes (the class gate in the claim query):
    # such an employee may pick up only work whose class is one of theirs. An employee whose
    # record names no class is not narrowed, and may pick up anything.
    narrowed: dict[str, set[str]] = {}
    # per employee: their department, and the stages their roles perform
    emp: dict[str, tuple[str | None, set[str]]] = {}
    for e in q("SELECT id, dept_id, roles, task_classes FROM employees WHERE status IN ('active','probation')"):
        stages = {s for role in json.loads(e[2] or "[]") for s in vocab.ROLE_STAGES[role]}
        staff.setdefault(e[1], set()).update(stages)
        emp[e[0]] = (e[1], stages)
        classes = json.loads(e[3] or "[]")
        if classes:
            narrowed[e[0]] = set(classes)
    working = {r[0] for r in q("SELECT id FROM employees WHERE status IN ('active','probation')")}
    exec_dept = (q("SELECT id FROM departments WHERE slug=?", vocab.EXECUTIVE) or [[None]])[0][0]
    for r in q("SELECT id, stage, dept_id, assignee, task_class FROM tasks WHERE status='ready' AND stage IS NOT NULL "
               "AND stage<>'approve'"):
        if r[3]:
            if r[3] not in working:
                add("error", "graph.unstaffed", f"{r[1]} stage {r[0]} is assigned to {r[3]}, who is not active")
            continue
        reach = staff.get(r[2], set()) | (staff.get(exec_dept, set()) if r[2] is None or r[1] == "verify" else set())
        if r[1] not in reach:
            add("error", "graph.unstaffed", f"{r[1]} stage {r[0]}: no active employee who can claim it performs '{r[1]}'")
            continue
        if r[4]:
            # the class gate: an employee narrowed to classes may pick up only work whose class is
            # one of theirs. The stage can never be claimed when every employee who could reach it
            # is narrowed away from this class. An employee whose record names no class is not
            # narrowed, so one of them in reach keeps the stage claimable.
            candidates = {e for e, (dept, stages) in emp.items()
                          if r[1] in stages and (dept == r[2] or (dept == exec_dept and (r[2] is None or r[1] == "verify")))}
            if candidates and not any(r[4] in narrowed.get(e, set()) or e not in narrowed
                                      for e in candidates):
                add("error", "classes.unstaffed",
                    f"{r[1]} stage {r[0]}: its task class {r[4]!r} is held by no active employee in reach — "
                    "no one may claim it (give an employee that class, or change the work's class)")

    # a work's workdir that no longer exists: the driver falls back to the run directory, so the
    # body runs against a directory with none of the inputs its plan names (measured 2026-10-21 on a
    # live instance: a recurring routine pointed at a scratchpad from a retired harness era, its body
    # spun on a dead path for an hour, and the work was only recovered by the lease sweep). Warn
    # rather than error: the run still works, but the job the routine was hired for does not.
    for r in q("SELECT id, title, workdir FROM tasks WHERE stage IS NOT NULL AND status='ready' "
               "AND workdir IS NOT NULL AND workdir<>''"):
        if not Path(r[2]).is_dir():
            add("warning", "workdir.missing",
                f"{r[1]} stage {r[0]} names workdir {r[2]!r}, which does not exist: the body will run in "
                "its run directory instead, with none of the inputs the plan expects")

    # A workdir INSIDE the instance that the LAST BACKUP does not carry (2026-10-25): the
    # durable output of a running routine would not ride the backup, on the one machine where
    # the organization exists nowhere else. Measured on the live instance: 19 tasks keep their
    # workdir under `$HOLON_HOME`, and the backup's fixed tree set named none of the routine
    # trees -- so what those routines wrote was left behind, silently, by a backup that
    # reported success.
    #
    # The backup now derives its trees from the works' own workdirs, so a backup taken NOW
    # carries them. The finding is therefore about the backup that exists, not about the rule:
    # the most recent backup's manifest must cover every tree a work names. An instance with
    # no backup at all is not this finding (the retention check owns that one).
    from holon.integrity import backup as _backup
    dest = (k.config.get("healing", {}) or {}).get("backup_to") or str(k.home.root / "backups")
    # A backup on the SAME DEVICE as the instance does not survive the device (2026-10-25).
    # BYLAWS §6 says it plainly -- "point it at another disk or a synced mount, or a dead
    # disk takes the backups too" -- and nothing checked whether it was. Measured on the
    # live instance: the backup directory is `$HOLON_HOME/backups`, the same btrfs pool as
    # the instance, so a dead disk took the organization with it.
    #
    # This is a WARNING, not an error: with no second disk the backup is still the best
    # thing available, and where the backups live is the operator's call. But the machine
    # must say it -- an operator cannot see a device boundary from inside the instance.
    # The finding names the setting that closes it.
    _dest_p = Path(dest).expanduser()
    if not _dest_p.exists():
        _q = _dest_p
        while not _q.exists() and _q != _q.parent:
            _q = _q.parent
        _dest_p = _q
    try:
        _same = os.stat(_dest_p).st_dev == os.stat(k.home.root).st_dev
    except OSError:
        _same = False
    if _same:
        add("warning", "backup.same_device",
            f"the backups go to {dest}, on the same device as the instance: a dead disk takes "
            "the backups with it -- set [healing] backup_to to another disk or a synced mount")
    latest = _backup.backups(dest)
    if latest:
        try:
            manifest = json.loads((latest[-1] / "manifest.json").read_text())
        except (OSError, ValueError):
            manifest = None  # a corrupt manifest: the backup's own verify owns that finding
        if manifest is not None:
            carried = set(manifest.get("trees") or [])
            wanted = set(_backup.in_home_workdirs(k))
            for tree in sorted(wanted - carried):
                add("warning", "backup.uncovered",
                    f"{tree}/ is inside the instance and a work names it as its workdir, but the last "
                    f"backup ({latest[-1].name}) does not carry it: what the routine writes there does "
                    "not ride `holon backup` -- take a new backup, or move the workdir outside the "
                    "instance")

    # The operationalization backstop (2026-10-25 red-team): an ENABLED recurring schedule whose
    # lane needs a body, in a department where no active employee performs the stages it needs,
    # fires into a void — every firing sits ready with nobody to claim it. Measured on a real
    # imported instance: three departments, 26 pending approvals, zero employees, and an enabled
    # schedule reported by NOTHING until its first firing stranded. doctor sees the state however
    # the instance reached it (a forced enable, a fired employee, a transferred head), and the
    # heartbeat turns the error into an incident. The same predicate as the enable gate and the
    # proposal note (schedules.unstaffed_stages) — one rule, or the three would disagree. The
    # script lane is exempt: the kernel is its runner. An enabled script schedule whose workdir is
    # gone is the same seam one layer down (the kernel silently substitutes the run dir, F6).
    from holon.execution.schedules import unstaffed_stages, staffing_note
    for r in q("SELECT s.id, w.dept_id, o.body, w.title, w.workdir FROM schedules s "
               "JOIN tasks p ON p.id = s.template_id JOIN tasks w ON w.id = p.parent_id "
               "JOIN outputs o ON o.task_id = p.id WHERE s.kind='recurring' AND s.enabled=1"):
        try:
            body = json.loads(r[2]) or {}
        except (json.JSONDecodeError, TypeError):
            continue
        lane = body.get("lane") or "full"
        if unstaffed_stages(w, r[1], lane):
            add("error", "schedule.unstaffed",
                f"enabled schedule {r[0]} ({r[3].removeprefix('Routine: ')}, lane {lane}): "
                + staffing_note(w, r[1], lane))
        if lane == "script" and body.get("command"):
            wd = r[4]
            if wd and not Path(wd).is_dir():
                add("warning", "schedule.workdir",
                    f"enabled script schedule {r[0]} ({r[3].removeprefix('Routine: ')}) names workdir "
                    f"{wd!r}, which does not exist: the kernel will run its command in the run "
                    "directory instead (F6, 2026-10-25)")

    # A3 (2026-10-22 audit): a result's `artifacts` are the permanent record of what it left
    # behind, and until now nothing re-read them — two of six live pointers had already stopped
    # resolving, rotted in `outputs` where only a verifier's glance ever saw them. Re-resolve
    # every execute output's artifacts; a path that no longer exists is a warning naming the
    # work and the pointer. A relative artifact is resolved against its work's workdir (the
    # contract's rule: workdir-relative), never against this process's cwd.
    for r in q("SELECT w.id AS work_id, w.title, w.workdir, o.body FROM outputs o "
               "JOIN tasks ex ON ex.id = o.task_id JOIN tasks w ON w.id = ex.parent_id "
               "WHERE o.kind='execute' AND o.body LIKE '%artifacts%' AND o.superseded=0"):
        try:
            arts = (json.loads(r["body"]) or {}).get("artifacts") or []
        except (json.JSONDecodeError, TypeError):
            continue
        for a in arts:
            if not isinstance(a, str) or not a.strip() or "://" in a:
                continue
            p = Path(a).expanduser()
            if not p.is_absolute() and (r["workdir"] or "").strip():
                p = Path(r["workdir"]).expanduser() / p
            if not p.exists():
                add("warning", "artifacts.dangling",
                    f"{r['title']} ({r['work_id']}) names artifact {a!r}, which does not exist — the "
                    "organization's record points at something it can no longer reach (restore it, or "
                    "supersede the result)")

    # A4 (2026-10-22 audit): placement was fenced nowhere, and a directory grew at the
    # instance root because a body put it there (five tasks' workdir on a live instance,
    # named by no config and no kernel vocabulary). A root directory is legitimate when the
    # kernel owns it (its own vocabulary), when a work names it as its workdir, or when a
    # declared [scope] root covers it; anything else is a stray, and the fix is to declare
    # it or remove it — never to let it keep growing unnamed.
    _vocab = {"config", "execution", "capabilities", "bylaws", "skills", "kb", "genome",
              "adapters", "backups", "ventures", "governance", ".clock"}
    # A work's workdir may sit *inside* a root directory no work names directly — the recurring
    # plans point at `<root>/routines/<plan>`, and `routines/` is declared by that use. So every
    # ancestor from the workdir up to the instance root is declared, not only the leaf.
    used = set()
    for r in q("SELECT workdir FROM tasks WHERE workdir IS NOT NULL AND workdir<>''"):
        p = Path(r[0]).expanduser().resolve()
        if p.is_relative_to(k.home.root.resolve()):
            used.update(p.relative_to(k.home.root.resolve()).parts)

    covered = [Path(x).expanduser().resolve() for group in (k.config.get("scope", {}).get("roots") or [],
               *(d.get("roots") or [] for d in (k.config.get("scope", {}).get("dept") or {}).values()))
               for x in group]
    if k.home.root.is_dir():
        for d in sorted(k.home.root.iterdir()):
            if not d.is_dir() or d.name in _vocab or d.name in used:
                continue
            if any(d.resolve().is_relative_to(c) or c.is_relative_to(d.resolve()) for c in covered):
                continue
            add("warning", "scope.stray",
                f"{d.name}/ sits at the instance root named by nothing: no work's workdir, no [scope] "
                "root, no kernel vocabulary. Declare it (a [scope] root, or a work's workdir) or "
                "remove it — an undeclared directory is where placement discipline goes to die")

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

    # the heartbeat's liveness stamp (G3): the clock organ (the gateway, or the kernel's
    # own timer) runs `holon tick` every 2 minutes and stamps .clock/last_tick with the unix
    # seconds of the last successful tick. Three cadences without a word (6+ minutes) means
    # the heartbeat is dead — schedules stop firing, leases stop renewing, held results are
    # never handed in — and nothing else says so. An absent stamp on a young instance stays
    # silent (no tick has run yet); a stale one is the warning, and a fresh one silences it.
    stamp = k.home.root / ".clock" / "last_tick"
    if stamp.exists():
        try:
            age_min = (time.time() - int(stamp.read_text().strip())) / 60
            if age_min > 6:
                add("warning", "heartbeat.stale",
                    f"the heartbeat's last tick stamp (.clock/last_tick) is {int(age_min)} minutes old: the "
                    "clock (`holon tick` every 2 minutes — the systemd timer or the gateway's clock organ) has "
                    "stopped, so due routines never fire and expired leases never renew (restart it, and see the "
                    "tick's errors in the journal / the gateway's log)")
        except (ValueError, OSError):
            add("warning", "heartbeat.stale",
                "the heartbeat's tick stamp (.clock/last_tick) is unreadable: it should hold one integer, "
                "the unix seconds of the last successful tick")

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
