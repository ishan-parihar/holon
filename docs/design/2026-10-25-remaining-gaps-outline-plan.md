---
status: proposed (successor to the 2026-10-24 outline, which lives in the operator's overlay)
date: 2026-10-25
description: "The outline-plan for the remaining implementation gaps, re-measured the day after the predecessor's own execution audit. The re-measurement reopened two rows this record's own first draft had wrongly closed (its lint reading measured a scratch home, which cannot see instance state; one reopened row is a new gap, F1, whose repair migration ships in the same commit); the staffing red-team landed between the two measurements and changed the plan's own gates (a routine is now staffed before it is lit); the install-time lifecycle fix landed and A1's step 0 can no longer rot in /tmp. Every fix-command this record names was verified by running --help against the live registry before it was written down."
tags: [outline, plan, gaps, isolation, scheduling, jcode, inbound, staffing]
supersedes: the 2026-10-24 remaining-gaps outline, which lives in the operator's overlay (archived there 2026-10-25 in the overlay's docs/archive/, with a tombstone)
related: []
---

# 2026-10-25: the remaining gaps, re-measured — and the two seams that moved under the plan

Status: **proposed outline-plan**. Scope: A only. This record supersedes the 2026-10-24 outline
(the execution-order record whose day-after audit is §7 of that document); the predecessor lives
in the operator's overlay, where dated design records belong, and this one carries only what a
universal distribution needs. Every number below was re-measured on **2026-10-25T21:30–21:53Z**
on the live instance (`--home`, SQL `mode=ro`) and on this tree (`d4f8c2a`, the commit the live
launcher resolves). Nothing here is about the other stack.

---

## 1. Where it stands, re-measured

