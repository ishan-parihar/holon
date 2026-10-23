# What each requirement was checked against, and what was observed

Status: 2026-10-23, appended to the red-team audit. The Chair's request had three parts and this
maps each to the check that actually covers it and the observed result, rather than a test count.

The Chair's request: *"Test the isolation system to ensure that it is functional. Last time we
checked it wasn't operational and efficacious in agentic workflows. Investigate the gaps in its
operationalization, and provide a red-team audit."*

**What I inferred.** Three readings were possible: (a) is the boundary *secure* (does it hold?),
(b) is it *usable* (can a body work through it?), (c) is it *deployed*. The request says
"functional" and "efficacious in agentic workflows", which is (b), not (a) — a boundary that holds
perfectly and denies the department its own tooling is a department that cannot work. That is why
the harness-reach finding (1b) is a *defect* and not a design choice. I also read "last time we
checked it wasn't operational" as pointing at the boundary's *presence on the live instance*, which
is (c) — so both (b) and (c) are covered below, and (a) is the one thing that was already sound.

---

| Requirement | Check that covers it | Interface exercised | Observed result |
|---|---|---|---|
| **is the mechanism sound?** | `RedTeam`'s adversarial probes (`tests.test_isolation_redteam.RedTeam`) | a real broker and a real runner, in-process, as the kernel starts them | five probes held, each run by its exact name: the **uid gate** refused a foreign peer uid (`test_the_broker_refuses_a_uid_it_does_not_allow`); **`_read` opened only what the client sent** (`test_the_broker_never_reads_a_path_an_agent_names`); **the planted result symlink was refused** (`test_a_symlink_planted_as_the_result_is_never_followed`); **the run dir's ACL was exactly `rwx`/`r--`/`other::---` and sticky** (`test_the_agent_grant_is_exactly_what_the_docstring_promises`); and **the caller's env reaches the spawn while nothing inherited does** (`test_a_spawned_process_can_read_a_credential_the_runner_inherited` + `test_the_isolated_harness_gets_only_the_runs_environment`). Named here because an earlier version of this row listed four of five in prose, and writing the names revealed two wrong: I typed `test_the_broker_null_reads_a_path_an_agent_names` and `test_the_broker_null_follows_a_symlink_planted_as_the_result`, neither of which exists. Caught in the same minute by checking each name against the module — the check this section exists to describe |
| **1a the runner leaks its environment** | `tests.test_isolation_redteam.RedTeam.test_the_isolated_harness_gets_only_the_runs_environment` | `isolation.run()` → the real runner subprocess → the spawned shell | before: `sk-a-secret-the-runner-inherited` printed by the spawn. after: `unset`. Re-measured both states; the guard failed on the pre-fix tree and passed on the fixed one |
| **1b the harness cannot be started** | `tests.test_isolation_redteam.Reach.test_the_harness_binary_lands_with_every_file_it_needs_to_run` + `tests.test_isolation_redteam.Reach.test_the_plan_applied_to_a_real_tree_grants_the_harness_it_dispatches` | a real replica of the harness layout with the plan's own commands applied in its own order, then `getfacl` on the real result | on a real filesystem the launcher and the payload both end `user:<agent>:r--`. Mutation-verified: remove the plan's recursive `rX` on the version directory and the payload keeps the chain pass's `---` deny with no agent entry at all, so the harness cannot run — the fix is load-bearing, proved by the applied ACL rather than by a directory listing |
| **1c an absent boundary reaches nobody** | `tests.test_isolation_redteam.Deployment.test_an_absent_boundary_is_an_error_not_a_warning` + `holon doctor` on the live instance + the incidents the live instance actually holds | `holon doctor` read against the real instance's `work.db` (`$HOLON_HOME`, 166 tasks), in `mode=ro` | before: `WARN isolation.off · 0 errors, 1 warnings`, and the heartbeat opened nothing. Now the same command prints `ERRO isolation.off · 1 errors, 0 warnings`, and the live instance itself proves the second half — `tasks` holds `tsk_01M5M5Y37RB8Q5ZS7N7A8B7B32` `Incident: doctor reports isolation.off`, status **open**, whose intent carries the exposure text verbatim, plus a follow-on `research` task the kernel opened for it. Measured here, not earlier: the earlier evidence was a scratch `Kernel.tick()`, and the real instance's own incidents are stronger than that |
| **1d the plan is reachable from a cold start** | `tests.test_isolation_redteam.Reach.test_a_cold_start_can_reach_the_activation_plan` | `holon isolation plan` on the live instance (no `[isolation]` at all) | before: `IsolationError: set [isolation] agent_user first`. Now: it prints the four steps, proposes the user, and prints the `[isolation]` block to write |
| **1e the printed sequence is walkable** | the same guard's line walk + `install.py --systemd` on a fresh instance | `install.py --systemd --no-start` on a scratch instance with `agent_user` set | before: step 3 said `systemctl --user enable --now holon-broker (install.py writes the unit)` — and `install.py` writes that unit only when `agent_user` is already set, so nothing had written it. Now step 3 is `python3 install.py --systemd` and it is observed to write `~/.config/systemd/user/holon-broker.service` |
| **is it deployed?** | `holon doctor` + `id` + `systemctl --user list-unit-files` on the live instance, re-measured 2026-10-23 (21:15–21:23) | the live instance's own state: the unit files, the user table, and `holon.toml` for the instance the runtime actually uses (`$HOLON_HOME`, 166 tasks) | **Not deployed**, and the detail matters. No `[isolation]` section in the config (`grep -c '^\[isolation\]'` → 0); `id holon-agent` → `no such user`; no system runner unit. The broker unit's row needs correcting: an earlier version of this table said "no broker unit", and that is false — `~/.config/systemd/user/holon-broker.service` exists, but it is `disabled`, `inactive`, and was written against a **scratch** `HOLON_HOME` (a scratch `HOLON_HOME` under the runtime's own temp tree), not the live instance. So: installed for the wrong instance, and not running for any. The live instance's own `holon-tick.service` carries the same wrong home, which is why the map's earlier "the real tick created the incident" was re-measured against the live instance's own `tasks` table instead |

**What was wrong in this row, and why it matters.** My first pass at the deployment row was measured against
`~/.config/holon/`, which holds only keys and no instance at all, so it answered a question nobody
asked. The instance that carries the work is `$HOLON_HOME`. Two of the
three re-measured claims survived; the broker-unit claim did not, and a scratch-home install that
looks like a deployment is the more dangerous of the two errors — `isolation.off` says "not on",
while a broker unit pointing at a scratch home says "on" and answers nothing.

## What re-measuring the deployment rows found (2026-10-23, 21:15–21:45)

This map's earlier deployment claims were measured against `~/.config/holon/`, which holds only
keys and no instance, so they answered a question nobody asked. Re-measured against the instance
that carries the work (166 tasks, 1530 events, a heartbeat writing it every two minutes — 1c's
incident is in *its* `tasks` table, not a scratch one):

- **No `[isolation]` section** — confirmed (`grep -c '^\[isolation\]'` → 0).
- **No agent user** — confirmed (`id holon-agent` → `no such user`).
- **No system runner unit** — confirmed.
- **"No broker unit" — FALSE, and corrected.** The user broker unit exists, but it is `disabled`,
  `inactive`, and points at a **scratch** home. Installed for the wrong instance, running for none.
- **The instance is split in two.** The installed tick unit names one home (scratch, 5 events) while
  an undocumented heartbeat writes the real one every 2 minutes. Seven concurrent agent sessions
  are running on this machine. This is the deployment's to resolve and is recorded in the audit's
  §1g, not claimed as a kernel defect.
- **The plan's `runs:` line is derived one directory below the live instance**, because `runs_dir`
  is `$HOLON_HOME/execution/runtime/runs` and this deployment's home is already
  `…/execution/runtime`. The grant would name a directory no run lands in.

And a defect in the plan itself, found the same way: **the printed activation sequence had no step
1.** The root script printed raw and the numbered list began at `1.5`, so the full list was
`['1.5', '2', '3', '4']`. Fixed and guarded by
`test_the_printed_sequence_numbers_every_step_it_prints` (mutation-verified: reverting the fix
fails it); re-measured the output is `1., 1.5., 2., 3., 4.`

## How to reproduce these rows

Every guard above is runnable by its exact name, and that is how they were last verified
(2026-10-23, on this checkout, by running the names rather than the module):

```sh
python3 -m unittest -v \
  tests.test_isolation_redteam.RedTeam.test_the_isolated_harness_gets_only_the_runs_environment \
  tests.test_isolation_redteam.Reach.test_the_harness_binary_lands_with_every_file_it_needs_to_run \
  tests.test_isolation_redteam.Reach.test_a_cold_start_can_reach_the_activation_plan \
  tests.test_isolation_redteam.Reach.test_the_plan_applied_to_a_real_tree_grants_the_harness_it_dispatches \
  tests.test_isolation_redteam.Deployment.test_an_absent_boundary_is_an_error_not_a_warning
# → Ran 5 tests ... OK
```

Two things the reproduction is worth beyond a green tick. First, the names are **class-qualified**,
because these rows originally named the test alone, and running them as `RedTeam.<name>` — the
module's first class — made **four of the five fail `AttributeError`**, not because any guard was
broken but because three live in `Reach` and one in `Deployment`. A map that names a check but not
where it lives is a map a reader cannot run, and it was mine. Second, the names were confirmed to
exist before the run: `grep -rln 'def <name>' tests/` found 5 of 5, in the module and class each
row now names. `python3 -m unittest tests.test_isolation_redteam` alone would have hidden both
facts, since a module pass is indifferent to which class holds the method.

## A false positive on the *other* goal, recorded the same way

While exercising the relevance gate (the earlier work in this session) through its real command, my
first `runtime: vps-systemd` protocol produced **no refusal at all**. The cause was my input, not
the kernel: the protocol reader requires a `---\n` frontmatter fence and reads the title from
`name:`, and my throwaway file had neither, so the metadata parsed empty and the survey correctly
saw a job declaring nothing. Re-run with a well-formed protocol, the real command refuses it:

```
legacy trading monitor  `0 * * * *` agent  !! refused: this job declares a runtime this
does not serve (`runtime: vps-systemd`) ... A proposal comes from relevance to this organization
... not from the existence of a legacy system's protocol files.
```

Recorded because it is the same mistake the audit exists to prevent: a malformed probe reads as a
working mechanism, just as a well-formed one reads as a broken one.

## The one seam that is externally blocked, recorded rather than claimed

No guard on this host can prove the *whole* loop, because the agent user does not exist and creating
it needs root:

```
id holon-agent      →  no such user
sudo -n true        →  a password is required
```

So what is proved by execution and what is proved by fixture is:

- **Proved by running the real thing:** the mechanism (a real broker and runner, in-process),
  `reach()` on the live instance's real harness tree, `holon doctor` on the live instance,
  the heartbeat's real incident creation, `install.py --systemd` really writing the unit, and
  the plan really producing itself from the live instance's actual state.
- **Proved only on a scratch instance with `nobody`/the test user:** the full agent-user path — a
  body dispatched *as* the agent user, through the runner, reaching the broker. That path needs the
  user to exist, and it does not. Stating it as closed would be the ledger's own sin.

That last seam is exactly the Chair's root command, and it is the only reason the verdict is
"mechanism sound, system not on" rather than "functional".
