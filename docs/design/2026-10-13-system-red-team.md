# Holon: the whole system, red-teamed (2026-10-13)

Status after the R1–R6 hardening, real-harness validation (Hermes, ZeroClaw), the VPS migration
and the first supervised wave. It answers three questions: what is built, what is left, and
whether integrity holds when agents act on their own. Read it before changing a mechanism it
covers; `AGENTS.md` holds the regression ledger that keeps the fixed defects fixed.

## 1. What Holon is

An organization that runs on any agent harness. The kernel (stdlib Python, two SQLite databases)
owns the org chart, the work graph, memory, skills and the rules; a harness (ZeroClaw, Hermes,
Claude Code, …) is only a body an employee runs in. Every piece of work moves through stages
done by roles (research, shape, plan, approve, execute, verify, retro); the Chair (the human)
approves what the grading and the autonomy policy send to them.

```
Chair ── holon CLI (Chair key) ──────────────┐
                                             ▼
          ┌────────────── kernel ($HOLON_HOME) ──────────────┐
          │ work graph · org/genome · autonomy · grading     │
          │ memory · context bundles · skills · schedules     │
          │ lint · doctor · conformance · backup              │
          └──────┬───────────────────────┬───────────────────┘
       push: tick → dispatch → driver    pull/native: holon CLI or `holon mcp`
                 ▼                       (run token / employee key)
     harness (ZeroClaw / Hermes / Claude Code) → agent → result envelope → submit
```

## 2. What is built

| Layer | Built | Proven by |
|---|---|---|
| Work graph | holarchy of work nodes; 7 stages; needs-edges; rework (`execute#n`, `plan#n`); stale-plan detection; decomposition across departments | tests; Hermes end-to-end; ZeroClaw conformance |
| Organization | departments, employees, roles, slots, genome profiles (`light`, `standard`, `executive`), probation, reviews from evidence, `hr reassign` | tests; VPS org (4 departments, 7 employees) |
| Authority | grades G0–G4 with kernel floors (risk, novelty, writes, scope, decomposition, external effects); authority table; earned authority; audit sampling; supervised/delegated/autonomous policy | tests |
| Security | Chair key, employee keys, run tokens; command registry with actor classes; the Chair key never reaches a harness | tests (`test_the_chair_key_never_reaches_a_harness`) |
| Memory | scopes `emp:`/`dept:`/`org`, kinds, confidence, dedupe/supersede, promotion by recurrence, pins, checkpoints | tests; Hermes end-to-end (terrain, lessons, proposal) |
| Context | per-stage bundle: identity, lineage (trajectory.db), terrain, lessons, decisions, logbook, earlier attempts, within a budget | tests |
| Skills | layered library (department → organization → standard), role skills hold the stage briefs, `skill export` per harness | tests; lint DEPTH |
| Scheduling | wake and recurring schedules, `tick` (leases, triggers, notify, dispatch), budgets per department | tests; VPS heartbeat since 2026-10-13 |
| Scope | `[scope]`/`[scope.dept.<slug>]`: briefs, grading, `{scope}` sandboxes, `scope.drift` | tests; VPS (charter, it) |
| Interfaces | CLI registry (`holon help --json`), MCP server (`holon mcp`: 21 tools incl. `holon_submit`), Chair notifications | tests; ZeroClaw agents used `holon_submit` in every observe stage |
| Integrity | lint (6 packs, 24 rules incl. `ref.guard`), doctor (22 checks), conformance (5 clauses), backup/restore, regression ledger | 124 tests; lint --strict clean |
| Adapters | ZeroClaw v3 (per-scope aliases, MCP, skills bundle, notify, pinned binary); Hermes v1; Claude Code (partial); systemd heartbeat | ZeroClaw 5/5 on the VPS; Hermes 5/5 + end-to-end on the laptop |

## 3. The feedback loops, honestly

| Loop | Sensor → actuator | State |
|---|---|---|
| Heal (minutes) | lease expiry, failing runs, failing schedules → stage back on the board, incident work | **works**; renewal while a harness lives and the failing-runs incident added 2026-10-13 |
| Rework (per work) | verify verdict, plan decision → `execute#n` / `plan#n` with feedback | **works** (tests); not yet exercised by a real failure |
| Employee learning (per run) | learnings, decisions → memory, record | **works** (Hermes end-to-end) |
| Collective intelligence | the same lesson from 2 colleagues → department → organization | **works** in tests; needs volume to show in production |
| Department retro | failures, rework, proposals → retro → actions as work | **wired**; the VPS had no steward until 2026-10-13 (heads now hold it) |
| Self-evolving skills | retro action → executor edits the department's skill layer → re-export | **broken on sandboxed harnesses** (G-B below) |
| Earned authority | verified results, audit disagreements → authority up or down | **inert in practice**: novelty floors every new task class at G2 (G-E) |
| Probation, audit sampling | time, samples → confirmation, re-judging | **works** (tests) |
| Metrics | sensors, each paired with a counter-metric | **read by retros only**; nothing acts on them automatically |