| | the 2026-10-24 outline (§7 audit) | 2026-10-25T21:3xZ re-measured | delta |
|---|---|---|---|
| doctor | 1 error, 0 warnings | **1 error, 2 warnings** — `isolation.off`; the two `smoke.check` warnings are the career-pipeline pool's own data (stack-B facts, named as such) | unchanged where it matters |
| strict lint | 2 errors (`ref.command` hermes, `schema.migration`) | **0/0 on a scratch home; the live home still reads both** — instrument note below | **both open on the live instance; both causes now measured** |
| employees | 7 active, all `operant` | 7 active, all `operant` | unchanged |
| jcode runs, ever | 0 | **0** | unchanged |
| last employee run | 2026-10-21T18:03Z (operant) | **2026-10-21T18:03Z (operant)** | unchanged |
| `wake` schedules | none | **none** (3 recurring only) | unchanged |
| recurring schedules | 2 off (past due), 1 on | **2 off (past due since 10-23), 1 on** — the KB-smoke routine fires daily (last 2026-10-25T06:00:37Z, next 2026-10-26T06:00Z) | unchanged |
| ready stages | 29 (26 approve, 1 research, 2 retro) | **29 — same 26 ventures-import approves, the same isolation.off incident research, the same 2 retros** | unchanged |
| `[isolation]` | absent | **absent** (no block, no agent user, broker inactive) | unchanged |
| `isolation/setup.sh` | absent (the audit had removed the dir) | **absent — and no longer able to rot**: `isolation plan` writes it durably by default since `5b5b367` | **the mechanism changed** |
| `channel_principal` (D1) | absent, guard skips | **absent, guard still skips** (verified: `tests.test_core.ChairChannels` skips with H8's promise as its message) | unchanged |
| telegram adapter (D2) | absent | **absent** | unchanged |
| the architecture index | did not exist | **exists in the distribution, unbuilt on the live instance** (`ad/index.db` absent) | **new seam, see §2** |

The headline survives a second day: **the engine runs, and no body dispatches.** The movements
since the predecessor's audit: two mechanism landings that changed the plan's own gates, one new
unbuilt capability (row 0), and — found by re-measuring with the right home — **two live-home lint
errors the first draft of this record wrongly closed** (§2c), one of which is a new gap (F1) no
previous record ever carried.

**A measurement correction this re-measurement made on itself.** The predecessor's §7 read the
schedules table positionally and recorded the KB-smoke routine as enabled with the others
disabled — correct. This record's own first two readings of the same table were **wrong in the
other direction** (a tuple-index drift read all three as `enabled=0`), and only a third read,
**by column name**, restored the truth: KB-smoke `enabled=1` and firing. The lesson is the
predecessor's own §7.2.1 in miniature: an instrument's positional assumption is a claim about
the schema, and the same query re-asked by name disagrees. Both readings are named here because a
record that silently dropped its own bad reading would teach the wrong thing.

## 2. The two seams that moved under the plan

The predecessor ordered Gap 0 → A1 → A2 → B1 → C1 → B2/B3 → D1 → D2 → E2. Between its audit and
this re-measurement, two things landed that change what those rows *are*:

**Its Gap 0 is not this record's row 0.** The predecessor's Gap 0 was the strict-lint regression
that week's work introduced (a read-route rule firing on a command-group CLI); it was closed
**in the overlay** the same day (`fcfd820`, and its manifest red-team `5c00820`/`5af526d`), and its
closure reached this tree through the upstream path (`8378d70`, then the full capability-layer
hardening in `b6daad8`) — so both 0s are closed on this tree, verified by today's 0/0 strict
lint. This record's row 0 — the unbuilt architecture index — is a new row of the same genesis the
predecessor gave its own: a gap that "did not exist when the record was written because it was
created by the record's own predecessor landing." The landing between the two records was
`5b5b367`, which shipped the index in the distribution without building it on the live instance.
A reader counting along the two records' 0s should count two different rows.

**(a) The staffing red-team landed (2026-10-25, `7941b1f`).** A routine is now staffed before it
is lit: `schedule enable` refuses an unstaffed lane unless the Chair passes `--force`, the same
predicate is proposed in the grade reasons, and doctor gained `schedule.unstaffed` and
`schedule.workdir` checks. **Consequence for this plan:** B1's wake schedules and B2's routine
enables are no longer bare configuration commands — they are gated on the staffing state they
find, by design. The gate is not an obstacle to the plan; it is the plan's own F7 finding
(“a `ready` stage nobody can claim, stranded”) made structural. On the live instance today the
gate fires on nothing, because nothing is enabled that needs a body.

**(b) The install-time lifecycle landed (`5b5b367`).** A1's step 0 can no longer rot: the plan
writes its root script to `$HOLON_HOME/isolation/setup.sh` by default, the printed sequence
names that path and the command that regenerates it, and doctor gained `isolation.half` (a
configured agent user that does not exist is an error, not silence). **Consequence for this
plan:** A1's remaining risk is no longer "the file vanished" — the predecessor's Chair hit
exactly that (`sh: /tmp/holon-isolation.sh: No such file or directory`) — but only "the root
step has not been run", which the doctor now names.

And one seam that is **new since both records**: the architecture index (`holon ad`) exists in
the distribution but is unbuilt on the live instance, so no brief carries the Precedent section.
That is one Chair command (`holon ad index`), and it is the first row below because it is
reversible, unrooted, and every later body's brief is richer for it.

**(c) This record's own first draft measured the wrong home, and the re-measurement reopened
two rows the draft had closed.** Its §1 row read "0/0, both closed" — measured by `lint --strict`
against a **fresh scratch home**, where instance state (declared drivers, applied migrations)
cannot appear, and it mislabelled the row "(this tree)". Re-measured against the live home
(the `HOLON_HOME` both user units name), the two pre-existing errors are still there, and their
causes are now measured rather than inferred:

- **`ref.command` (E2 reopened).** `hermes` is declared as an **instance-level** driver at
  `$HOLON_HOME/config/drivers/hermes.toml` (one of four such tomls there; the distribution
  ships only `generic-shell` builtin), and `hermes` is not on PATH. Nothing in the distribution
  declares hermes — the name left the kernel entirely on 2026-10-12 (`0af4904`, law 2). The fix
  is the Chair's one command: `rm $HOLON_HOME/config/drivers/hermes.toml` (its 28 runs ended
  2026-10-14; every live employee is `operant`), or install a `hermes` on PATH. The
  predecessor's claim that this error "is honest and pre-existing" is confirmed, with the cause
  now measured: instance state, not distribution state.
