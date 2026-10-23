# The heartbeat, the channel, and the live activation — measured

Status: 2026-10-22T20:46Z. Live instance `/home/ishanp/holon`. Repo public `563bb2c` · 598 tests ·
lint 0/0. Backup taken before any change: `/var/tmp/ishanp-agentic/rescue/backup-pre-activation`
(1146 files).

Three approvals acted on. **Two are fully live. One is blocked on a single root command, and that
is stated plainly below.**

---

## 1. The digest now proves delivery — live and measured

`[notify] dropped_marker` is set on the live instance, against the exact text your command prints
when it has nowhere to deliver.

Measured before and after, on the live instance, with a real owed item:

| | Recorded event | Cursor | Question |
|---|---|---|---|
| **before** (the defect) | `sent` / `1 items` | advanced past it | never retried |
| **after** (now) | `failed` / `1 items: …no [chair] channels configured` | does **not** advance | stays owed, retried next tick |

```
$ holon --home ~/holon msg send chair "is the digest reaching me now?" --kind question --as emp_…
$ holon --home ~/holon tick
  ('2026-10-22T20:28:02', 'failed', '1 items: …no [chair] channels configured — the message has
   nobody to reach')
  question still owed (unacked)?  True
```

The silent drop is closed. Anything the organization owes you is now either delivered or reported
as failed — never recorded as sent having reached nobody.

---

## 2. The heartbeat is live

```
systemctl --user is-enabled holon-tick.timer  →  enabled     (the symlink survives a reboot)
systemctl --user is-active   holon-tick.timer  →  active
```

Journal, from its own runs:

```
01:58:20 holon: swept 0 expired leases; 0 triggers; fired 0 schedules
02:00:20 holon: swept 0 expired leases; 0 triggers; fired 0 schedules
02:02:21 … 02:04:21 … 02:06:21 …            (every 2 minutes, ~1s each)
```

and `.clock/last_tick` is written each tick (`1792701599`), so `doctor`'s `heartbeat.stale` check
is silent rather than guessing.

---

## 3. How `holon tick` operationalizes everything — the architecture

`holon tick` is the kernel's only clock. It is **idempotent** — call it from any timer, any number
of times, and it converges rather than duplicating. `KillMode=process` means a dispatch it starts
outlives the tick itself, so a slow body never dies because its heartbeat ended.

```mermaid
flowchart TD
  T["holon tick  (every 2 min; idempotent)"] --> S["1. _salvage<br/>a run whose lease expired but whose result is on disk<br/>→ handed in, not redone"]
  T --> L["2. sweep_leases<br/>an expired lease's stage → failed, then re-queued<br/>so a dead executor's work is not lost"]
  T --> G["3. triggers<br/>event and rate triggers fire"]
  T --> N["4. notify<br/>the Chair's digest — the only path from the org to you"]
  T --> D["5. _deliver<br/>messages owed to the Chair"]
  T --> C["6. schedules<br/>due recurring plans and routines"]
  T --> H["7. held_checks<br/>results an agent handed in itself, whose dispatcher died"]
  T --> R["8. script lanes<br/>ready execute stages with lane=script"]
  T --> B["9. _backup (every backup_every_h = 24h)<br/>then pruned past backup_keep"]
  T --> DR["10. _doctor (every doctor_every_min = 30 min)<br/>a failing check opens an incident"]
```

Three properties worth naming:

**Nothing runs inline.** Except in `holon tick --foreground`, every phase that would start a body
spawns a detached process and returns. A stuck harness cannot stall the heartbeat, and the oneshot
unit cannot take the dispatch with it when it exits.

**One failing thing does not stop the clock.** Each schedule is recorded with its own outcome; one
employee's failure is one event, not a dead heartbeat. A stage that keeps failing opens an
incident, which is how you learn about it.

**Every phase has a guard.** Eight `HeartbeatFoundation` tests across these phases, plus the
systemd validity and reboot contract, all mutation-verified. The liveness stamp at
`.clock/last_tick` is what doctor reads instead of asking you to notice missing ticks.

What it does **not** do: it does not claim work, run harnesses inline (except `--foreground`), judge
results, or decide anything. Those happen when a body is dispatched or an agent reaches the broker.

