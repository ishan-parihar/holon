# What remains yours, and why each one matters

Status: measured 2026-10-22T19:55Z against the live instance (`$HOLON_HOME`), read-only.
Repo: public `e569251` · 598 tests · lint 0 errors / 0 warnings.

Three actions are yours. None was taken. Each is listed with what it is, what it changes, what it
costs if it stays open, and the exact command.

---

## 1. Turn isolation on — `holon isolation plan`, then `apply`

### What it is

Isolation is the trust boundary: **employees run as their own Unix user instead of you.** There is
no `holon-agent` binary to install — `holon-agent` is a *username* the kernel grants three lists
to. I measured what those lists would be from your live config:

```
reads:   $HOLON_HOME (the checkout)
writes:  the B-side venture pool (its core, data and logs roots)
runs:    <instance>/execution/runtime/runs/
```

plus recursive ACL **denies** across your home directory for the Chair keys in
`~/.config/holon/keys/`.

### Measured state today

`holon doctor` reports:

```
WARN isolation.off   agents run as the kernel's user: one with a shell can read the Chair key
                     and write work.db (set [isolation] agent_user; `holon isolation plan`)
```

`[isolation]` is absent from `holon.toml`, and `id holon-agent` returns **"no such user"**. So
nothing is half-done — it has never been applied here.

### Why it matters

This is the single item that makes the other two safe. **While it is open, every agent operates as
you**, which means:

- **the Chair key is readable by any agent's shell** — doctor states it plainly, and the whole
  authority model descends from that key;
- `work.db` is writable, so a body could alter the work graph directly rather than through the
  contract;
- **the channel I just landed is, until this closes, a second path to your authority** — an
  employee you cannot distinguish from yourself.

It is also the item your own broker already assumes: the broker refuses the Chair key to harnesses
(`test_the_chair_key_never_reaches_a_harness_env`) and resolves the run token, but it can only do
that if agents are *not* running as the kernel's user in the first place.

### The command — two steps, and the order matters

`isolation plan` refuses to run until `[isolation] agent_user` names the user, so the config edit
comes first. It is yours to choose; `holon-agent` is the conventional name and the one the doctor's
warning suggests.

**Step one — name the user** in `$HOLON_HOME/holon.toml`:

```toml
[isolation]
agent_user = "holon-agent"
```

**Step two — read the plan, then apply it.** `plan` prints exactly what `apply` will do and
changes nothing:

```sh
holon --home $HOLON_HOME isolation plan       # the user, the grants, the ACL denies — read-only
holon --home $HOLON_HOME isolation apply      # creates holon-agent, applies the ACLs, moves the adapter homes
```

Afterwards the doctor's `isolation.off` and `isolation.runner` checks both pass, and agents run as
`holon-agent` with the three lists above and nothing else. This is the one action of the three
that changes *who is running*, so read the plan before applying it.

---

## 2. Enable the heartbeat — `systemctl --user enable --now holon-tick.timer`

### What it is

`holon tick` is the kernel's **only clock**. Nothing else can notice that something is late. It
runs, in order: salvage → lease sweep → triggers → **the Chair's digest** → due schedules → held
checks → backup → doctor.

### Measured state today

```
systemctl --user is-enabled holon-tick.timer  →  disabled
systemctl --user is-active   holon-tick.timer  →  inactive
systemctl --user list-timers holon-tick.timer →  0 timers listed
```

The heartbeat **is not running**, and it will not start after a reboot. The installer is correct —
`systemctl --user enable` writes the `timers.target.wants` symlink, which is the only reason a user
unit starts after a reboot — but this instance's symlink was never created. Nothing recorded why,
which is exactly why the reboot contract now has a guard.

### Why it matters

Every guarantee in this session's work is conditional on a running heartbeat:

- the lease sweep that recovers a dead executor's work (your `holon-agent`'s runs otherwise block
  until a lease is manually recovered);
- **the Chair's digest** — the one path from the organization to you;
- `doctor`, which is what tells you the next bad thing, and `backup`, which is what protects the
  only copy of the organization's state.

The cost of leaving it open is not "the heartbeat is a bit late" — it is **the instance stops
running the moment nobody is logged in, and nothing says so**.

### The command

```sh
systemctl --user enable --now holon-tick.timer
systemctl --user list-timers holon-tick.timer        # expect NEXT filling in every 2 minutes
```

---

## 3. Make the digest prove delivery — add `dropped_marker` to `[notify]`

### What it is

`[notify] command` is the entire contract between the kernel and whatever delivers your digest: the
kernel computes the message, substitutes `{message}`, and reads the **exit code** as the proof.

### Measured state today

```
[notify]
command = "operant org holon notify --reason heartbeat --text {message}"
        # dropped_marker: absent
        # [chair] channels: none
```

And running that command produces:

```
no [chair] channels configured — the message has nobody to reach
```

**…and it exits 0**, because it treats "nothing is configured" as a successful no-op. So the kernel
advances its `notify_seq` cursor past the owed items and records `notify: sent` — the digest is
marked delivered, never retried, and the Chair never hears about a lane breach, an incident, or a
stage nobody can claim. **You have now seen this session's measurement; this line is what closes
it.**

### Why it matters more than the others

It is the smallest edit with the largest consequence, because **it is silently wrong today.** The
two items above announce themselves when they break (`isolation.off`, `disabled`). This one does
not — `notify: sent` is written whether the message reached you or not. Ask for anything in the
organization to reach you — the channel I landed, an incident, a stage nobody can claim — and
without this line it lands in a table nobody reads.

It is also why **item 2 alone is not enough**: enabling the timer will start the heartbeat firing
the digest every 2 minutes into a command that reports success having delivered nothing, and every
one of them will advance the cursor.

### The command

Add to `[notify]` in `$HOLON_HOME/holon.toml`:

```toml
[notify]
command = "operant org holon notify --reason heartbeat --text {message}"
dropped_marker = "no [chair] channels configured"
```

Then `holon msg reply <id>` answers a question in the thread it answers — the whole path the plan
landed.

---

## How the three relate

**3 first, then 2** — the digest has to *prove* delivery before it runs every two minutes. **1
before either is meaningful for autonomy**, because it decides whose identity a message or a run
carries.

| | If you do nothing |
|---|---|
| 1 | every agent runs as you; the Chair key is reachable from any agent's shell |
| 2 | the organization stops when your login session ends, and nothing says so |
| 3 | nothing the organization finds ever reaches you, and every one of them is recorded as sent |

None of the three was taken. The overlay is synced and gated green, so all of it is safe to do
now.
