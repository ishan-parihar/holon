# AGENTS.md — holon (the distribution)

This is the kernel source, not an instance: no state lives here, and instances live at
`$HOLON_HOME`. If you were handed a Holon brief, read its `BRIEF.md` instead. This file is for
agents changing the kernel itself: where things are, and the laws every change must keep.

**Start here:** `docs/INTENT.md` (what Holon is), then this file. The reading order for a new
agent is at the end.

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
| `holon/execution/isolation.py` | the trust boundary: the runner (agent processes as the agent user), the broker (the organization for agents), setup plan and doctor's probe |
| `holon/capabilities/{memory,context,skills}.py` | memory (**only writer of memory.db**), context bundles, the skills library |
| `holon/capabilities/playbooks.py` | playbooks: a job's protocol and rubric as a versioned department skill (refined by the kernel's loop) |
| `holon/capabilities/decisions.py` | the decision-model client (`[classifier]`): lane choice, rubric judge, triage, routing; every caller has a safe default |
| `holon/skills/` | the standard skills library; role skills hold the stage briefs |
| `holon/integrity/{lint,doctor,conformance,backup}.py` | the rubric, live-state checks, adapter conformance, backup/restore |
| `holon/mcp.py` | the organization as MCP tools (stdio), for harnesses that mount MCP servers |
| `holon/cli.py` | the command registry and its actor classes |
| `adapters/<harness>/` | everything harness-specific: driver, installer, identity and skills sync |
| `adapters/systemd/` | the heartbeat timer (`holon tick` every 2 minutes) |
| `adapters/notion/` | the Chair's dashboard: a live Notion mirror (not a harness); tested against `tests/fake_notion.py` |
| `install.py` | cold start or upgrade of a machine: instance (SQLite + Chair key), launcher, Notion databases, user units; `tests/test_install.py` |
| `docs/` | INTENT, GENOME, CONTRACT: the kernel's reference and law docs |
| `docs/design/` | scrubbed design records and audits; an audit's work list separates defects fixable now from changes that wait on the Chair |

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

- **Audit a seam by tracing, not by reading claims.** A design record's statement about code is a
  hypothesis: open the cited line and check its control flow. Follow one value through every
  consumer (the same field can mean "readable" to one and "writable" to the next), grep the
  other side of each seam for a reader, ask what an "empty means ungoverned" default does when
  it becomes non-empty, and read live state only with `mode=ro` and `holon doctor` before you
  conclude. Label each finding confirmed or plausible. The worked example, with its open work
  list, is `docs/design/2026-10-16-capability-layer-audit.md`.
- **Read the dated design records before changing a mechanism they cover.** They live in the
  operator's private overlay (`docs/design/` there; see the section below) — publish a record
  here only when it is scrubbed of operator context. Its universal lessons belong where they
  are enforced: the ledger below, the adapter READMEs, GENOME and CONTRACT.
- **A defect found by a real run gets a guard test and a ledger row in the same commit.** The
  ledger below is checked by `holon lint` (`ref.guard`): every row names a test, and every test
  it names must exist, so a guard cannot be deleted silently.
- **Real-harness lessons belong in the adapter's README** (what the harness does that the
  contract does not expect) and, when the kernel changed, in GENOME or CONTRACT.
- **An interface package is done only when one test crosses the seam with the other side's real
  artifact** — the kernel's real prepared run into the runtime, the runtime's real files into
  the kernel's `submit`. Unit tests that stub the seam mark phases "landed" that have never run.
- **A new config key is documented in `docs/GENOME.md` §6** or lint fails (`bind.config`).
- **After deploying to an instance:** pull, run the tests there, `holon doctor`, and re-run an
  adapter installer whose inputs changed (`[scope]`, model, notify); doctor reports `scope.drift`
  until you do.
- **Instance specifics stay out of the repo.** Hosts, paths, chat ids and keys live in the
  instance's `holon.toml` or the installer flags, never in code, skills or docs.

## The universal repo and the personal overlay

This repository is the **universal Holon**: usable by anyone, free of every operator's context.
Each operator — the Chair included — keeps a **private overlay repository**: a fork whose `main`
tracks this repo, plus a `personal` branch that adds their own `docs/STATUS.md` (deployments,
standing decisions, what is next) and dated `docs/design/` records. The overlay's `PERSONAL.md`
holds the sync and publish runbooks.

What that means for every change here:

- **Nothing personal lands in this repo**: no hosts, paths, chat ids, endpoints, model-gateway
  names, deployment notes, or the operator's other systems. Ops logs and design records with
  operator context live in the overlay; instance specifics in `$HOLON_HOME` (the law above).
