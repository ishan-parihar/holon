# AGENTS.md — holon (the distribution)

## Contents

- [Map](#map)
- [Laws](#laws-enforced-by-holon-lint-holon-doctor-and-the-tests)
- [Verify a change](#verify-a-change)
- [Working on the kernel without losing context](#working-on-the-kernel-without-losing-context)
- [The universal repo and the personal overlay](#the-universal-repo-and-the-personal-overlay)
- [Regression ledger](#regression-ledger)

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
| `holon/execution/bylaws.py`, `holon/bylaws/*.toml` | role → stage mapping, profiles, slots |
| `holon/execution/autonomy.py` | the autonomy policy |
| `holon/execution/service.py` | the kernel: bylaws rules, claims, ingestion, sampling, triggers, metrics |
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
| `docs/` | INTENT, BYLAWS, CONTRACT: the kernel's reference and law docs |
| `docs/design/` | scrubbed design records and audits; an audit's work list separates defects fixable now from changes that wait on the Chair |
| `TODO.md` | the register: open work items with their status and verdicts, seeded from the audit work lists (`scripts/genome status` is the supervised transition) |
| `docs/decisions/` | immutable decision records (AD-NNN), minted when the Chair ratifies a direction change; superseded only by a newer AD |
| `docs/archive/` | superseded docs, with a tombstone at the original path and a row in its `INDEX.md` |

## Laws (enforced by `holon lint`, `holon doctor` and the tests)

1. **Stdlib only** under `holon/`.
2. **No harness named under `holon/`.** Harness specifics live in `adapters/`.
3. **One writer per database.** Other capabilities' databases are opened read-only.
4. **Status changes go through `Work._set`**, which logs a hash-chained event.
5. **Enumerations come from `vocab.py`; result shapes from `contract.py`.** Copies fail lint.
6. **Schema changes are new migration files.** An edited applied migration fails lint.
7. **Every org term binds to a mechanism** in the INTENT and BYLAWS tables.
8. **Every command declares its actor class** in the registry.
9. **Tests first** for any change to the graph, the contract, security or drivers.
10. **Two stacks, never one system.** This repository — the `holon` kernel, this overlay and the
    `project-genome` skill — is **A**. The Chair's production venture runtime (Paperclip, ZeroClaw,
    the Hermes organism, the VPS) is **B**. They share vocabulary ("departments", "routines",
    "employees", "ventures") and **nothing else**. A finding about one is never evidence about the
    other: a gap in A is A's to close, an outage in B is B's, and A's work never waits on B. The
    bridge between them (the ventures import) is a **separate, Chair-gated program**, not A's
    critical path. Read `docs/design/2026-10-20-the-a-side-program-boundary-and-solutions.md`
    before reporting anything about "the organism", "the ventures" or "the migration".

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
- **Read the dated design records before changing a mechanism they cover.** They live in
  the operator's private overlay (`docs/design/` there; see the section below) — publish a record
  here only when it is scrubbed of operator context. Its universal lessons belong where they are
  enforced: the ledger below, the adapter READMEs, BYLAWS and CONTRACT. A design record superseded
  by a later one is moved to `docs/archive/` with a tombstone at its original path — never edited
  past its status line.
- **A defect found by a real run gets a guard test and a ledger row in the same commit.** The
  ledger below is checked by `holon lint` (`ref.guard`): every row names a test, and every test
  it names must exist, so a guard cannot be deleted silently.
- **Real-harness lessons belong in the adapter's README** (what the harness does that the
  contract does not expect) and, when the kernel changed, in BYLAWS or CONTRACT.
- **An interface package is done only when one test crosses the seam with the other side's real
  artifact** — the kernel's real prepared run into the runtime, the runtime's real files into
  the kernel's `submit`. Unit tests that stub the seam mark phases "landed" that have never run.
- **A new config key is documented in `docs/BYLAWS.md` §6** or lint fails (`bind.config`).
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
  by running `scripts/sync-public.sh` in the overlay checkout; nothing flows from an overlay into
  this repo except deliberate, scrubbed cherry-picks.
- **Instances move between machines with `holon backup` / `holon restore`**, never with git.
- **Publishing this repo requires the history-squash runbook** (in the overlay's PERSONAL.md):
  history that predates the split carries operator context and must never be pushed as-is.

### The two repos are synced by a script, never by hand

The overlay's `scripts/sync-public.sh` is the only way work moves between this public repo
and an overlay. It lives in the overlay, not here — run it from the overlay checkout, on
the `personal` branch:

```sh
scripts/sync-public.sh              # sync, gate, and push
scripts/sync-public.sh --no-push    # sync and gate, but do not push
```

What it does, in order: fast-forwards the overlay's `main` to this repo's `main`; merges
`main` into `personal`; checks the invariant that outside the overlay's file set, `personal`
equals `main`; runs the test suite and the strict lint; then pushes `main` and `personal`.
A red gate or a drifted file stops it before anything is pushed.

So the loop is: **make a universal change here, commit and push it, then run the script in
the overlay.** Do not hand-merge the public repo into the overlay, and do not copy files
between the two checkouts — the script is what keeps the branches, the invariant and the
gates in agreement, and a hand merge skips all three. When the script reports a conflict,
the rule is: overlay files keep the overlay's version, universal files take `main`'s, then
commit and run it again.

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
| Capability-layer red-team | the pooled entry binaries joined only `commands`: a department fenced solely by `act_commands` ran its act posture refusing the pooled services its brief advertised | `test_a_fence_that_exists_only_in_act_commands_still_names_the_pooled_services` |
| Live instance, 2026-10-17 | the `api_memory` view was created out-of-tree selecting `created_at`, a column no migration defines, so `holon lint --strict` died with "no such column" instead of checking the read API | `test_a_drifted_api_memory_view_is_repaired_by_migration` |
| KB audit, 2026-10-17 | cross-scope recall was the bundle budget's first casualty: large stage material crowded it out and the gaps block trailed at the bottom, so agents never learned what they never got | `test_recall_floor_keeps_cross_scope_recall_and_the_gaps_lead` |
| KB audit, 2026-10-17 | every execute run wrote an episode, but nothing could search them: "what have we tried on problems like this" was answerable only through the bundle's pre-chosen slice | `test_episodes_are_searchable_across_scopes` |
| KB audit, 2026-10-17 | the agent command fence dropped the kernel's own synthesis commands (playbook evidence, events, skill path, episodes, show, recall, law) — an executor refining a playbook could not read the evidence its refinement learns from | `test_the_agent_fence_carries_the_knowledge_join_commands` |
| KB audit, 2026-10-17 | distilled law copies could silently drop a role, stage, scope or memory kind the vocabulary carries — agents operate from the copies | `test_a_law_copy_drifting_from_the_vocabulary_is_caught` |
| KB audit, 2026-10-17 | search stripped source weight: an agent could not weigh a pinned Chair constraint against a low-confidence guess, nor follow provenance to its run | `test_search_renders_source_weight` |
| KB audit, 2026-10-17 | the Chair's manual promote wrote no `derived_from` edge while consolidation's did — provenance depended on the promotion path | `test_chair_promote_writes_the_derived_from_edge` |
| Memory-boundary red-team, 2026-10-17 | the body's memory-wire store sat outside `holon backup`'s trees — an instance migrated by backup/restore silently lost every body's working memory | `test_the_backup_covers_the_adapter_homes` |
| Memory-boundary red-team, 2026-10-17 | every operant body shared one `DEFAULT_BANK` memory-wire store — colleagues' working contexts were mutually readable and nothing was portable | `test_the_kernel_formats_the_per_run_memory_placeholder` |
| Fence seam, 2026-10-17 | `memory add` fenced only emp:/dept:/org: any employee could write steering kinds into any class or project scope, and every covering run read them as binding law | `test_shared_scopes_are_fenced_for_employees` |
| Read surfaces, 2026-10-17 | an item's provenance graph was unexplorable: no command rendered a supersedes chain or derived-from edges | `test_memory_show_renders_the_provenance_graph` |
| Read surfaces, 2026-10-17 | synthesis was push-only: no joined answer existed across memory, episodes, skills, playbooks and gold standards | `test_recall_joins_memory_skills_and_verified_results` |
| Read surfaces, 2026-10-17 | the law layer was unreachable at runtime, so agents operated from possibly-stale distilled copies with no source to consult | `test_law_show_serves_the_distributions_own_docs` |
| Dormant seam, 2026-10-17 | the external strategy stores' schemas lived only in the bundle's SELECTs — a drifted store would fail open and invisibly; a present store now must declare a known api_version and lint proves the contract columns | `test_a_present_external_store_declares_a_known_version` |
| Two-stack conflation, 2026-10-20 | the docs that stated "never conflate them" were design records an agent reads *after* forming a model, so A's own gaps were reported as B's work and A's completion was read as the ventures' migration; the boundary is now a law (`AGENTS.md` law 10, `INTENT.md` design law 7) and lint's `boundary` pack fails a doc that asserts one system | `test_the_boundary_lint_pack_catches_a_conflated_doc` |
| jcode seam, 2026-10-20 | the jcode adapter's README claimed an MCP server sees the run's environment — true on jcode v0.93, false on v0.99.0, where the run token never reaches the server and `holon_submit` can never bind to a run | `test_the_run_token_reaches_a_stdio_mcp_server` |
| jcode seam, 2026-10-20 (re-proved) | **the "v0.99.0 drops the token" diagnosis was false** — the token reaches the MCP child; the probe had used an unreal token, and an unreal token falls back to the chair principal, which looks like a missing token. The real defect is that a lost token **silently becomes the chair** (fail-open) | `test_a_harness_without_the_token_is_refused_not_promoted_to_chair` |
| Live conformance, 2026-10-21 | the observe posture disabled every file tool while the brief named `$result_path` first: a real planner produced a complete envelope, was refused, and stopped with it unhanded | `test_the_driver_keeps_a_write_path_in_the_observe_posture` |
| Live conformance, 2026-10-21 | an observe policy carried `commands: []`, so a harness refused even a read-only probe under lockdown and the kernel read the refusal as a blocker | `test_an_observe_stage_can_probe_what_it_reads` |
| Live conformance, 2026-10-21 | `holon_submit`'s schema read only `oneOf`, so every stage except `plan` — and the whole stage-less server — published a free-form object and a coercing client wrapped its lists | `test_the_stage_less_server_publishes_types_too` |
| Live conformance, 2026-10-21 | the kernel published a positional's name (`slug`) as the MCP property, a runtime derived `--slug` from it, and the CLI rejected it with a usage dump a body burned calls on | `test_a_named_property_sent_as_a_flag_is_accepted` |
| jcode seam, 2026-10-20 (fail-closed) | a run token that is present but unknown must be refused, not downgraded to the chair principal | `test_an_unknown_run_token_is_refused_rather_than_falling_back` |
| operant harness, 2026-10-20 | the driver and the smoke check pinned a **stale** `/usr/local/bin/operant` (root-owned, 2026-10-17, built against ONNX Runtime 1.20.1) while the repo's pin is 1.24.4 — so every operant run died at the loader and the Tools department waited. The error text pointed at the library, which was correct; **a harness that cannot start must block its department, not read as idle** | `test_a_harness_whose_library_is_incompatible_fails_the_smoke_check_closed` |
| KB audit, 2026-10-20 | `scripts/genome` was re-pinned twice while `holon/vendor/genome.py` stayed on the previous release, so a knowledge base could pass `holon kb run ... lint` and fail the distribution's own `scripts/genome lint` — two copies of one CLI, two rulesets | `test_the_vendored_cli_is_the_same_build_as_scripts_genome` |
| Capability layer, 2026-10-20 | the manifest directory was renamed `genome/units` → `bylaws/units`, but a manifest left at the old path is invisible to the kernel, so `holon bylaws sync` emptied the units tables with no error and no lint finding — one command from losing the whole capability layer | `test_a_manifest_outside_the_manifest_directory_never_empties_the_capability_layer` |
| Chair control surface, 2026-10-23 | `isolation apply` was the one destructive command with **no gate at all**: `restore` and `keys gc` require an explicit `--force`, this one rewrote every ACL between the kernel's user and every agent on a bare return-press. It surfaced while deciding which commands a chat surface may offer — a one-tap button would have re-fenced the organization with no second look. `isolation plan` stays ungated on purpose | `test_isolate_apply_refuses_without_force` |
| Import fidelity, 2026-10-23 | the mini-YAML reader unescaped a single-quoted scalar, so a pool's `cmd: '… \"healthy\" …'` imported as `"healthy"` — the shell would run a command that is not the one the operator approved | `test_an_escaped_quote_inside_a_double_quoted_scalar_reads_as_a_quote` |
| Import fidelity, 2026-10-23 | the same reader stripped the fold marker from every continued line and joined with a space, so a double-quoted `\` (YAML's escaped line break) became `\ ` and a single-quoted `\` vanished from a value whose backslash was literal | `test_an_escaped_quote_inside_a_double_quoted_scalar_reads_as_a_quote` (the folded cases) |
| Placement health, 2026-10-23 | `scope.stray` collected workdir names one level down, so a root directory that holds a live work's workdir (`<root>/routines/<plan>`) was reported as owned by nothing — a false positive that hides the real signal | `test_a_root_directory_whose_child_a_work_names_is_not_stray` |
| Live conformance, 2026-10-21 (G-e) | a harness's own lockdown gate refused a read-only probe *and* the observer's result hand-in, because the observe posture carried no allow entry — and those refusals became blockers on work the body was only asked to read | `test_operant_installer_isolates_and_writes_both_postures` |
| Migration foundation, 2026-10-21 | an absent `status` key silently paused **every** protocol in the only importable pool, and the apply's `except …: continue` reported `routines: 0` beside `protocols: 4` with no record — absence is not a declaration of pause, and a drop is named | `test_a_protocol_that_declares_no_status_is_not_silently_dropped` |
| Migration foundation, 2026-10-21 | the department export copied the source machine's `workdir` verbatim: a live routine's workdir was another harness's dead session scratch dir, and the archive would have handed it to the first machine that imported it | `test_the_archive_does_not_carry_the_source_instances_workdir` |
| Refusals seam (H-03), 2026-10-21 | the on-disk refusal record harnesses write into the run dir was read by **nothing** in the kernel: a body that refused a call the harness's own way lost it, and with it the blocker the verifier is promised. The envelope still wins; the on-disk record is the fallback | `test_a_refusal_the_body_wrote_only_to_disk_is_not_lost` |
| Migration foundation, 2026-10-21 | the dry run could not see what the apply would refuse — the `script`-lane refusal lives in `add_routine`, which a survey never calls — so a real pool's survey said `skipped: []` while its apply filed 3 of 4 and named nothing about the fourth. A survey now predicts its own refusals | `test_a_dry_run_predicts_what_the_apply_would_refuse` |
| Measurement seam, 2026-10-21 | a caller's own shell budget shorter than four stages of real work killed the harness mid-clause and the run read `FAIL plan — no result.json`: a false 1/5 for a harness whose plan, approve and execute had all completed | `test_a_slow_harness_is_not_reported_as_a_broken_one` |
| Import fidelity, 2026-10-20 | the "an outside-reach import lands OFF" guard read outside-reach from the decision model alone, so when the model was off or rate-limited every import read as internal and the outreach fleet landed enabled with a clock — one bulk approval lit it | `test_an_outside_reach_import_starts_disabled_with_the_decision_model_down` |
| Import fidelity, 2026-10-20 | the import asked the decision model whether a protocol reached outside, while the kernel's own deterministic rule (`_external_by_name`, the verbs in the protocol's words) went unused — so the same pool read as internal under one path and outside under another | `test_the_outreach_check_sees_the_repaired_text` |
| Import fidelity, 2026-10-20 | the mini-YAML reader unescaped `\uXXXX` with four hex digits and `\xXX` with four too, so every `\x` escape was left raw: the pools write a multiplication sign as `\xD7`, and a charter read that way showed the operator's escape sequence instead of the character | `test_it_reads_every_pools_yaml_the_way_a_yaml_library_does` |
| Trust boundary, 2026-10-20 | the knowledge-base tree was in neither the agent user's read reach nor its write reach, so under isolation a body could not read the KB it was bound to — `holon kb run` worked (the kernel runs the CLI) but reading or diffing its own KB was denied | `test_a_knowledge_base_is_readable_by_the_agent_user_but_never_writable` |
| Taxonomy, 2026-10-20 | `hr hire --task-classes` was write-only — the employee's classes were stored and read by nothing, so the `dept:class` namespace named a class without binding it to a gate: a specialist claimed work in a class their record did not name | `test_an_employee_may_only_claim_the_task_classes_their_record_names` |
| Taxonomy, 2026-10-20 | the class gate as first written also blocked *assigned* work, so a Chair's assignment to a specialist in a class they do not hold stranded the work forever — nobody else may claim assigned work | `test_a_narrowed_employee_still_claims_the_work_assigned_to_them` |
| Taxonomy, 2026-10-20 | a stage whose class no employee in reach holds can never be claimed, and the doctor's unstaffed check knew nothing of the class gate: the work waited forever with no signal | `test_a_class_nobody_in_reach_holds_is_reported_not_left_waiting` |
| Taxonomy, 2026-10-20 | the class check as first written reported every classed stage on an instance whose employees named no classes — five false errors on a healthy live instance, because an unnarrowed employee may still pick the work up | `test_an_unnarrowed_employee_keeps_a_classed_stage_claimable` |
| Taxonomy, 2026-10-20 | a typo in `--task-classes` created a class the taxonomy does not carry: the employee could claim nothing, and nothing said so | `test_a_hire_may_not_name_a_task_class_the_taxonomy_does_not_carry` |
| Taxonomy, 2026-10-20 | the class gate was invisible to the employee it narrows — the identity card named the roles, never the classes the record held | `test_an_employee_is_told_the_task_classes_they_hold` |
| Knowledge layer, 2026-10-20 | `holon/capabilities/kb.py` cited `docs/plans/2026-10-19-holon-knowledge-layer.md` as its plan, and the file did not exist: the module named a plan no reader could open, and the phases (K1–K4) lived only in the code's section comments | `test_the_plan_the_code_cites_exists_and_names_the_phases` |
| Harness seam, 2026-10-20 | an MCP client that cannot type a field coerces an untyped array to an object and wraps it as `{"item": [...]}`; `holon_submit` published `result` as a free-form object with no `properties`, so the client had no field types to coerce *to* and guessed — a body that produced a complete plan had it rejected with seven field complaints, and the plan was lost | `test_a_wrapped_list_is_accepted_where_the_contract_says_list` |
| Harness seam, 2026-10-20 | the tool's schema lookup called `Path(...)` without importing it, and the bare `except Exception` around it turned the `NameError` into "no stage" — so the contract was never published, silently, and the client kept guessing | `test_the_submit_tool_publishes_the_result_envelope_not_a_free_form_object` |
| Harness seam, 2026-10-21 | a real jcode verifier produced a complete pass verdict with three genuinely good improvement proposals, written as content-only objects; the kernel rejected the whole result with six field complaints — the brief taught the proposal shape nowhere and the contract wanted {title, intent} — so a correct verdict was lost over a shape the body never chose | `test_a_content_only_proposal_is_repaired_not_rejected` |
| Harness seam, 2026-10-21 | every observe-stage brief told the body to write result.json, but the lockdown seat denies file_write — the primary route was impossible and the body discovered it only by being refused. The rule now lives in the employee skill: holon_submit (or the JSON as the final message) IS the primary route under lockdown | `test_the_runs_brief_names_the_schema_it_must_write` (the brief block) |
| Harness seam, 2026-10-20 | a body needed the envelope's exact shape and could not get it: the schema lived in the distribution, which was in the agent user's read reach and simultaneously forbidden by the brief ("Holon's own source code is not an input") — so an operant body spent 66 tool calls hunting for a file that existed and was visible | `test_the_runs_brief_names_the_schema_it_must_write` |
| Harness seam, 2026-10-20 | a body that never submitted left nothing behind: the run failed, the stage recycled, and no event named what it had been doing, so a repeated stall was indistinguishable from a single one | `test_a_stalling_body_records_that_it_produced_nothing` |
| Driver lifecycle, 2026-10-20 | every shipped driver declared a 3600s timeout while a `plan` stage leases 1800s and a `verify` one 1200s, so the lease sweep recovered work the harness was still running: the stage returned to ready under a live run and two bodies contended for one result file | `test_a_drivers_deadline_never_exceeds_the_stages_lease` |
| Driver lifecycle, 2026-10-20 | a body's `notify` command exits 0 while printing "no [chair] channels configured", so the kernel advanced its `notify_seq` cursor, recorded `notify: sent`, and never retried the digest — the Chair heard nothing about a lane breach. A `[notify] dropped_marker` now names that text | `test_a_command_that_drops_the_message_and_exits_zero_is_a_failed_send` |
| Driver lifecycle, 2026-10-20 | a body stopped mid-run lost its whole stdout: Python buffers a pipe that is not a terminal, so a killed body's reasoning — the only evidence of what it tried — was gone. Its output now survives | `test_a_stalled_body_is_stopped_while_its_lease_is_still_live` |
| Red-team R2, 2026-10-22 | the placement audit's own stray check cried wolf on the live instance: `.clock/` is the heartbeat's liveness stamp (the operant gateway's clock organ runs `holon tick` every 2 min and stamps it), but the kernel's vocabulary did not own it — a false `scope.stray` that hid the real signal, which was the **absence** of a liveness check: doctor could not see a dead heartbeat at all | `test_the_heartbeat_liveness_dir_is_not_stray`, `test_doctor_warns_when_the_heartbeat_stamp_goes_stale`, `test_tick_stamps_the_heartbeat_liveness` |
| Artifacts audit, 2026-10-22 | `artifacts` is the one contract field with **no reader at all** — defined in `contract.py`, named once in the executor skill, consumed by no line of code: a result's durable work rotted in `outputs`, and two of six live pointers had already stopped resolving. It is now validated as paths, graded against the reach (A6), and pinned to memory as an `artifact` item at the work's scope with the run's id in the provenance (A2). The executor brief told the body to write "paths, URLs, commit ids" — URLs being the first thing the validator refuses, so the brief said the opposite of the contract | `test_submitted_artifacts_are_pinned_to_memory` |
| Artifacts audit, 2026-10-22 | the contract's `artifacts` field accepted any string, so a typo or a prose line passed validation and rotted undetected — the validator now refuses URLs, empty segments and multi-line entries, naming the offending index; and grading walked `writes` but never `artifacts`, so a result claiming a durable artifact beyond its reach graded as routine — an absolute artifact outside workdir+reach now raises the floor exactly as a write does | `test_artifacts_entries_must_be_paths`, `test_an_artifact_outside_reach_raises_the_floor` |
| Artifacts audit, 2026-10-22 | a re-submitted identical artifact duplicated the pin (two runs, two items, one truth); and a failed run pinned its phantom artifacts, teaching the organization to trust paths that do not exist. Reinforcement is memory's own fingerprint rule, proved at the pin seam, and failures pin nothing | `test_a_resubmitted_artifact_reinforces_rather_than_duplicates`, `test_a_failed_result_pins_nothing` |
| Artifacts audit, 2026-10-22 | no law said where durable things live, so each body improvised: a stray root directory grew on a live instance and became five tasks' workdir. The execute brief now states the placement law once (workdir or a declared scope root, every durable thing named in `artifacts`) | `test_the_execute_brief_names_the_placement_law` |
| Chair channel, 2026-10-22 | the digest read `events JOIN tasks` and nothing else, so `msg send`/`msg request` landed in `messages` and the Chair never learned of them: a body blocked on which key to use had no path to its owner, 17 live messages with 0 addressed to the Chair, and nothing reported the gap. Unacked `question`/`request` messages to the Chair now fold into `pending_for_chair` and are acked on delivery | `test_an_employees_question_to_the_chair_reaches_the_digest` |
| Chair channel, 2026-10-22 | `thread_id` was a column no path in the kernel ever wrote (`_message` writes `thread_id or mid`) and nothing read, so a conversation was not representable: the answer to a question was indistinguishable from a new remark. `Work.reply` answers in the thread it answers and acks what it answered; `msg reply` and `msg thread` are its CLI | `test_a_reply_joins_the_thread_it_answers` |
| Placement health, 2026-10-22 | the heartbeat's liveness stamp (`246aaa5`) carried two comments naming a harness in the kernel, which is law 2, so `holon lint --strict` failed with two `agnostic.harness` errors and the distribution could not pass its own rubric — and its own `scope.stray`/`heartbeat.stale` tests then failed on the full-suite order. The comments name the mechanism, not a product | `test_no_harness_named_in_kernel` |
| Artifacts audit, 2026-10-22 | an artifact pointer that stopped resolving was invisible until someone needed it: two of six live pointers had already rotted, and nothing re-read them. Doctor's `artifacts.dangling` re-resolves every execute result's artifacts (relative ones against their workdir), and `scope.stray` names a directory at the instance root that no workdir names, no `[scope]` root covers and no kernel vocabulary owns | `test_doctor_reports_a_dangling_artifact_pointer`, `test_doctor_reports_a_stray_root_directory` |
| Artifacts audit, 2026-10-22 | gc could collect a run directory a pinned artifact still pointed into, breaking the pin's promise silently: memory's `artifact` items are now consulted before any dir is collected, and a pointed-at file (with its envelope) survives the sweep | `test_gc_keeps_a_run_dir_a_pinned_artifact_still_points_into` |
| Artifacts audit, 2026-10-22 | the units layer had the pooling mechanism and no workflow: nothing ever turned "a script a department keeps running" into a declared, pooled, health-checked capability (one manifest on the live instance; ventures' scripts scattered where they were born). `bylaws draft` (employee) proposes a manifest that seeds nothing; `bylaws promote` (chair) moves it into the manifest directory only after the same validation lint runs | `test_a_draft_unit_lands_only_when_the_chair_promotes_it` |
| Register drift, 2026-10-22 | TODO.md said H4 "blocked: a document knowledge base, waits on the Chair" for three days after the K1–K4 layer shipped, and H10's six Tier-0 blockers were true on 2026-10-20 and false on 2026-10-22 — a register whose rows lie corrupts every plan built on it. Lint's `register` pack now flags a row marked blocked on a mechanism the registry demonstrably carries (the row's own backticked citation, matched as a full path or a family); H4 and H10 were corrected in place with their re-measured verdicts | `test_a_blocked_register_row_for_a_shipped_mechanism_is_drift` |
| Live instance, 2026-10-21 | cancelling a work left its `retro` stage `ready`: the stage used to be exempt from the cancel sweep, so a cancelled playbook refinement kept offering its retro to any claimer — a body redoing work the Chair had withdrawn (found cancelling the stale board: two retros survived eight cancels). A repeat cancel also skipped the sweep, because `_finish` returned early on already-terminal work: the kernel's department-retro loop re-opens a root whose stage still says ready, which is how one stale retro survived every cancel until the sweep ran on terminal work too | `test_a_repeat_cancel_still_sweeps_the_stage_it_left_behind` |
| Survey, 2026-10-21 | the trust-boundary survey claimed 250 world-readable Chair keys; the dir was 0700 and every file 0600, so no other user could read them — the real defect was the 250 orphans (4 from the conformance era). `holon keys gc` now collects them, into quarantine rather than into nothing, and reports the mode so the claim is checkable | `test_the_key_dir_mode_is_reported` |
| Reachability audit, 2026-10-21 (F1/F8) | the MCP fence was a hand-maintained tuple, so a command added to the registry with actor `any` was silently unreachable to a mounted employee — `task list` among 23 of them. An employee could see work in its brief and could not run the command that lists the board | `test_the_allowlist_is_derived_from_the_actor_class_not_hand_listed` |
| Reachability audit, 2026-10-21 (F1/F8) | the broker carried a *second* hand-maintained list, so a command could be reachable through one gate and absent from the other: `task list` reached the broker through `BROKER_EXTRA` while being absent from `AGENT_COMMANDS`, and a body that discovered work it could not claim was waiting in the same seat that told it to | `test_a_command_the_registry_declares_any_reaches_the_fence` |
| Reachability audit, 2026-10-21 (F5/F6) | a knowledge base bound to no department was impossible: `kb seed --dept` was `required=True`, so the distribution's own law and history had nowhere to live and an agent was judged against a rule it was given no way to read. `--dept` now takes `org` (or omits) for the organization's own KB — readable by every employee, writable by the Chair alone | `test_an_org_scoped_kb_is_readable_by_every_employee_and_writable_by_none` |
| Reachability audit, 2026-10-21 (F3) | the law lived in a store `recall` never joined: one call answered "what did we learn" and left "what is the rule" unanswerable, so an agent refined a playbook against a rubric it could not read | `test_recall_joins_the_law_the_distribution_states` |
| Reachability audit, 2026-10-21 (F2) | cross-scope recall was the bundle budget's first casualty, so an org-scoped KB crowded out a smaller department's own material and the gaps block trailed at the bottom: the agent learned what it did get, never what it never got | `test_recall_degrades_to_silence_when_no_kb_is_seeded` |
| Reachability audit, 2026-10-21 (F7) | `scripts/genome lint` and `holon lint --strict` were two gates over one repository with two rubrics, and they disagreed — the repo's own gate reported an unlinked page (W009) the strict lint was silent about, so an agent running one of the two believed it was clean | `test_the_kernels_lint_carries_the_distributions_own_genome_checks` |
| Import fidelity, 2026-10-21 | the mini-YAML reader had no branch for a block scalar, so `purpose: >-` read as the value `>-` and the charter's own prose was dropped: eleven pool files use one, and a `purpose` that imports as a constructor is worse than a refused one. A folded block now joins its lines the way a library joins them, with YAML's chomp indicator honoured | `test_a_block_scalar_is_folded_the_way_a_yaml_library_folds_it` |
| Backup, 2026-10-21 | every daily backup copied a body's own harness binary: `adapters/operant/home/aft/aft-v0.59.0/aft`, which the adapter installer re-downloads through `install_lib.pin_binary` and is therefore not instance state. A 683 MB instance produced 125 MB backups and grew 101 MB per retained release for nothing. A backup now excludes them — and `holon restore`, which copies each tree out rather than merging, keeps the live binary instead of deleting the only copy of it | `test_the_backup_excludes_binaries_the_installer_reproduces` |
| Retention, 2026-10-21 | `[retention] backup_keep` was documented in BYLAWS §6 and read by no line of code, so nothing ever removed a backup: 443 MB accumulated in six directories, 123 MB a day, on the one machine where the organization exists nowhere else. The heartbeat and `holon gc --apply` now prune past the count and report what they removed. Two backups in the same second also shared one path, so `mkdir` failed and the second was lost while the tick reported one taken | `test_the_backup_keeps_only_what_its_keep_count_names` |
| Cold start, 2026-10-21 | every systemd unit the installer wrote named the checkout it was built from — `ExecStart=/home/…/holon/bin/holon tick` — so a machine could not move or delete that tree without every tick dying with "file not found" and nothing saying why. The launcher already guarded cross-checkout contamination and `--repoint` was the operator's word, but no guard existed for the same class of pinning one layer down. The units now resolve the launcher through the PATH they already set, which survives a move | `test_a_unit_survives_the_checkout_it_was_installed_from_moving` |
| Cold start, 2026-10-21 | `holon init` recorded `parents[1]` of `holon/cli.py` while the doctor compared `parents[1]` of `holon/integrity/doctor.py` — one decision (which directory is the distribution), two hop counts, and every fresh cold start therefore warned `code.root` about a stale checkout that did not exist. A warning nobody can act on trains an operator to ignore the doctor. The root is now decided by structure in one place both sides import, and the guard is the cold start itself | `test_a_cold_started_instance_is_not_reported_as_a_stale_checkout` |
| Migration FK defence, 2026-10-21 | a `.sql` migration that rebuilds a referenced table ran with foreign keys ON, so `0017_intake_source.sql`'s `DROP TABLE tasks` failed with `FOREIGN KEY constraint failed` while `runs`, `outputs` and `task_needs` pointed at it — and the Notion mirror, which constructs the writer `Kernel` on every push tick, crash-looped on it for six hours (701 restarts) | `test_a_table_rebuilding_sql_migration_applies_over_referenced_rows` |
| Lane fail-open, 2026-10-21 | a routine whose cron fires every few minutes landed as a `full` routine when no decision model was available: the lane defaulted to the most expensive one, so a `*/5` job bought a body on each of its 288 daily firings, unattended. A cadence the kernel cannot justify is now refused by name, in the routine and in the survey | `test_an_import_without_a_decision_model_does_not_buy_a_body_every_5_minutes` |
| Live instance, 2026-10-21 (C2) | a recurring routine's `workdir` was a scratchpad from a retired harness era that no longer exists: the driver silently falls back to its run directory, so the body ran against an empty dir for an hour against inputs its plan named, and the work was recovered only by the lease sweep. `holon doctor` now reports `workdir.missing` for any ready stage whose workdir is gone | `test_a_ready_stage_whose_workdir_is_gone_is_reported` |
| Kernel, 2026-10-21 | a comment naming a harness era inside the kernel's own doctor check failed `agnostic.harness` (law 2, and the guard that enforces it) | `test_no_harness_named_in_kernel` |
| Validation of that gate, 2026-10-23 | the fix repaired the plan's printed instruction and two design records, but `doctor`'s own `isolation.reach` remediation still named `holon isolation apply` bare — unreachable while `isolation.off` is the warning it is today, live the moment the root isolation step lands. A message that names the fix must name the fix that works | `test_no_remediation_names_a_command_the_gate_would_refuse` |
| Validation of that gate, round two, 2026-10-23 | the fix repaired `doctor`'s `isolation.reach` strings and then went looking for the class: `retention.overdue` said `holon gc --apply`, which `ValueError`s on an instance with no recent backup. Two commands, one surface, one defect | `test_no_remediation_names_a_command_whose_gate_would_refuse_it` (tests/test_integrity.py, the guard round two) |
| Operator skill, 2026-10-23 | `moves.md` told the agent `holon gc --apply` "is the one move here that needs no approval" — measured on a fresh instance it returns `PermissionError` without a recent backup or `--force`, so it needs the Chair's approval in one of two forms. A different defect from the remediation class, same family: a claim about a gate, in the skill that manages the system. The `SKILL.md` moves table then carried the same flag-less command (no approval claim, so the claim loop could not see it), and the guard now reads both files the skill's readers read. The export consequence was closed too: both defects were in the library, so neither reached the harness until `holon skill export` re-rendered — measured as `skills.export_stale` (4 files) on the live instance, then 0 warnings past `isolation.off` | `test_a_skills_claim_that_a_move_needs_no_approval_is_true`, `test_force_is_the_chairs_call_to_collect_with_no_backup` |
| Isolation runner unit, 2026-10-23 | `setup_plan` wrote its unit with `Environment=PYTHONPATH=<planning checkout>` — the portability rule 2026-10-21 landed for the tick and broker units did not reach the second writer. Move the worktree and every agent process dies at `import holon` with nothing in the journal naming the cause. Fixed by resolving the tree at start time from the launcher the unit already has | `test_the_runner_unit_survives_the_checkout_it_was_planned_from_moving`, `test_the_runner_units_start_command_resolves_a_real_launcher` |
| Skill red-team, 2026-10-21 | the distribution's skills all ran **one department, from inside it**, and nothing encoded how to manage the organization of systems — add, remove, refactor, expand, condense, heal, evolve were verbs with no home, so an agent able to do work could not change the system that does the work (measured: 8 of 10 add commands, 6 of 7 evolve commands and every heal command were named by no skill). `holon-operator` now teaches them, with each move's exact command and what it owes; BYLAWS §4's library list had drifted from the library itself and the new guard keeps the two in agreement | `test_bylaws_names_every_operation_skill_the_library_carries` |
| Readiness audit, 2026-10-22 | doctor's `artifacts.dangling` named a remedy that did not exist — "supersede the result" was a sentence, not a command, so the only answers to a rotted artifact pointer were restoring a /var/tmp scratchpad cleaned by 30-day tmpfiles or writing SQL by hand. `holon task supersede` now exists (chair): it marks the execute output withdrawn (migration 0018), leaves the work's own history untouched, and retires the artifact pins the result made in memory, through memory.py (one writer per database, law 3) — and doctor's warning skips what is already superseded, so it counts only results still standing behind their record | `test_supersede_marks_the_output_and_records_the_event`, `test_supersede_retires_the_memory_pins`, `test_doctor_stops_reporting_a_superseded_result`, `test_supersede_refuses_work_with_no_execute_result`, `test_supersede_is_chair_only` |
| Live corpus, 2026-10-22 | a pool the operator rewrote mid-session embedded `\"healthy\"` inside a SINGLE-quoted scalar, and the kernel's reader unescaped it — but YAML keeps backslashes literal in single-quoted and plain scalars, so the imported check command silently differed from the pool's text (`"healthy"` where the operator wrote `\"healthy\"`): a command that is not the one that was approved. The corpus comparison (every pool file, against a real YAML library) caught it the moment the file changed; the single-line scalar path and the plain-continuation path both gate on YAML's own rule now — escapes exist only inside double quotes | `test_an_escaped_quote_inside_a_double_quoted_scalar_reads_as_a_quote` (both the single- and the double-quoted case), `test_it_reads_every_pools_yaml_the_way_a_yaml_library_does` |
| Isolation red-team, 2026-10-23 | the isolated harness was promised "only the run's environment (never the kernel's)" and the runner merged its caller's dict over **its own whole environment**, so every name the runner inherited — credentials included — arrived inside the agent process, and the caller could override any of them. The runner branch was the *less* filtered of the two: the non-isolated branch drops `HOLON_CHAIR_KEY`/`HOLON_KEY` explicitly while the isolated one reintroduced the entire environment one layer down. The spawn now gets exactly the environment its caller asked for, and nothing else | `test_the_isolated_harness_gets_only_the_runs_environment` |
| Isolation red-team, 2026-10-23 | `reach()` granted a harness binary as the one file `shutil.which` returns, while the harness is a tree: measured on the live instance, a 505-byte launcher beside a 171 MB payload it execs after setting `LD_LIBRARY_PATH` to its own directory. The launcher was granted and the payload was not, so the agent user could not start the harness the instance dispatches — a boundary that closes is also a department that cannot work. The harness's own directory is now in the read reach | `test_the_harness_binary_lands_with_every_file_it_needs_to_run` |
| Isolation red-team, 2026-10-23 | an absent trust boundary was a doctor **warning**, and the heartbeat turns only `error` findings into incidents — so a live instance whose agents run as the kernel's own user (any one of them able to read the Chair key and write `work.db`) signaled nobody, unattended. The kernel already rated a promised-but-runnerless boundary an error, so an absent one is the same exposure a step wider. It is an error now, and `_isolation` no longer returns early on an unconfigured installation, which reported nothing about the boundary at all. The Chair's decision, with its measured cost, is recorded in `docs/design/2026-10-23-isolation-off-as-an-error.md` | `test_an_absent_boundary_is_an_error_not_a_warning`, `test_an_unconfigured_installation_says_something_about_the_boundary`, `test_a_doctor_error_becomes_one_incident_and_the_check_waits_its_interval` |
| Isolation red-team, 2026-10-23 | `holon isolation plan` — step 1 of BYLAWS §11's activation (`plan` → `sudo sh` → `apply --force` → broker → doctor) — refused with "set [isolation] agent_user first", so the plan that tells you which user to create was unreachable until you had already named one: the documented sequence was broken at its own first step, which is how the live instance's boundary stayed absent for a week. The plan now proposes `holon-agent-<instance>`, prints the exact `[isolation]` block to write into `holon.toml`, and the root script names the same user it creates — nothing downstream trusts the proposal: `apply`, the broker's allowed uids and doctor's probe all still check what the config says | `test_a_cold_start_can_reach_the_activation_plan` |
| Isolation red-team, 2026-10-23 | my own activation output named a step that cannot work: the plan told the Chair `systemctl --user enable --now holon-broker (install.py writes the unit)`, and `install.py` writes that unit **only when `agent_user` is already in holon.toml** — so a cold start following the plan would have been told to start a unit nothing had written, and install.py's `--systemd` was nowhere in the sequence that needs it. The third instance of this class in the ledger (a guard now walks every line the plan prints: a line naming `holon-broker` with `systemctl` and not `install.py` fails). Measured end-to-end on a fresh instance: with `agent_user` set, `install.py --systemd` does write the unit | `test_a_cold_start_can_reach_the_activation_plan` |
| Chair ruling, 2026-10-23 | an import proposed two routines whose workdir was a **legacy system's own repository** — the trading system's checkout — so the job's work, its playbook and its checks all lived inside a system the organization neither owns nor can reach, and "historical correlation" (`cron-jobs/*.md` happen to exist) read as a reason to run them. The Chair cancelled all four pending routines and ruled that proposals come from relevance, not correlation. `add_routine` — the ONE writer every path goes through (`routine import`, the venture import, a planner's decomposition, the Chair's own `routine add`) — now refuses a job whose own protocol declares a runtime this kernel does not serve (`runtime: vps-systemd`, `runtime: manual`), the survey predicts its own refusal rather than printing a clean line for a file the apply would drop, and the **workdir is not the gate**: an imported department's scope roots are the pool's own directories, so a pool outside the instance is the normal, correct case | `test_a_protocol_declaring_a_foreign_runtime_is_refused`, `test_a_protocol_declaring_no_runtime_is_not_fenced`, `test_the_kernels_own_runtimes_are_not_foreign`, `test_the_import_command_names_the_refusal_in_the_survey`, `test_the_venture_dry_run_names_it`, `test_the_venture_apply_skips_it_and_names_why`, `test_a_pool_outside_the_instance_is_still_a_valid_import`, `test_a_routine_written_by_hand_is_not_fenced` |

## Reading order for a new agent

1. `docs/INTENT.md`: what Holon is and the laws.
2. This file: the code map, the laws lint enforces, the regression ledger.
3. `docs/BYLAWS.md` (departments, roles, configuration) and `docs/CONTRACT.md` (the harness
   contract).
4. The README of the adapter you touch (`adapters/<name>/README.md`), which records what the
   real harness does.
5. Any audit in `docs/design/` whose work list touches what you are changing: its §1 defects are
   yours to fix (tests first, guard + ledger row); its §2 changes wait on a decision.
6. In the operator's overlay (not public): `docs/STATUS.md` and the dated `docs/design/` records — the
   deployment, the standing decisions and the design history.
7. `TODO.md` (the register: what is open, what was decided, evidence for each verdict) and
   `docs/decisions/` (AD-NNN records of the Chair's ratified direction changes).
