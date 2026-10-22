# Holon: the heartbeat foundation, the channel, and the plan landed

Repo: public `bf4f965` (overlay synced, not pushed) · 598 tests · lint 0 errors / 0 warnings

---

## Part 1 — What was broken and what fixed it

### 1.1 The notification seam (the one that made everything else moot)

`notify()` shells `[notify] command` and treats the **exit code as the proof of delivery**. Your
command printed `no [chair] channels configured — the message has nobody to reach` and
**exited 0** — so the kernel advanced its `notify_seq` cursor, recorded `notify: sent`, and
**never retried the digest**. Every lane breach and incident the heartbeat has ever found was
marked delivered; the Chair heard nothing, and nothing in the journal said the message went
nowhere. It had **zero tests**.

Measured, side by side:

```
exit 0 + dropped         top=19  cursor=19  sent=True    next tick: 0 owed
exit 1 = honest failure  top=32  cursor=19  sent=False   next tick: 1 owed
```

**Fixed** with `[notify] dropped_marker` (BYLAWS §6): the instance names the text its command
prints when it delivered nothing, and a matching output is a **failed send** — the item stays
owed and the event says `failed`.

### 1.2 The heartbeat does not survive a reboot

`systemctl --user is-enabled holon-tick.timer` → **`disabled`**. The installer writes the units
correctly (`systemctl --user enable` creates the `timers.target.wants` symlink, which is the only
reason a user unit starts after a reboot) — the live instance's symlink was never created, so the
heartbeat dies with the login session and nothing records why. Not a code defect; **enabling it
is your action**. The contract now has a guard: `WantedBy=timers.target`, `Persistent=true`,
`OnBootSec=2min`, `KillMode=process` on the oneshot (so a spawned dispatch outlives its tick).

### 1.3 The tick itself — sound, now guarded

Eight `HeartbeatFoundation` guards across every phase (salvage, lease sweep, triggers, notify,
due schedules, held checks, backup, doctor), plus the systemd validity and reboot contract.
Mutation-verified throughout.

## Part 2 — An employee can now reach you: the Chair channel, landed

The plan's prior was a finding: **an employee could not reach the Chair at all.**

- `pending_for_chair` read `events JOIN tasks` and nothing else — **`msg send` and `msg request`
  landed in `messages` and the digest never saw them.** Measured: 17 live messages, **0** addressed
  to the Chair, `entity='message'` appears **0** times in events.
- **No reply direction.** `holon msg` was `send`, `inbox`, `request`.
- **`thread_id` was a dead column.** `_message` writes `thread_id or mid`, so every message opened
  its own thread; **no caller in the kernel ever passed one.** A conversation was not representable.

(I claimed earlier that the Chair's reply "was appended to the same thread". I measured after
asserting, found it false, and the record says so.)

**Landed, all three:**

| | What it does | Guard |
|---|---|---|
| **W1** | Unacked `question`/`request` messages to the Chair fold into the digest; acked when the digest is actually delivered | `test_an_employees_question_to_the_chair_reaches_the_digest` |
| **W2** | `msg reply` answers in the thread it answers, goes back to **whoever asked**, acks what it answered; `msg thread` reads the conversation. **No schema change** | `test_a_reply_joins_the_thread_it_answers` |
| **W3** | Attribution asserted, not assumed: the sender is the kernel's resolution of the authenticated principal, never the body's claim | `test_a_reply_goes_back_to_whoever_asked` |

Two more defects surfaced from a real instance:

- `msg send` **could not name a kind** — it only ever posted `dm`, the one kind the digest does
  not chase, so the whole employee→Chair path was unreachable from the CLI. `--kind` now names any
  of the six.
- `--as chair` from the Chair produced `emp:chair`, a sender no employee key resolves to. It is
  now refused.

**Measured end to end on a real instance:**

```
emp:<id>  ── question ──▶  chair      [acked: owed → answered]
chair     ── answer  ──▶  emp:<id>    [same thread_id]
```

## Part 3 — Why the two facets were not built as proposed

**A Telegram thread per agent** builds a *second identity* beside the one the kernel binds to the
run token, and the regression ledger is a catalogue of exactly that failure. It survives as a
**rendering** from `messages.sender` — client-side, in your adapter. **Direct Gmail/Telegram
tooling with scoped authorization** builds a *second authorization* beside the broker's, which is
the right instinct one layer too high. Scope belongs to the registry's actor class, which is
already the law and already derived into the MCP fence.

**One channel, owned by the kernel, rendered by the adapter.** The kernel decides *who* spoke and
*what* is owed; the adapter decides *how* it looks.

## Part 4 — The gates

| Gate | Status |
|---|---|
| 0 · `isolation.off` cleared | **open — yours** |
| 1 · The digest proves delivery | closed |
| 2 · The units carry the reboot contract | closed |
| 3 · The heartbeat actually enabled | **open — yours** (`systemctl --user enable --now holon-tick.timer`) |
| 4–6 · W1, W2, W3 | closed |

**The channel is landed and green, but not safe to run on the live instance while gate 0 is open:**
with agents running as you, it is a second path to the Chair's authority. The three Chair actions
are `isolation plan` / `apply`, the timer enablement, and adding `dropped_marker` to `[notify]`. I
have taken none of them.

## Correction

Four full-suite failures appeared during verification that were **not mine**: the heartbeat's
liveness stamp (landed after my previous push) carried two comments naming a harness in the
kernel, which is law 2, so `holon lint --strict` failed and the distribution could not pass its
own rubric. The comments now name the mechanism rather than a product, with a ledger row.