- **Design records may be published here only scrubbed** — mechanism, decision and evidence
  without the operator's context.
- **A pull request is checked for the above before merge**; instance specifics are asked to move
  to the contributor's own overlay.
- **Upstreaming flows one way**: universal work is committed here first and the overlay pulls it
  (`git merge` of this repo's `main`); nothing flows from an overlay into this repo except
  deliberate, scrubbed cherry-picks.
- **Instances move between machines with `holon backup` / `holon restore`**, never with git.
- **Publishing this repo requires the history-squash runbook** (in the overlay's PERSONAL.md):
  history that predates the split carries operator context and must never be pushed as-is.

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
| Paperclip contrast red-team | a recurring plan fired a new instance on every tick while the last one was still open, and kept firing after its work was cancelled | `test_recurring_plan_creates_preapproved_work` |
| Paperclip contrast red-team | a stage no active employee could claim (assignee paused, or nobody in reach performs it) waited forever with no signal | `test_a_stage_nobody_can_claim_is_reported_not_left_waiting` |
| Paperclip contrast red-team | nothing backed the instance up, though the organization exists nowhere else | `test_the_heartbeat_backs_the_instance_up` |
| Metrics loop | metrics were read only when a retro happened to run; a failing department with no proposals never got one | `test_a_rate_past_its_bound_makes_a_department_retro_due` |
| jcode usage | a run stopped after `holon_submit` recorded no tokens, and the `--json` summary counted cached input twice | `test_a_harness_still_running_after_its_result_is_in_is_stopped` (driver `usage_line`) |
| jcode install | a rerun failed (`provider add` without `--overwrite`) and then duplicated the `[features]` block | `test_a_rerun_keeps_one_copy_of_the_features_block` |
| jcode conformance | a planner spent its whole 3600 s timeout reading Holon's source to derive the grade floor | `test_planners_are_told_the_grade_floor_and_the_approver` |
| Notion live setup | connection resets aborted push; a retried create whose reply was lost left an untracked twin page | `test_a_lost_reply_is_retried_and_the_orphan_it_leaves_is_trashed` |
| Notion schema pass | enumerations mirrored as text slugs; ids as text; one-way relations; dead columns | `test_enumerations_are_selects_and_references_are_dual_relations` |
| Notion schema pass | a kernel column could be added without deciding whether Notion shows it | `test_every_column_is_mirrored_or_accounted_for` |
| Notion schema pass | a live workspace on the older schema had to be rebuilt by hand | `test_an_older_schema_is_upgraded_in_place` |
| Notion schema pass | a page deleted for good came back, but its stages still pointed at the old page | `test_webhook_events_drive_every_inbound_path` |
| Notion schema pass | no single command built a new machine's SQLite and Notion databases | `test_a_new_machine_gets_every_database_and_a_rerun_changes_nothing` |
| Notion webhooks | a public receiver would store whichever verification token reached it first | `test_the_receiver_answers_only_on_its_secret_path` |
| Notion webhooks | a resent verification token got 401 because one was already stored, so a subscription could never be verified after a first miss | `test_webhooks_are_verified_and_bots_ignored` |
| Notion webhooks | the tunnel dialled the edge over a dead IPv6 route after each network blip | `test_a_new_machine_gets_every_database_and_a_rerun_changes_nothing` (edge-ip-version 4) |
| Notion, lanes deploy | a new kernel column (`lane`) made every push fail on the live workspace until a manual `check --repair` | `test_a_column_added_by_a_kernel_upgrade_reaches_notion_when_the_service_starts` |
| Notion webhooks | without webhooks, Chair edits waited for the 5-minute full pass | `test_the_poll_fallback_asks_only_for_pages_edited_since` |
| Trust boundary build | a skill name was never validated, so `skill show ../../…` read any file the kernel's user could; through the broker that is the Chair key | `test_skill_names_cannot_climb_out_of_the_library` |
| Playbooks, live RSI demo | an hour-long retro was voided because one learning carried a `kind` outside the vocabulary | `test_a_bad_label_on_a_learning_is_dropped_not_fatal` |
| Playbooks, live RSI demo | the refinement's executor overwrote the playbook files itself (no `skill_changes`): the change went live before verification and without a version | `test_an_executor_that_edits_a_playbook_directly_has_the_edit_held_until_verified` |
| Playbooks, live RSI demo | a refinement planner named the playbook files by path, so the plan was graded as an ordinary outside write and its executor was not told to return them as skill changes (a direct overwrite would skip verification) | `test_a_plan_naming_a_skill_file_by_path_is_a_skill_change` |
| Playbooks, live RSI demo | refinement work carried scores but not what the runs learned: an executor's own proposed refinement and the verifiers' notes never reached the planner | `test_a_verifier_failure_opens_refinement_whose_verified_change_is_the_next_version` |
| Playbooks, live RSI demo | a push-mode agent wrote its result and left handing it in to its dispatcher, which had died: on lease expiry the finished work would have counted as a failed attempt | `test_a_result_left_on_disk_by_a_dead_dispatcher_is_handed_in_not_redone` |
| Playbooks, live RSI demo | an agent's own `holon submit` ran the checks and the rubric judge after recording the result, so the dispatcher, stopping the finished harness, killed it mid-judgment: a verifier session was spent instead | `test_a_deferred_result_is_held_from_verifiers_until_the_kernel_checks_it` |
| Playbooks, live RSI demo | a routine run's brief showed the playbook's rubric and the plan's v1 copy (stale after refinement), and told a routine run it was G0 work | `test_a_verifier_failure_opens_refinement_whose_verified_change_is_the_next_version` |
| Decision models, live script routine | the free tier's 50 decisions a day ran out (HTTP 429); the judge fell back to a verifier, but nothing said why | `test_a_decision_model_that_is_down_is_never_silent` |
| Decision models, live run over 61 recurring jobs | "reaches outside the organization" counted digests to the Chair, flagging 58 of 61 jobs (9 after the fix) | `test_reports_to_the_chair_do_not_count_as_reaching_outside` |
| Notion webhooks | the public URL was set up by hand, so a new machine had no webhooks | `test_a_new_machine_gets_every_database_and_a_rerun_changes_nothing` |
| Integration audit, live probe | the kernel and the body's runtime disagreed on the run directory's variable name, so no real run ever saw enforcement, trajectory or usage | `test_prepare_writes_the_run_without_spawning` |
| Integration audit, live probe | the operant driver shipped a literal template in its `[env]`, so every body used a false state home — a silent read-only breach | `test_operant_driver_env_is_absolute_after_install` |
| Integration audit, live probe | a database migrated by newer code ran silently under older code (two checkouts, one launcher) | `test_a_database_newer_than_the_code_refuses_to_run` |
| 2026-10-16 red-team | refused executes could still close with no decision on the refusal: sampling and the fast/script lane skips (a passing rubric judge included) never looked at the blockers the H-03 fold had stored | `test_a_refused_success_is_never_closed_by_sampling` |
| 2026-10-16 red-team | test processes wrote scratch Chair keys into the operator's real `~/.config/holon/keys` (170+ orphans; conformance had been pinned to its scratch dir, the wider test process had not) | `test_no_test_process_writes_chair_keys_into_the_real_key_dir` |
| 2026-10-16 red-team | a guard's command was the fixer's own shell, run by the heartbeat as its user — a path from any employee's result to the kernel user's privileges; a guard now names a Chair-authored `[checks.class]` entry | `test_a_guard_naming_an_unknown_check_class_is_refused_at_submit` |
| Capability-layer audit | the pooled-units render was nested inside the execute/verify branch, so plan, research and shape stages never saw what the department pools | `test_pooled_units_join_the_reach_and_render_in_briefs` |
| Capability-layer audit | pooled roots joined the writable reach: grading passed a write into one, policy.json fenced nothing, isolation granted write, the ZeroClaw sandbox allowed it | `test_pooled_roots_are_read_only_in_policy_grading_and_isolation` |
| Capability-layer audit | pooled services' entry binaries never reached the terminal fence — and deriving one from an empty commands list would have refused every other command the department runs | `test_a_department_that_pools_but_lists_no_commands_is_never_derived_a_fence` |
| Capability-layer audit | skills were exported once per shared home, so a department fork of a skill appeared twice: the standard copy in the harness's skill list and the fork in the brief | `test_a_run_carries_its_departments_resolved_skills` |
| Capability-layer audit | the operant installer pinned a `[memory]` provider the runtime ignores and left the continual-harness lane unpinned, promising memory isolation that did not exist | `test_operant_installer_isolates_and_writes_both_postures` |

## Reading order for a new agent

1. `docs/INTENT.md`: what Holon is and the laws.
2. This file: the code map, the laws lint enforces, the regression ledger.
3. `docs/GENOME.md` (departments, roles, configuration) and `docs/CONTRACT.md` (the harness
   contract).
4. The README of the adapter you touch (`adapters/<name>/README.md`), which records what the
   real harness does.
5. Any audit in `docs/design/` whose work list touches what you are changing: its §1 defects are
   yours to fix (tests first, guard + ledger row); its §2 changes wait on a decision.
6. In the operator's overlay (not public): `docs/STATUS.md` and the dated `docs/design/` records —
   the deployment, the standing decisions and the design history.