- **`schema.migration` (a new gap, F1).** The live `memory.db` carries `0003_api_memory.sql`
  applied with checksum `d3d6c1492905`, which **matches no version of the file that ever
  existed in git**: the instance applied a pre-commit draft at 2026-10-15T14:29Z, and the file
  was committed 52 minutes later with checksum `d747bd0673cd` (and never changed since). Every
  other migration row matches its shipped file exactly (all five, measured). The drift is
  unreachable by every kernel path: `migrate()` re-stamps only `checksum IS NULL` rows, the
  live row is not NULL, and no command exists to repair a drifted non-NULL row. `holon lint
  --strict` read this error on the live home until this commit. **The same class as the
  2026-10-25 `supersede` ledger row** (a drifted view, repaired by minting `0018`): the repair
  is a migration minted in the distribution, `holon/migrations/memory/0006_checksum_repair.py`, following `0004`'s
  precedent — a no-op on a healthy instance, a one-row re-stamp on the drifted one, honest by
  ordering (migrations apply in order, so any instance applying 0006 has already had 0004
  re-assert the view from the shipped definition). The live instance's heartbeat applies it on
  its next migrate. Law 6 is kept: no applied migration file was edited; the repair is itself a
  new migration, guarded by three tests in `tests/test_integrity.py` (`ChecksumDriftRepair`),
  mutation-verified from both directions.

## 3. The plan, in tiers

