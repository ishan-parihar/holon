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
| **is the mechanism sound?** | `RedTeam`'s five adversarial probes | a real broker and a real runner, in-process, as the kernel starts them | all five held: the uid gate refused a foreign peer uid, the chair-only commands refused an anonymous caller, `_read` opened only what the client sent, the planted `result.json` symlink was refused, the run dir's ACL was exactly `rwx`/`r--`/`other::---`/sticky |
| **1a the runner leaks its environment** | `test_the_isolated_harness_gets_only_the_runs_environment` | `isolation.run()` → the real runner subprocess → the spawned shell | before: `sk-a-secret-the-runner-inherited` printed by the spawn. after: `unset`. Re-measured both states; the guard failed on the pre-fix tree and passed on the fixed one |
| **1b the harness cannot be started** | `test_the_harness_binary_lands_with_every_file_it_needs_to_run` + `isolation.reach()` on the live instance | the real harness tree on this host | the plan now grants the harness install's own directory (on the live instance that is
`~/.jcode/builds/versions/<version>`), which `ls` confirms holds both the launcher and the
payload it execs. Before, only the launcher was granted — the launcher's own text is
`LD_LIBRARY_PATH="$self_dir"; exec "$self_dir/jcode-linux-x86_64.bin"` |
| **1c an absent boundary reaches nobody** | `test_an_absent_boundary_is_an_error_not_a_warning` + the live doctor + the real tick | `holon doctor` on the live instance; `Kernel.tick()` on a real scratch instance | before: `WARN isolation.off · 0 errors, 1 warnings`, and no incident. Now: the same command prints `ERRO isolation.off · 1 errors, 0 warnings`, and the real tick created `Incident: doctor reports isolation.off`, status `open`, carrying the exposure text and the remedy |
| **1d the plan is reachable from a cold start** | `test_a_cold_start_can_reach_the_activation_plan` | `holon isolation plan` on the live instance (no `[isolation]` at all) | before: `IsolationError: set [isolation] agent_user first`. Now: it prints the four steps, proposes the user, and prints the `[isolation]` block to write |
| **1e the printed sequence is walkable** | the same guard's line walk + `install.py --systemd` on a fresh instance | `install.py --systemd --no-start` on a scratch instance with `agent_user` set | before: step 3 said `systemctl --user enable --now holon-broker (install.py writes the unit)` — and `install.py` writes that unit only when `agent_user` is already set, so nothing had written it. Now step 3 is `python3 install.py --systemd` and it is observed to write `~/.config/systemd/user/holon-broker.service` |
| **is it deployed?** | `holon doctor` + `id` + `systemctl --user list-unit-files` on the live instance | the live instance's own state | no `[isolation]` section, no agent user, no runner unit, no broker unit. **Not deployed**, and now an error the heartbeat reaches rather than a warning it drops |

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
