# Holon: the heartbeat, `holon-agent`, and your two-faceted channel

Status: measured, fixed where the fix is the kernel's, offered where it is yours
Repo: `df9c6e6` (public), `7fae8ea` (overlay) · 558 tests · lint 0/0

---

## 1. What was broken

Three things measured. Two were broken.

| # | Measured | Verdict |
|---|---|---|
| a | The Chair's notification path | **broken, silently** |
| b | The timer's reboot contract | **broken on this machine** |
| c | The tick's internal phases | sound, now mutation-verified |

### (a) The Chair never hears anything

`notify()` shells `[notify] command` and reads the **exit code as the proof of
delivery**. The live instance's command is:

```
operant org holon notify --reason heartbeat --text {message}
```

Running it produces:

```
no [chair] channels configured — the message has nobody to reach
```

…and it **exits 0**, because it treats "nothing is configured" as a successful
no-op. The kernel therefore does this, in its own transaction:

```
top=19  notify_seq_after=19  sent=True
       next tick's notify() would report: 0 owed items
       event: sent / 1 items
```

The cursor advanced. The event says `sent`. The digest is **never retried**, and
nothing in the journal says the message went nowhere. Compare the honest failure:

```
--- exit 1 = honest failure
    top_seq=32  notify_seq_after=19  sent=False
       next tick's notify() would report: 1 owed items
       event: failed / 1 items
```

This is the only path from the organization to the Chair, and it had **zero
tests** (nothing referenced `notify_seq`). Every lane breach, every incident,
every stage nobody can claim — all recorded as delivered.

**Fix:** `[notify] dropped_marker` (documented in BYLAWS §6). A matching output
is a failed send; the item stays owed and the event says `failed`. Four guards
now cover it (honest send, silent drop, honest failure, quiet instance),
mutation-verified.

### (b) The heartbeat dies on reboot

```
systemctl --user is-enabled holon-tick.timer  →  disabled
```

The installer does the right thing — `systemctl --user enable` writes the
`timers.target.wants` symlink. The live instance's symlink was never created,
so the timer does not start after a reboot, and it does not outlive the login
session either. This is not a code defect, so it was not fixed unilaterally:
**enabling the heartbeat is the Chair's action.**

The regression guard now proves the contract: `WantedBy=timers.target`,
`Persistent=true`, `OnBootSec=2min`, `KillMode=process` on the oneshot (so a
spawned dispatch outlives its tick), and that `systemd-analyze verify --user`
accepts every unit the installer writes.

### (c) The tick itself

Sound. Eight `HeartbeatFoundation` guards extended across every phase
(due-fire-once, failure-streak → one incident, streak recovery, idempotency,
disabled schedule, cancelled template, lease sweep without loss, backup
reported), all green and mutation-verified. A missing guard here is not a small
bug: it is "the organization stops running, and nothing says why."

---

## 2. The heartbeat and `holon-agent`, in plain terms

### The heartbeat = one command, run every 2 minutes

`holon tick` is the kernel's only clock. Nothing else can notice that something
is late. It runs, in order:

1. **`_salvage`** — pick up work left on disk by a dead dispatcher
2. **`sweep_leases`** — recover runs whose lease expired; a dead executor's
   stage goes to `failed`, then `_retry_or_fail` re-queues it
3. **`triggers`** — fire event and rate triggers
4. **`notify`** — the digest to the Chair (§1a)
5. **due schedules** — the recurring plans
6. **`held_checks`** — the class checks that wait their interval
7. **`_backup`** — the daily instance backup
8. **`_doctor`** every 30 min — a failing check opens an incident

### `holon-agent`

**There is no `holon-agent` binary.** Checked `$PATH`, the repo, and the
installer. What exists is `[isolation] agent_user` — the **Linux user** the
agents run as. Measured with `isolation.reach()` against the live config:

```
reads:   <projects>/holon   (the checkout)
writes:  <projects>/pioneer-qf  (+ core, data, logs)
runs:    <instance>/execution/runtime/runs/
```

So `holon-agent` is **not a program**. It is a **username** with three lists:
what it can read, what it can write, and where its run directories live.

Two consequences that matter for the channel question:

- **Isolation is off right now.** `holon doctor` reports `isolation.off`, and
  `holon isolation plan` refuses to run without `[isolation] agent_user`. Until
  it is applied, **agents run as the operator**, with the operator's filesystem
  permissions. That is the single largest standing gap in A.
- The **Chair keys** live in `~/.config/holon/keys/` with recursive ACL
  **denies** across the home dir. That is what stops an agent from reading its
  way to the Chair's authority.

---

## 3. The two-faceted channel — assessment

Answered against what exists, because most of the second facet already exists
and is already scoped.

### What already exists

| Surface | Status |
|---|---|
| `[notify] command` | kernel → one digest, Chair only. Just fixed. |
| `_deliver` | the internal fan-out behind it |
| `msg send / inbox / request` | employee ↔ Chair messages |
| `[chair] channels` | `<platform>:<user id>` refs whose channel messages are the Chair's |

`MESSAGE_KINDS` carries `dm, channel, request, question, answer, proposal`.

### Facet 1 — "a Telegram thread per agent" — recommend against

The topology already carries `channel` for broadcast and `dm` for a named
party. A thread *per agent* is a fan-out the model does not hold: N unbounded
surfaces, each needing `[chair] channels` entries, each a place a stray message
could land. `holon/mcp.py` derives its fence from actor class — guarded by
`test_the_allowlist_is_derived_from_the_actor_class_not_hand_listed` — precisely
because hand-maintained lists drift. N threads is that same defect with a
management cost.

Measured: `[chair] channels` is currently `[]`, and the digest already breaks at
exactly the hand-off point (`no [chair] channels configured`). Fixing the seam
that exists beats adding a second one.

### Facet 2 — "direct Gmail/Telegram tooling with scoped authorization" — right instinct, wrong layer

This is already the trust boundary. An employee already: runs as `holon-agent`
(not the operator), gets scoped read/write from `[scope]`, and is forbidden every
Chair command by the broker. Adding tooling "with scoped authorization" builds a
**second** authorization system beside the one the kernel enforces, and the
regression ledger is a catalogue of that failure mode:

- `test_the_chair_key_never_reaches_a_harness_env` — the Chair key reached a
  harness environment
- `test_a_shared_tree_others_commit_to_or_write_ignored_paths_in_is_not_a_breach`
  — a shared tree tripped the read-only guard
- `test_an_outside_reach_import_starts_disabled_with_the_decision_model_down` —
  one bulk approval lit the outreach fleet

Each is "a second channel with its own authorization."

### The recommendation

**One channel, authorized by the kernel, with per-employee attribution.** A
per-employee thread is a *rendering* of `dm` to a named employee — a client-side
concern in the adapter, not a kernel topology change. The kernel decides **who**
is speaking (the run token, which binds a session to a run); the adapter decides
**how** it renders.

**Two gates before any of this runs:**

1. **Turn on isolation** and set `[isolation] agent_user`. Without it every
   agent operates as the operator, and a notification channel becomes a direct
   pipe to the Chair's authority.
2. **Add `dropped_marker` to the live config** and prove the digest arrives,
   before adding a surface on top of it.

---

## What is the Chair's to decide

| Action | Risk |
|---|---|
| `systemctl --user enable --now holon-tick.timer` | begins dispatching work |
| add `dropped_marker = "no [chair] channels configured"` to `[notify]` | surfaces the backlog that was owed |
| run `holon isolation plan` / `apply` with an `agent_user` | the real trust boundary |

All three are Chair actions. None was taken unilaterally.