---

## 4. Isolation — measured, and blocked on exactly one root step

**Honest answer to your question: no, isolation does not guarantee complete operationalization
without gaps. It closes the identity gap, which is the one that makes the others safe.** The
remaining gaps are listed at the end of this section, measured, not guessed.

### What is now verified

`[isolation] agent_user = "holon-agent"` was set and `isolation plan` was read. It confirmed the
reach measured earlier, exactly:

```
writes: pioneer-qf (and its core/data/logs roots), the two adapter homes
reads:  the jcode, operant and claude binaries, the checkout, the instance's kb/
runs:   <instance>/execution/runtime/runs/
```

`isolation apply` was then run. **Every `setfacl` failed**, with `Invalid argument near character
3`, and **nothing was written** — verified with `getfacl` on the instance's own dotfile.

### Root cause, confirmed by isolation

```
setfacl -m "u:nosuchuser9999:rwx" <file>   → setfacl: Option -m: Invalid argument near character 3
setfacl -m "u:ishanp:rwx"        <file>   → applied: user:ishanp:rwx
```

ACLs work on this btrfs. The failure is that **`holon-agent` does not exist yet**, and creating it
is the one step that needs root:

```sh
sudo sh /tmp/holon-isolation.sh      # useradd the agent user + install the runner service
```

I do not have non-interactive sudo here, so this is yours.

### Why I reverted the config key

Setting `agent_user` **without** the runner turned the live doctor from `0 errors` to:

```
ERROR isolation.runner   the runner does not answer (cannot reach @holon-runner-…: Connection
                        refused): no agent can run
```

That is the kernel being correct rather than permissive — once you promise an agent user, the
runner is required, and it refuses to let agents run as the kernel's user instead. But it would
have left the instance unable to run any agent, so `agent_user` was reverted until the root step
can run. `dropped_marker` survived the revert and the digest fix is untouched. **Live doctor: 0
errors, 11 warnings** (the 11 were there before this session).

### What is already proven about isolation

27 tests in `tests/test_isolation.py` pass, including: a harness running through the runner as the
agent user, the broker never acting as the Chair and ignoring a caller's `--home`, agents reaching
only agent commands, the runner refusing other uids, and **`doctor_probes_what_the_agent_user_can_reach`**
— the same probe `holon doctor` runs against your instance once the runner exists.

So the order that works is: root step → `isolation apply` → start `holon-broker` → `holon doctor`.
Doing it out of order is what produced the error above.

### Gaps isolation does not close (measured)

**Where the warnings actually are, counted from the live doctor after the export:**

- **`isolation.off`** — expected: `agent_user` is not set, so agents run as the kernel's user.
  This is the one the root step closes, and it is the highest-consequence warning in the list.
- **`workdir.missing` × 2 and `artifacts.dangling` × 7** — the live record points at B-side
  scratchpads under `/var/tmp/ishanp-agentic/claude-1000/…-hermes-organism/…/scratchpad/…` that
  the retired harness era left behind. Isolation changes nothing here: `[isolation] writes` is the
  pioneer-qf scope only, so those paths are outside the agent's reach either way. These are a
  record-keeping problem — restore the files, or supersede the results — now visible because the
  doctor checks for them.

**Correction to an earlier claim of mine:** I first reported `artifacts.dangling` as ×3. The
live doctor counts **7** dangling pointers across 5 works — one `Refine playbook:
routine-notes-digest` result names 2 unresolvable paths (a relative directory and a relative
checksum pair), four absolute paths point into the retired harness era's scratchpad. My earlier
count was wrong; the check is doing its job.

**`skills.export_stale` is now cleared** — it needed `holon skill export` against *both* export
targets (`adapters/jcode/home/skills` and `adapters/operant/home/skills`), because the live
instance registers an export per adapter home. The live instance went from 11 warnings to 10 the
moment the stale copies were refreshed; only the `isolation.off` warning and the B-side pointers
remain.

---

## 4b. What the live heartbeat now fires, and one real consequence of it being on

Enabling the heartbeat means the scheduler is no longer idle. Measured on the live instance:

