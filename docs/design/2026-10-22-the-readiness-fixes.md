---
status: executed (mine) + awaiting the Chair (the rest)
date: 2026-10-22
description: "The readiness fixes: what the audit executed end to end (supersede remedy, hygiene, the staged venture pool) and the Chair's remaining actions with their exact commands — the four-step isolation procedure the earlier record got wrong (apply does not create the agent user; the root script does, and the broker unit must exist before agents can reach the organization)."
tags: [readiness, chair-actions, isolation, hygiene]
supersedes: 2026-10-22-what-remains-yours.md (archived with its body in docs/archive/)
---

# The readiness fixes: executed, and what remains the Chair's

Measured 2026-10-22T23:40Z against the live instance, read-only before acting, every claim
re-verified after. Doctor before this session's fixes: **1 error, 10 warnings**. After:
**0 errors, 1 warning** (`isolation.off` — the Chair's root action, below).

## 1. What this session executed (verified, not asserted)

| Fix | Command / change | Measured after |
|---|---|---|
| the supersede remedy doctor named but did not exist | `task supersede` (chair): migration 0018, `Work.supersede` walks a work's execute outputs (a cancelled retry does not shield the done attempt), memory pins retired by memory.py, doctor skips superseded rows | 6 guards, mutation-verified; the live instance's five rotted demo results superseded, 2 dangling warnings gone |
| the two recurring routines firing into rotted `/var/tmp` workdirs | cancelled the open firings, disabled both schedules, re-proposed both with durable instance-root workdirs (`~/holon/routine-disk-usage`, `~/holon/routine-notes-digest`) | the two old schedules read `off`; the two proposals await the Chair's approval, unapproved by design |
| the five dangling artifact pointers (30-day tmpfiles rot) | `task supersede` each rotted demo work with its note | doctor's `artifacts.dangling` count: 7 → 0 |
| stale skill exports (2 files) | `holon skill export` to both registered dirs | `skill check`: 11 skills ok |
| the empty venture placeholder | staged the real B-side pool (15 protocols, 16 checks, 183 scripts) into the instance's ventures tree | `venture import` dry run reads **15 of 15 protocols with their skills** (was 0) |

A stray-check false positive the audit created itself (`routines/` container) was caught by
the placement law's own check within a minute of creating it and re-homed — the check bites
its author too, which is the point.

## 2. The heartbeat, re-measured (the earlier record's claim was false)

The claim "the heartbeat is not running and will not start after a reboot" was **falsified**
on 2026-10-22 (ledger: Red-team R2): the runtime gateway's clock organ — a user service,
enabled and active — runs `holon tick` every 2 minutes and stamps `.clock/last_tick`
(doctor reads the stamp; `heartbeat.stale` warns past three cadences). A user unit with
`Linger=yes` on this machine survives reboots. The systemd `holon-tick.timer` the earlier
record told the Chair to enable is a **redundant second clock**, and the Chair enabled it
anyway (now `active/waiting`) — harmless, and a useful fallback if the gateway ever stops.
Both clocks stamp the same file; doctor's `heartbeat.stale` reads whichever ran last.

## 3. Isolation: the four steps, in order (the earlier record's procedure failed live)

The earlier record said: set `[isolation] agent_user`, then `holon isolation plan`, then
`holon isolation apply` — claiming apply "creates holon-agent". **`apply` does not create
the user**: `isolation plan` prints the procedure itself, and only the first step's root
script creates the agent user and its runner service. The Chair followed the two-step record
on 2026-10-22 (config edit at 02:14 local), doctor then threw `isolation.runner` (connection
refused — no runner service), and the config was reverted. What the kernel actually requires
(`holon isolation plan`, verbatim steps):

```sh
# 1. as root, once — creates the agent user and the runner system service
holon isolation plan --write /tmp/holon-isolation.sh    # review it first
sudo sh /tmp/holon-isolation.sh

# 2. as you — the ACLs (harmless to re-run)
holon isolation apply --force

# 3. the broker unit: install.py writes it ONLY when [isolation] agent_user is set,
#    and this instance's units were written before isolation was configured — so the
#    unit does not exist yet. Re-run the installer AFTER step 1 sets the config:
python3 install.py --home ~/holon --bin-dir ~/.local/bin   # writes holon-broker.service
systemctl --user enable --now holon-broker

# 4. doctor: the isolation checks probe what the agent user can reach
holon doctor
```

The config edit comes first (`[isolation] agent_user = "holon-agent"` in `holon.toml`), then
steps 1–4. After all four: `isolation.off`, `isolation.runner`, `isolation.broker` all pass,
and agents run as `holon-agent` with three lists and nothing else. The earlier record's step-3
gap (the broker unit) is why its two steps could never complete on this instance.

## 4. What remains the Chair's, in order

1. **Isolation** (above; root + the config edit). The one Tier-0 blocker.
2. **The `[chair] channels` id** — `dropped_marker` is set and verified (the 20:28Z digest
   recorded `failed` with the marker, not a false `sent`), but no channel is configured, so
   the digest still has nowhere to land. One line in `holon.toml`:
   `[chair] channels = ["telegram:<your user id>"]`.
3. **Four approvals awaiting you** — the two re-homed routines (durable workdirs) and the
   two pioneer monitor/reconciliation routines: `holon task show <id>` then
   `holon plan approve <id> --note …`.
4. **The pilot ruling (H16)** — career-pipeline is staged and dry-run clean; `--apply`
   (15 protocols, all landing unapproved) is one command once you rule on the department's
   shape and whether its KB federates.
5. **Pioneer staffing** — the department exists, its two monitor/reconciliation proposals
   are yours to approve or cancel, and it has no employees.

`holon doctor` after all of the above: 0 errors, 0 warnings is reachable today.
