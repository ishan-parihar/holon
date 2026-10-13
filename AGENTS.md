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
| `adapters/notion/` | the Chair's dashboard: a live Notion mirror (not a harness); tested against `tests/fake_notion.py` |
| `install.py` | cold start or upgrade of a machine: instance (SQLite + Chair key), launcher, Notion databases, user units; `tests/test_install.py` |
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

- **CI** (`.github/workflows/ci.yml`) runs the same two commands on every push (Python 3.11 and
  3.13); a red run blocks nothing by itself, so look before you merge or deploy.
- **The heartbeat runs `holon doctor`** every `[healing] doctor_every_min`; a failing check opens
  an incident and reaches the Chair. Tests turn it off (`tests/helpers.py`) and test it explicitly.

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
| VPS first wave | a slow model outlived its stage lease, so the stage could be reclaimed mid-run | `test_a_running_harness_keeps_its_lease_and_a_hung_one_is_killed` |
| VPS survey | two wakes due in one tick could both pass the concurrency check | `test_the_concurrency_cap_is_checked_inside_the_claim` |
| VPS survey | a harness sandbox built for an older `[scope]` kept its old reach | `test_doctor_catches_a_sandbox_built_for_another_scope` |
| System red team | the skill-evolution loop could not finish on a sandboxed harness | `test_a_verified_change_lands_in_the_department_layer_and_every_export` |
| System red team | verification was only an LLM's judgment; nothing a command could decide was checked | `test_a_failing_check_is_rework_without_spending_a_verifier` |
| System red team | doctor ran only when a person ran it | `test_a_doctor_error_becomes_one_incident_and_the_check_waits_its_interval` |
| jcode, local conformance | the harness kept running a closing model turn (~12 min) after the agent had submitted | `test_a_harness_still_running_after_its_result_is_in_is_stopped` |
| VPS deployment | an auto-committer sharing the tree tripped the read-only guard, so it was switched off | `test_a_shared_tree_others_commit_to_or_write_ignored_paths_in_is_not_a_breach` |
| Red-team R1 | the Chair key reached a harness's environment | `test_the_chair_key_never_reaches_a_harness` |
| Red-team R1 | a harness name leaked into the kernel | `test_no_harness_named_in_kernel` |
| jcode conformance | a planner spent its whole 3600 s timeout reading Holon's source to derive the grade floor | `test_planners_are_told_the_grade_floor_and_the_approver` |
| Notion live setup | connection resets aborted push; a retried create whose reply was lost left an untracked twin page | `test_a_lost_reply_is_retried_and_the_orphan_it_leaves_is_trashed` |
| Notion schema pass | enumerations mirrored as text slugs; ids as text; one-way relations; dead columns | `test_enumerations_are_selects_and_references_are_dual_relations` |
| Notion schema pass | a kernel column could be added without deciding whether Notion shows it | `test_every_column_is_mirrored_or_accounted_for` |
| Notion schema pass | a live workspace on the older schema had to be rebuilt by hand | `test_an_older_schema_is_upgraded_in_place` |
| Notion schema pass | a page deleted for good came back, but its stages still pointed at the old page | `test_webhook_events_drive_every_inbound_path` |
| Notion schema pass | no single command built a new machine's SQLite and Notion databases | `test_a_new_machine_gets_every_database_and_a_rerun_changes_nothing` |
| Notion webhooks | a public receiver would store whichever verification token reached it first | `test_the_receiver_answers_only_on_its_secret_path` |
| Notion webhooks | without webhooks, Chair edits waited for the 5-minute full pass | `test_the_poll_fallback_asks_only_for_pages_edited_since` |
