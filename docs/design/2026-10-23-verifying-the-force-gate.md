# Verifying the `--force` gate, and the third place it was needed

Status: 2026-10-23. Live instance `/home/ishanp/holon`. Repo public `ad16fb1`.
No state changed in this session; every conclusion below is from a mutation or a real command.

The `--force` gate on `isolation apply` (`7518afa`) was validated by reading its own consumer, and
that validation found a regression I had introduced: the plan's printed instruction named the apply
bare, so following the kernel's own words raised ValueError. That was fixed (`97afcec`) with a guard
that reads the real printed stdout. This record is the second round of that validation. It asked four
questions and found that the fix was still incomplete.

---

## 1. Question one: was the "five stale docs" claim true?

The claim I made was that five places said `holon isolation apply` bare and all five were fixed.
Measured, that was wrong twice, in opposite directions:

- **One file I had already fixed and forgotten.** `docs/COMMANDS.md` carries `--force` — but not
  because of my commit: the operator regenerated it in `36ef8ef`, which is where the registry's new
  flag reached the generated table. My note said "no regeneration needed," which would have left the
  operator to discover the drift.
- **One file that still drifted, and was the one that mattered.** `holon/integrity/doctor.py` tells
  the operator which command repairs the boundary. Both of its `isolation.reach` remediation strings
  said `(holon isolation apply)` bare. My fix had touched the plan's printed output and two design
  records — a `docs/` sweep — and never grepped the code path that consumes the gate.

Neither `docs/design/2026-10-22-the-readiness-fixes.md:43` nor `docs/BYLAWS.md` is a command to paste;
they are prose about the step, so they were right to leave alone. The distinction that mattered was
**instruction versus mention**, and the first pass had not drawn it.

## 2. The defect, and why it was latent rather than live

Both strings sit inside `doctor._isolation`'s reach loops. Those loops are unreachable today:
`[isolation]` is still unset on the live instance, so the function returns at the `isolation.off`
warning and never reaches them. Verified: the live doctor reports `0` isolation.reach lines.

So the failure is scheduled, not present. The moment the Chair's root isolation step lands and one
`setfacl` lands wrong, doctor prints the repair — and the repair returns
`ValueError: ... pass --force`. It is the same failure mode the plan's instruction had, in the
surface that fires *after* the surgery rather than before it.

A grep for it would have found it in the first pass. The pass over the *code* that names the apply was never run, because the first pass swept `docs/` and believed that covered the callers.

## 3. The guard, and what makes it more than its own string twice

`test_no_remediation_names_a_command_the_gate_would_refuse` reads `doctor.py`'s source, takes each
line naming the apply command, and asserts that line carries the flag the registry requires. Nothing
in the test restates a string of mine.

That claim was tested by mutation, three ways:

| Mutation | Result |
|---|---|
| one remediation back to bare | `AssertionError`, names the line |
| `isolation` gate made inert (`if False and not args.force`) | `test_isolate_apply_refuses_without_force` fails; the new guard stays green by design |
| both remediations carry the flag | `OK` |

The middle case is the one that matters. If my guard had been written as "the command I wrote is the
command in the registry," then making the gate inert would have been harmless and both tests would
have passed quietly. It does not: the gate has its own guard, and the words about the gate have this
one. Neither is a restatement of the other.

## 4. The runbook — the defect that would have actually cost a step

`/var/tmp/ishanp-agentic/isolation-one-root-step.md` is the handoff the Chair follows for the root
isolation step. Its step 2 read:

```sh
python3 -m holon --home /home/ishanp/holon isolation apply       # 83 setfacl commands
```

Bare. Following it returns ValueError, one command into the Chair's only remaining manual step,
immediately after the root part succeeded and the config key was re-added. The root pass would have
landed and the ACL pass would not have, with an error that says nothing about *why* the command is
refused beyond its own flag.

Fixed in place, and the fixed line was checked against the registry rather than eyeballed: the
registry's arg list for `isolation apply` is `['--force']`, and the runbook now carries it.

The runbook is hand-written and nowhere in the repo generates it. I checked the same sweep across
the projects tree, the overlay and the scratch dir for a *command* as opposed to a mention; the other
hits (`pretrial-plan.md:33`, the runbook's own line 87 explaining the setfacl failure) are prose, so
they stay as they are. The overlay's personal copies of the two design records will take `main`'s
version on the next `sync-public.sh`, which is the rule for them.

## 5. What the four questions answered

| Question | Verdict |
|---|---|
| Was the "five stale docs" claim true? | No. One was already fixed by the operator's regeneration, and one was missed in the code — so the count was neither five nor complete |
| Are there consumers outside the repo? | Yes, and the runbook was the one that would have cost a step. `grep`-patchable, so the general case is covered |
| Does the new guard merely assert my own string twice? | No, proved by mutating the gate itself and watching the complementary guard fail |
| Does the full suite pass? | 622 tests, `OK`; `lint --strict` 0/0 |

## 6. What is unchanged, and the two actions that are still the Chair's

Nothing in `$HOLON_HOME` changed. The live instance still reports 0 errors and 1 warning
(`isolation.off`), the digest still retries every minute against the new token, and `isolation apply`
still refuses bare — which is precisely why the runbook line would have failed.

The two Chair actions remain the Chair's for the reasons they were: sending `/start` to the bot
(Telegram forbids bots initiating DMs, so nothing on my side can complete it) and the root isolation
step (it needs `sudo`).