| # | Gap | Fix (a command, verified against the registry) | Gate (a command) |
|---|---|---|---|
| **0** | the architecture index is unbuilt on the live instance; no brief carries Precedent — a new row, created by the landing between the two records (`5b5b367`) | `holon ad index` (chair; builds `$HOLON_HOME/ad/index.db`, the build refuses a dangling ledger guard) | `holon ad find isolation` returns rows; a fresh brief carries "Precedent: what the organization already learned" |
| **A1** | `isolation.off` — agents run as the kernel's user | `holon isolation plan` (writes `$HOLON_HOME/isolation/setup.sh` durably), review it, then **as root**: `sudo sh $HOLON_HOME/isolation/setup.sh`, write the `[isolation]` block it prints into `holon.toml`, then `holon isolation apply --force` | `holon doctor` prints no `isolation.off` |
| **A2** | the broker unit, now that `agent_user` exists | `python3 install.py --systemd` | `systemctl --user is-active holon-broker.service` → `active` |
| **B1** | no `wake` schedule, so the 7 employees never run | `holon schedule add --as <emp> --kind wake --cron "*/15 * * * *" --stage <stage>` per department | a `ready` stage is claimed inside one cron window; a `runs` row with a real driver appears |
| **B2** | two `off` recurring routines past-due since 2026-10-23 (Disk usage report, Notes digest) | `holon schedule enable <id>` (carries the staffing gate; `--force` is the Chair's word) or leave disabled and note why | no enabled schedule whose `next_run_at` is in the past, and no disabled one left ambiguous |
| **B3** | 29 ready stages: the 3 legacy (1 research, 2 retro) + 26 ventures-import approves | the 3: one `holon dispatch --as <id> --task <stage>` each, or `holon task cancel <id> --note "…"`; the 26 are the Chair's per-job approval | `ready` counts drop by disposition, not timeout |
| **C1** | 0 of 7 employees bound to jcode; 0 jcode runs ever | `holon hr set-driver <emp> --driver jcode --reason "…"`, one reversible employee at a time, **after B1** | one `runs` row with `driver=jcode` |
| **D1** | `Auth.channel_principal` absent; the H8 guard skips | implement the method the guard names, in `holon/execution/security.py` | `test_an_inbound_channel_identity_can_be_authorized_as_the_chair` **runs and passes, not skips** |
| **D2** | `adapters/telegram/` does not exist | only after D1, and on the Chair's go | one test crossing the seam with a real `sendMessage` |
| **E1** | the uid decision is unprovable here | closes by A1+A2+B1 landing | a live broker-mediated run under a real agent user |
| **E2** | `hermes` declared as an **instance-level** driver (`$HOLON_HOME/config/drivers/hermes.toml`), not on PATH | the Chair's one command: `rm $HOLON_HOME/config/drivers/hermes.toml` (its 28 runs ended 2026-10-14, every live employee is `operant`), or put a `hermes` on PATH | live-home `holon lint --strict` reads **0** `ref.command` errors |
| **F1** | the live `memory.db`'s applied `0003` checksum matches no committed version (a pre-commit draft was applied); no kernel path could repair a drifted non-NULL checksum row | **DONE in this commit**: repair migration `holon/migrations/memory/0006_checksum_repair.py` follows `0004`'s precedent — a no-op on a healthy instance, a one-row re-stamp on the drifted one, honest by ordering (any instance applying it has already had `0004` re-assert the view); the live instance's heartbeat applies it on its next migrate | live-home `holon lint --strict` reads **0** `schema.migration` errors (after the next heartbeat) |

## 4. The order, and why

```
0 → A1 → A2 → B1 → C1 → B2/B3 → D1 → D2 → E1 (consequence) → E2, F1 (both the Chair's)
```

**Why 0 is first now.** It is one reversible command, it fails loudly (the build refuses a
dangling guard), and it enriches every brief the later rows dispatch. Nothing downstream depends
on it, which is exactly why it costs nothing to do first. (A new row, not the predecessor's Gap 0
— that one is DONE, and §1's strict-lint row is its closure re-verified.)

**Why A before B, unchanged from the predecessor and still true.** Every dispatched body runs as
the kernel's own user until A1; B1, C1 and B3 dispatch bodies. Dispatching before A is what A
exists to prevent.

**Why B1 before C1, unchanged.** A wake schedule makes the organization self-running; a driver
swap is worth testing only when something fires.

**Why B2 moved after C1 (the one reordering).** The predecessor ran B2 before C1 as cleanup.
With the staffing gate landed, B2 is no longer cleanup: enabling the two stale routines arms
them, and their lane is `full` — the gate will ask whether the department that owns them can
staff them, and the honest answer may depend on what B1 and C1 established about who runs what.
Enabling a routine into a department you have not yet scheduled is the exact F7 shape the
red-team fenced.

**Why D1 and D2 stay last and unchanged.** The inbound seam is what makes a button act; the
carrier without the principal is a button that cannot identify its owner.

**E2 and F1, the two live-home lint errors, both close with the Chair's word and neither
before.** E2 is the driver row the Chair retired in effect but never in state (the toml is
still there; the runs stopped 2026-10-14; every live employee is `operant`). F1 is the drifted
checksum no predecessor row ever had, because their instruments could not see it either. Both
are measured, both have one-command fixes on the live instance, and the plan does not pretend
either is closed.

## 5. What is the Chair's, and what is not

**The Chair's, and only theirs:** row 0's one command (`holon ad index` — it mutates instance
state), A1's root step (`sudo sh` the durable script), A2 (`install.py --systemd`), the 26
ventures-import approvals, the D2 go/no-go, and the formal hermes-driver retirement. B2's
`--force` escape, if the staffing gate ever fires, is also the Chair's word.

**Mine, now:** B1 (the wake schedules), C1 (one reversible employee at a time), B3's three
legacy stages (dispatch or cancel with a reason), and D1 (the method the guard names).

**Both:** E1's measurement, once A1–B1 land.

## 6. What this outline does not claim

- It does not claim the sibling's staffing gates as its own. They landed between the two records
  (`7941b1f`, `b17c647`), they are the plan's gates now, and this record only re-ordered B2
  around them.
- It does not claim E2 was "fixed". It was measured closed. Which landing closed it is not
  established here, and the difference is recorded rather than smoothed over.
- It does not carry stack-B numbers. The two `smoke.check` warnings are the career-pipeline
  pool's own data on the other side of law 10; they are named so a reader knows the doctor's
  warning count is not this program's.
- It does not re-prescribe the predecessor's §7 commands verbatim: every command above was
  re-verified by running `--help` on each against the live registry (`schedule add`, `schedule
  enable`, `hr set-driver`, `dispatch`, `task cancel`, `isolation apply`, `ad index`,
  `adapter test`; eight of eight answered, every prescribed flag present), because the
  predecessor's own disclosure 3 recorded naming a command that did not exist.
- It carries no verdicts about rows it did not measure. H2, H3, H5, H6 (the register's blocked
  rows) are untouched by this record and remain the Chair's.

---

## 7. Succession

This record supersedes the 2026-10-24 outline (execution order + day-after audit). The
predecessor and its audit live on in the operator's overlay, where dated design records belong;
this record is the universal successor, and the register rows it corrects are corrected in the
universal register only. What the successor rows say the predecessor did not: the staffing gate
as a landed mechanism (§2a), the durable setup script (§2b) — and, because this record's own
first draft measured a scratch home instead of the live one, **two reopened rows (E2, F1) whose
causes the predecessor's table carried as unmeasured residue** (§2c). The instrument lesson is
the predecessor's own, learned twice in one day: a claim about live state must name its home,
and a scratch home cannot see instance state.