## 4. Can regressions get through?

"Impossible" is not a property any system can promise; the achievable property is: **every
regression is caught before it lands, or detected within one heartbeat and turned into work.**
Where Holon stands, layer by layer:

| Layer | Caught before it lands | Detected after | Gap |
|---|---|---|---|
| Kernel code | 124 tests; lint --strict; regression ledger (`ref.guard`) | — | no CI: nothing runs them unless a person does (G-C) |
| Instance state | migrations with checksums | doctor: integrity, hash chain, invariants, drift | doctor runs only on demand; its errors notify nobody (G-C) |
| Harness | conformance suite; installer checks (binary, aliases) | failing runs → incident | binary/skill-export drift is caught only for scope (G-B, G-C) |
| Agent work | plan grading, Chair/approver gate, read-only guard, independent verifier | rework, retros | **verification is an LLM's judgment; no machine checks** (G-A) |
| Organism (the target repo) | — | `arch validate` when someone runs it | the commit-gate work item is still open; nothing blocks a red change |

## 5. Fragmentation and redundancy

**In the ecosystem around Holon** (the main source of fragmentation):

1. **Four schedulers.** Hermes cron (51 of 100 jobs enabled, runs inside hermes-agent),
   ZeroClaw cron (34 jobs migrated from Hermes, none enabled), systemd timers and crontab
   (≈15), Holon wake/recurring schedules (8 enabled). Decided 2026-10-13: Hermes stays until the
   ZeroClaw handover is done; Holon schedules org work only.
2. **Several employee registries.** Holon employees; the organism's HR (`hr`, workforce-ops);
   AXE's agent registry (task-grid); ZeroClaw cron "employees" (`emp-05d6…`). Holon is meant to be
   the organization; until the legacy migration, the others are separate systems that happen to
   use the same words.