```
sch_…JJPZ1N  recurring '0 6 * * *'  enabled  next 2026-10-23T06:00Z   (KB count smoke)
sch_…2SQMES  recurring '0 7 * * *'  enabled  next 2026-10-23T07:00Z   (disk usage report)
sch_…7RXF    recurring '0 8 * * *'  enabled  next 2026-10-23T08:00Z   (notes digest)
```

Three enabled plans, firing in the morning. Two of them create stages whose workdir is one of
the B-side scratchpads listed above, and bodies are available to claim them (7 active employees
on the live instance).

I checked what actually happens rather than assuming, and the kernel is correct here. The
workdir is a planning variable, not a hard dependency: `briefs.load` resolves
`node.get("workdir") or str(run_dir)`, so a body dispatched into a vanished workdir works in its
run directory and **`workdir.missing` names the stage**. That guard exists precisely because of
this instance's notes-digest routine — its own guard test cites this instance by name
(`tests/test_loops.py::…test_a_ready_stage_whose_workdir_is_gone_is_reported`), and says out loud
that the run still works in the run dir while the job it was hired for does not.

So the residual risk is not a crash or a lost body: it is a body spent on a stage that cannot do
its job, and a `worked in its run dir` result that reads as a success. **This is the case for
fixing the two workdirs (or cancelling the two plans) before tomorrow 07:00Z** — which the Chair
can do with existing commands, and needs no code change.

The two `ready` stages right now are exactly the instances the warning names:
`verify: Disk usage report — 2026-10-22T07:01` and `execute: Notes digest — 2026-10-22T08:01`.

## 4c. Closing the loop: what measuring found that the record did not say

The Chair's review flagged that this record never reported back whether the work satisfied its
requirements. Measuring it produced two corrections and one substantive repair.

**(1) The live heartbeat has not dispatched a body this session.** The `plan` and `approve` stages
of the 08:01 notes-digest work read `done`, which reads as a body having worked them. The event
log says otherwise: they are `kernel` copies of the recurring plan, written at 08:01:31 in the
same instant the work was created, with **no run row and no employee_id**. The run ledger for
that work is empty, and every tick run during this session logged `fired 0 schedules`.

So the heartbeat is alive and the timer is enabled, but **it has not yet proved it can dispatch
an agent on this instance**. The most recent real run is an operant `verify` that succeeded
2026-10-21T18:03, before this session. The next recurring firing is 2026-10-23T06:00Z, and that
is the first real evidence either way. The `HeartbeatFoundation` guards were written against
exactly this and pass in tests; that is not the same claim, and the record now says so.

**(2) Every live employee is bound to the `operant` driver, not `jcode`.** The adapter-conformance
question therefore has **no bearing on this instance's live operations**. The adapter suite passes
29/29 (including `test_the_run_token_reaches_a_stdio_mcp_server`, the seam recorded as failing),
which is code-level proof, not a live jcode body. `holon adapter test jcode` on the live instance
ran 25+ minutes producing no output before it was stopped — the same "explores, never submits"
symptom the register records.

**(3) Two real defects were found and fixed, because measuring rather than trusting surfaced
them.** Recorded as `9aa9d31` with red-first guards, mutation proof, and ledger rows:

- the mini-YAML reader unescaped single-quoted scalars, so a pool's
  `cmd: '… \"healthy\" …'` imported as `"healthy"` — a command that is not the one approved;
