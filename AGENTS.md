# AGENTS.md — holon (the distribution)

This is the kernel source, not an instance: no state lives here, and instances live at
`$HOLON_HOME`. If you were handed a Holon brief, read its `BRIEF.md` instead. This file is for
agents changing the kernel itself: where things are, and the laws every change must keep.

## Map

| Path | Owns |
|---|---|
| `holon/core/vocab.py` | every enumeration (roles, stages, statuses, kinds); nothing else may define one |
| `holon/core/contract.py` | the result contract per stage; validators and `schemas/` are generated from it |
| `holon/core/{db,ids,paths}.py` | SQLite, migrations with checksums, the hash-chained audit trail, ids, instance paths |
| `holon/execution/work.py` | the work graph; **the only writer of tasks, task_needs, outputs, runs** |
| `holon/execution/org.py` | departments, employees, authority, evidence-based reviews |
| `holon/execution/genome.py`, `holon/genome/*.toml` | role → stage mapping, profiles, slots |
| `holon/execution/autonomy.py` | the autonomy policy |
| `holon/execution/service.py` | the kernel: genome rules, claims, ingestion, sampling, triggers, metrics |
| `holon/execution/security.py` | the Chair key, employee keys, run tokens |
| `holon/execution/{briefs,drivers,grading,schedules,staleness,cron}.py` | the harness contract and scheduling |
| `holon/capabilities/{memory,context,skills}.py` | memory (**only writer of memory.db**), context bundles, the skills library |
| `holon/skills/` | the standard skills library; role skills hold the stage briefs |
| `holon/integrity/{lint,doctor,conformance,backup}.py` | the rubric, live-state checks, adapter conformance, backup/restore |
| `holon/mcp.py` | the organization as MCP tools (stdio), for harnesses that mount MCP servers |
| `holon/cli.py` | the command registry and its actor classes |
| `adapters/<harness>/` | everything harness-specific: driver, installer, identity and skills sync |
| `adapters/systemd/` | the heartbeat timer (`holon tick` every 2 minutes) |
| `docs/` | INTENT, GENOME, CONTRACT; `docs/design/` holds dated design records |

## Laws (enforced by `holon lint`, `holon doctor` and the tests)

1. **Stdlib only** under `holon/`.
2. **No harness named under `holon/`.** Harness specifics live in `adapters/`.
3. **One writer per database.** Other capabilities' databases are opened read-only.
4. **Status changes go through `Work._set`**, which logs a hash-chained event.
5. **Enumerations come from `vocab.py`; result shapes from `contract.py`.** Copies fail lint.
6. **Schema changes are new migration files.** An edited applied migration fails lint.
7. **Every org term binds to a mechanism** in the INTENT and GENOME tables.
8. **Every command declares its actor class** in the registry.
9. **Tests first** for any change to the graph, the contract, security or drivers.

## Verify a change

```sh
python3 -m unittest discover -s tests -t .
python3 -m holon --home "$(mktemp -d)" lint --strict   # zero errors and zero warnings before you commit
```

## Working on the kernel without losing context

- **Read the dated design records** in `docs/design/` before changing a mechanism they cover; the
  latest red-team (`2026-10-13-system-red-team.md`) lists what is built, what is open and why.
- **A defect found by a real run gets a guard test and a ledger row in the same commit.** The
  ledger below is checked by `holon lint` (`ref.guard`): every row names a test, and every test
  it names must exist, so a guard cannot be deleted silently.
- **Real-harness lessons belong in the adapter's README** (what the harness does that the
  contract does not expect) and, when the kernel changed, in GENOME or CONTRACT.
- **A new config key is documented in `docs/GENOME.md` §6** or lint fails (`bind.config`).
- **After deploying to an instance:** pull, run the tests there, `holon doctor`, and re-run an
  adapter installer whose inputs changed (`[scope]`, model, notify); doctor reports `scope.drift`
  until you do.
- **Instance specifics stay out of the repo.** Hosts, paths, chat ids and keys live in the
  instance's `holon.toml` or the installer flags, never in code, skills or docs.

## Regression ledger

| Found | Defect | Guard |
|---|---|---|
| Hermes, first real run | exported skills baked one instance's path into `$holon` | `test_new_check_and_export` |
| Hermes, first real run | Hermes ignores `OPENAI_BASE_URL` and skips stall detection for local endpoints | `test_hermes_driver_reaches_custom_endpoints_and_detects_stalls` |
| Hermes, first real run | `__pycache__` from running tests counted as an observe-stage write | `test_caches_left_by_running_tests_are_not_a_breach` |
| Hermes, first real run | a read-only breach after the agent self-submitted crashed the dispatch | `test_a_breach_after_the_agent_submitted_itself_is_recorded_not_fatal` |
| Claude Code, conformance | tools confined to the workdir, so the agent could not read its brief | `test_claude_code_driver_can_read_its_run_dir_and_gets_its_identity` |
| VPS migration | an instance migrated before checksums had none to verify | `test_idempotent` |
| VPS install | an installer dry run printed a provider key | `test_zeroclaw_installer_produces_valid_config_with_safe_roots` |
| ZeroClaw, VPS conformance | agents submitting through `holon_submit` crashed the conformance clauses | `test_a_harness_whose_agents_submit_for_themselves_passes` |
| VPS, first heartbeat | the oneshot unit's cgroup kill took every spawned dispatch with it | `test_the_heartbeat_unit_lets_spawned_dispatches_outlive_the_tick` |
| VPS, first heartbeat | the timer's `PATH` found an old `zeroclaw` without `agent -a` | `test_zeroclaw_installer_pins_a_binary_that_supports_agent_aliases` |
| VPS, first heartbeat | a stage whose runs kept failing retried forever and never opened an incident | `test_a_stage_whose_runs_keep_failing_opens_an_incident` |
| VPS survey | two wakes due in one tick could both pass the concurrency check | `test_the_concurrency_cap_is_checked_inside_the_claim` |
| VPS survey | a harness sandbox built for an older `[scope]` kept its old reach | `test_doctor_catches_a_sandbox_built_for_another_scope` |
| Red-team R1 | the Chair key reached a harness's environment | `test_the_chair_key_never_reaches_a_harness` |
| Red-team R1 | a harness name leaked into the kernel | `test_no_harness_named_in_kernel` |