3. **Several memories.** Holon `memory.db` (the organization's), memory-wire banks, ZeroClaw
   `brain.db`, Hermes `state.db`; a jcode adapter would add a fifth unless its memory is off.
4. **Two governance laws.** Holon's charter/authority/genome governs the organization; the
   organism's `arch` (ADs, RGs, `_org.yaml`) governs the repository. They meet only through the
   charter department's work. The `executive` profile's *charter* slot (a steward) is not the
   *Charter Office* department (the organism's architecture law): same word, two things.
5. **Skill copies.** The library is exported into each harness (ZeroClaw bundle, Hermes skills
   directory). Copies drift when skills change and nobody re-exports (G-B).

**In the kernel:** the `ext_id`/`sync_dirty` columns wait for a dashboard that does not exist
(G-D); instance leftovers on the VPS (`governance/law` empty, a pre-graph `smoke.sh`).
No duplicated mechanisms were found: vocabularies, contract shapes, config keys and CLI
references are single-sourced and lint-checked.

## 6. Implementation gaps

### P1: needed for the loops to close and for regressions to be caught

- **G-A Machine-checked acceptance.** The kernel runs department or task-class checks after
  execute (e.g. charter: `arch validate`; kernel work: tests + lint) in the workdir, before the
  verifier; a failing check is rework, and the output joins the verifier's context. Proposed
  `[checks.dept.<slug>] commands = [...]`, run within the department's scope.
- **G-B Skill evolution that works on any harness.** Agents cannot write `$HOLON_HOME/skills`
  (forbidden path, by design). Proposed: an execute result may carry `skill_changes`
  (department layer only); the kernel applies them after the approved plan named them, runs
  `skill check`, re-exports to every registered harness skills directory, and doctor reports
  `skills.export_stale` when an export no longer matches.
- **G-C Integrity on the heartbeat.** `tick` runs doctor (cheap) every N minutes; a new error
  becomes incident work and a Chair notification. A CI workflow runs tests and lint --strict on
  every push to the kernel repository.
- **G-D The Chair's dashboard (planned G6).** A read-only view of the graph, gate queue and
  department health. Options: a Notion mirror driven by `sync_dirty` (one-way, matching the
  organism's Notion habit), or a local SQLite browser (Datasette) over `work.db` opened read-only.

### P2: needed before widening autonomy

- **G-E Task-class taxonomy.** Track records accrue per `task_class`; free-form classes mean every
  task is novel (floor G2), so earned authority and sampling never engage. Each profile should
  declare its department's classes, and lint should flag unknown ones.
- **G-F Cost and tokens.** Harness costs (ZeroClaw `state/costs.jsonl`) are not read, so
  `cost_per_verified_result` is empty and budgets count runs, not tokens.
- **G-G Read-only guard on shared trees.** The VPS sets `trust_harness_read_only` because another
  process auto-commits the organism repository; charter's observers may run `arch`, which can
  write. Proposed: observe stages run against a read-only snapshot (`git worktree` at HEAD).
- **G-H Back-off after an incident.** A failing stage keeps being retried at every wake after its
  incident opens; park it until the incident closes.
- **G-I Claude Code execute/verify** conformance (deferred by the Chair).

### P3

`holon desk` (an employee's inbox and board in one view), span-of-control split proposals, a
memory-wire bridge for promoted lessons, VPS instance leftovers.

## 7. Harness candidates

**ZeroClaw** is the production body (VPS). **Hermes** stays a validated second body.
**jcode** (Rust, MIT, ~3 MB RAM per additional headless session) is viable as a third adapter, a
driver and an installer like Hermes:

- driver: `jcode run --json -C {workdir} --provider-profile holon -m <model> "{briefing}"`, with
  `--tools`/`--disable-tools` per posture (no write tools while observing) and `--no-update`;
- an isolated `JCODE_HOME` per instance (provider profile for the OpenAI-compatible gateway, the
  `holon mcp` stdio server in `mcp.json` with `shared: false` so each run's token stays its own);
- a `pre_tool` hook enforcing the observe posture and the department's scope roots (jcode's gate
  hooks fail open on crashes, so the kernel's own guards stay on);
- jcode's own memory and ambient mode off for Holon employees (they would fork the
  organization's memory and do unapproved proactive work), and the `swarm` tool off or limited to
  execute (sub-agents outside the work graph are invisible to the organization).

jcode's ambient mode is a single self-scheduling assistant, not a scheduler: it does not replace
ZeroClaw cron/SOP for the legacy loops, nor Holon's schedules for org work.

## 8. Decisions recorded

- 2026-10-13 (Chair): keep hermes-agent until the ZeroClaw cron migration is done; legacy
  organism migration starts only after the Chair validates this architecture; first wave in
  supervised mode; per-department scope.
- 2026-10-13 (applied under that delegation, reversible): `light` for charter/it/pilot and
  `executive` for executive; heads hold lead + checker (head, researcher, steward, verifier),
  engineers hold maker (planner, executor); `[budget] max_running_per_dept = 1`; scopes for
  `it` (`~/.hermes`, `~/.cache`, `/tmp`; `systemctl` while executing) and `charter` (`arch`).
  The reassignment events read "Chair-approved"; the Chair approved the scope and supervised
  mode, and the role split was applied as the recommended default.

## 9. Status after closing the gaps (2026-10-13, local; VPS deferred by the Chair)

| Gap | Closed by | Guard |
|---|---|---|
| G-A machine checks | `[checks]` / `.dept.<slug>` / `.class.<class>` run by the kernel after execute; failure = rework, pass = evidence for the verifier | `test_a_failing_check_is_rework_without_spending_a_verifier` |
| G-B skill evolution | `skill_changes` in the execute result, named in the plan (`skill:<skill>/<file>`, floor G2), applied by the kernel on verification, rolled back if `skill check` fails, re-exported to every registered harness directory; doctor `skills.export_stale` | `test_a_verified_change_lands_in_the_department_layer_and_every_export` |
| G-C integrity on the heartbeat | `tick` runs doctor every `doctor_every_min`, one incident per failing check; GitHub Actions runs tests and lint --strict | `test_a_doctor_error_becomes_one_incident_and_the_check_waits_its_interval` |
| G-D dashboard | `adapters/notion`: Departments, Employees, Work, Runs with the holarchy's relations; hash-based push, signed webhooks, reconcile, Chair-only inputs, `check --repair` | `tests/test_notion.py` against a strict fake API; **live Notion validation pending a token** |
| G-E taxonomy | `[classes]`; agent-proposed work outside it breaches the contract; doctor `classes.unknown` | `test_agents_use_the_departments_task_classes` |
| G-F cost and tokens | drivers record the `usage` a harness prints (jcode, Claude Code JSON) on the run | `test_a_harness_summary_is_unwrapped_and_its_usage_recorded` |
| G-G guard on shared trees | file snapshots instead of git state; `[planner] guard_ignore` | `test_a_shared_tree_others_commit_to_or_write_ignored_paths_in_is_not_a_breach` |
| G-H back-off | failing stages wait 10 min, doubling, before the next claim | `test_a_stage_whose_runs_keep_failing_opens_an_incident` |
| G-I Claude Code | deferred by the Chair | — |

Found while validating jcode for real, and fixed: a harness kept running a closing model turn
(about 12 minutes on free-stack) after its agent had submitted through `holon_submit`; the
dispatcher now stops it (`test_a_harness_still_running_after_its_result_is_in_is_stopped`).
A planner also spent its whole 3600 s timeout reading Holon's source to derive the grade floor;
plan contexts now state the floor and the approver
(`test_planners_are_told_the_grade_floor_and_the_approver`). jcode conformance then passed 5/5
(plan 6.2 min, approve 23.3, execute 10.4, verify 11.3).

Still open: the Notion mirror against a real workspace (needs an integration token and a public
URL for webhooks); the VPS redeploy of all of the above (after the Chair's local verification);
nothing yet acts on metrics automatically beyond retros; a run that submits through `holon_submit`
and is then stopped records no token usage.