- the same reader mishandled folds in quoted scalars (a double-quoted `\` is YAML's escaped
  line break and joins with nothing; a single-quoted `\` is literal and folds with a space);
- `scope.stray` collected workdir names one level down, so a root directory that holds a live
  work's workdir was reported as owned by nothing.

**Verified from measurement, not inspection:** the live corpus, 20 real pool files read against
PyYAML, went from **2 divergences to 0**; full suite **606 tests OK**; strict lint 0/0; three
mutations of the reader each fail a guard; live instance 0 errors / 1 warning.

---

## 5. Channels: how they are configured, and where the surface area is

### How a channel is configured

There are exactly two config keys, and they are different things:

| Key | What it does |
|---|---|
| `[notify] command` | the kernel → Chair seam. The kernel computes the digest, substitutes `{message}`, shells this, and reads the exit code as proof. `dropped_marker` closes the silent-drop. |
| `[chair] channels` | `<platform>:<user id>` refs whose channel messages count as the Chair's — for a runtime that *fronts* a channel, never for agents. The broker still forbids agents every Chair command. |

Yours are currently:

```
[notify]  command = "operant org holon notify --reason heartbeat --text {message}"
[chair]   channels = ["telegram:5297486612"]   (was none until 2026-10-22)
```

So once the destination was named, the kernel computes, the adapter delivers, and the digest
now **reaches you**. Before this session's activation, `[chair] channels` was empty, and the honest
"nobody" was the correct output.

**[2026-10-22, activated]** The Chair channel is now wired and delivering:
`channels = ["telegram:5297486612"]`. The id was not guessed — it is the `admins` entry already
declared in the live gateway config, and the Telegram API confirms it is a private chat
(`@ishan_parihar`), which is exactly the identity the kernel's comment asked for. The bot is live
(`@ip_operant_testing_bot`).

The kernel's own seam is proven, not just the adapter's: a real owed item
(`msg send chair … --kind question`) ticked through the heartbeat and recorded
**`sent — 1 items`**, where the identical path recorded `failed` at 20:28Z. Two messages reached the
Chair during this activation: one adapter-level test, one through the kernel's digest.

Wiring a *different* destination is an adapter-side change (another platform on the gateway, plus
its ref here); law 2 forbids naming a platform under `holon/`.

### The surface area, in one picture

```mermaid
flowchart TD
  subgraph K["Kernel — the only writer"]
    W["work.db: messages · events · tasks"]
    P["pending_for_chair()<br/>the one digest"]
  end
  subgraph A["Adapter — the rendering, on your side"]
    N["[notify] command"]
    CH["[chair] channels"]
  end
  subgraph AG["Agents — through the broker, never the kernel's user"]
    M["msg send / reply / thread<br/>holon_msg_* via the derived MCP fence"]
  end
  W --> P --> N --> Chair(["Chair<br/>the digest, the thread, the reply"])
  CH --> Chair
  AG --> W --> P
  Chair -->|"msg reply"| AG
```

**Where the surface actually is, concretely:**

1. **Inbound to you (one seam).** `[notify] command`. Everything owed reaches you through it —
   decisions, incidents, breaches, and now employee `question`/`request` messages. It is one
   command, and it now proves delivery.
2. **Outbound from you (one path).** `msg reply <id>` — answers in the thread it answers, back to
   whoever asked. `msg thread <id>` reads the conversation.
3. **Agent ↔ agent.** `msg send` / `msg inbox` / `msg request`, plus 56 MCP tools a mounted body
   can call, all derived from the registry's actor class.
4. **The broker** (not yet running, because isolation is not applied) is the only door from agents
   into all of it. It authenticates the run token, never the Chair key, and never reads a file on
   the agent's behalf.

**What is deliberately not a surface:** a Telegram thread per agent, and direct Gmail/Telegram
tooling with scoped authorization. Both would build a second identity or a second authorization
beside the ones the kernel binds to the authenticated principal and the registry's actor class.
The per-employee thread is a *rendering* of `messages.sender` — your adapter's job, and the kernel
does not learn the mapping exists.

## 6. The one action left, and why it is safe to do now

```sh
sudo sh /var/tmp/ishanp-agentic/rescue/isolation-root-step.sh   # root: the agent user + its runner
# re-add [isolation] agent_user (step 1.5, below), then:
holon --home /home/ishanp/holon isolation apply                  # the ACLs (needs the user to exist)
systemctl --user enable --now holon-broker                       # install.py writes this unit
holon --home /home/ishanp/holon doctor                            # the isolation checks, probed as holon-agent
```

`[isolation] agent_user` is currently **absent** from `holon.toml` — re-add it at the same
time as the root step, or the `isolation.runner` error above returns:

```toml
[isolation]
agent_user = "holon-agent"
```

The backup at `/var/tmp/ishanp-agentic/rescue/backup-pre-activation/` covers the way back, and the
runbook is `/var/tmp/ishanp-agentic/isolation-one-root-step.md`. The root script it names is
byte-identical to what the kernel's own `isolation setup_plan` generates, and regenerable with
`holon isolation plan` — an earlier version of this handoff named a path under `/tmp`, which no
longer existed.
