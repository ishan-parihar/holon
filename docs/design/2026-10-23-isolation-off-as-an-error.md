# `isolation.off` as an error, and the incident it opens every tick

Status: 2026-10-23. Live instance `/home/ishanp/holon`. Repo public `34c58d8`.
Measured, not asserted: every number below is from a command run against the live instance
or a fresh one.

~~An edit in the operator's working tree (uncommitted, left untouched) promotes `isolation.off`
from a warning to an error.~~ **Decided 2026-10-23: option (a) is implemented.** The severity
change is committed, the incident it opens is real, and the test that disagreed was updated to
expect a set — see §4 below for what that costs every fresh cold start.

This record was written while the edit was still in a working tree; it is kept as the analysis
that carried the decision, and its numbers were re-measured after the change rather than edited.

---

## 1. Why the edit is right about the mechanism

The edit's claim is that a warning reaches nobody. Measured: `holon/execution/service.py`
`_doctor` (lines 2456-2466) runs `holon doctor` on the heartbeat and opens an incident only for
each finding whose `severity == "error"`. A warning produces no incident, no digest line, and no
Chair notification.

The exposure is real and it is on now: `agents run as the kernel's user` means any dispatched
body with a shell can read the Chair key and write `work.db`. Measured on the live instance
today: that check was `WARN isolation.off`, and 17 live messages with 0 addressed to the Chair
was the 2026-10-22 finding on a sibling seam.

So the mechanism argument stands: a warning is the wrong severity for this exposure.

## 2. What the edit changes, measured

| | as deployed (HEAD `34c58d8`) | with the edit (uncommitted) |
|---|---|---|
| `holon doctor` on the live instance | 0 errors / 1 warning | **1 error / 0 warnings** |
| heartbeat | no incident | one incident, every tick, until isolation is applied |
| `tests/test_isolation.py:175` | asserted `("isolation.off", "error")` and passed | unchanged, still passes |
| `tests/test_loops.py:185` | passed | **fails**: the tick opens two incidents, `isolation.off` and `audit.chain` |

A note on how the first two columns were obtained, because the difference is easy to get wrong:
the live `holon doctor` is run from the checkout (`python3 -m holon --home …`), so it sees
uncommitted edits. The first column is from a detached worktree at HEAD
(`git worktree add --detach`), which carries the committed `add("warning", …)`; the second from
the same command run in the working tree, which carries the edit's `add("error", …)`.

**Superseded in the same session.** That closing sentence — "the deployed organization still runs
the warning" — was true when written and false an hour later: the severity change is now committed
and pushed, so the live instance reports **1 error / 0 warnings** and every tick opens the incident
until the boundary is applied. The columns are kept because they record how the measurement was
taken and why the two were confusable, not because the left one still describes the deployment.

The last row is the whole of the cost, and it is the row that decides this.

## 3. The disagreement is about identity, not about severity

`test_loops.DoctorOnTheHeartbeat.test_a_doctor_error_becomes_one_incident_and_the_check_waits_its_interval`
tampers with the audit trail on purpose and asserts the heartbeat opens exactly one incident,
named `doctor reports audit.chain`. It is a guard against the heartbeat opening incidents twice
for one condition, and against `doctor_every_min` being ignored.

With the edit, the fixture — a fresh instance with no `[isolation]` — *also* has `isolation.off`,
which is now an error, so the same tick produces two. The test then fails on its identity
assertion. That is not the test being wrong: it is the test doing its job, and the fixture
having two problems instead of the one it meant to introduce.

## 4. The decision the Chair owns

Three ways to resolve it, each with a cost:

**(a) Accept both incidents, and change the test to expect a set.** The heartbeat names every
failing check each tick, which is honest and matches what an operator wants to read. Cost: an
instance with isolation off and any other failing check raises two incidents per interval, and
`pending_for_chair` carries both.

**(b) Keep the edit, and make the fixture isolate itself.** Add `[isolation] agent_user` to the
`DoctorOnTheHeartbeat` pipeline so the only failing check is the one it tampers with. Cost: the
test no longer represents a default instance, so the *other* direction — a default instance whose
doctor produces errors — loses its one guard.

**(c) Severity is the wrong instrument.** A third state (`isolated`, `off`, `absent`) with the
absent case an error, and the off-when-configured case whatever it is today, is the precise
version of the edit's intent. Cost: a vocabulary change, which needs `vocab.py` and the
conformance gates, and is more than one commit.

The recommendation recorded here is **(a)**, because the exposure is the organization's own
databases and the heartbeat is the only thing that will say so unattended, and because a test
that expects a set of incidents is a test that fails when a *second* real problem appears — which
is what should happen. But it is the Chair's: it changes what the heartbeat does on every
instance that has not been isolated, which is every fresh cold start.

## 5. What is unchanged

The edit is not committed. `git diff` still shows it in the operator's tree and `HEAD` carries
the original `warning`. The MCP guard committed beside it (`34c58d8`) is unrelated and
independent: the Chair control surface publishes no gated command to an employee session, which
holds whether or not this severity changes.
