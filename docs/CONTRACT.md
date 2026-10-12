# The harness contract

Everything a harness author needs to run Holon employees. The kernel guarantees this contract;
any agent runtime that honors it can work in the organization, and nothing else is required.
The ZeroClaw and Hermes adapters in `adapters/` are implementations of it, and
`holon adapter test <driver>` checks any harness against it.

## 1. A run

A run is one employee doing one stage of one piece of work. Push mode: `holon dispatch` (or the
heartbeat, `holon tick`) claims a stage for an employee and invokes its driver. Pull mode: an
employee session holding an employee key (`HOLON_KEY`) runs `holon claim`, works, and runs
`holon submit <run_id>`.

For each run the kernel writes `$HOLON_HOME/execution/runtime/runs/<run_id>/`:

| File | Written by | Content |
|---|---|---|
| `BRIEF.md` | kernel | the stage's instructions, rendered from the role skill's `briefs/<stage>.md` |
| `brief.json` | kernel | run, stage and work ids, role, posture, employee, every path below, the run token (mode 0600) |
| `identity.md` | kernel | the employee's identity card, for harnesses that put it in the system prompt |
| `context.md` | kernel | the compiled context: role, work, earlier stages' outputs, memory, lineage |
| `result.json` | **agent** | the result envelope (§2) |
| `driver.log` | kernel | command, exit code, stdout and stderr (push mode) |

Environment in push mode:

| Variable | Value |
|---|---|
| `HOLON_HOME` | the instance root |
| `HOLON` | the kernel command to run from any shell (also `holon` on PATH) |
| `PYTHONPATH` | lets `python -m holon` find the kernel |
| `HOLON_RUN_ID`, `HOLON_TASK_ID` | the run and its stage node |
| `HOLON_STAGE`, `HOLON_ROLE` | research, shape, plan, approve, execute, verify or retro; the role that performs it |
| `HOLON_POSTURE` | `observe` (change nothing) or `act` (execute only) |
| `HOLON_EMPLOYEE_ID` | who is working |
| `HOLON_BRIEF_DIR`, `HOLON_RESULT_PATH` | where the brief is and where the result goes |
| `HOLON_RUN_TOKEN` | authenticates this employee for this run only (§6) |

The kernel never passes `HOLON_CHAIR_KEY` to a harness.

## 2. Result envelopes

One JSON object per run, defined once in `holon/core/contract.py` and published as JSON Schemas
in [`schemas/`](../schemas) (`holon contract schemas` prints them). Every envelope may also carry
`learnings` and `decisions` (become memory) and `proposals` (go to the department's steward).

| Stage | Shape |
|---|---|
| research | `{"summary", "terrain": [{"content", "source", "kind"?, "confidence"?}], "open_questions"?}` |
| shape | `{"decision": accept \| redirect \| cancel, "note", "dept"? (redirect), "title"?, "intent"?, "acceptance"?, "task_class"?, "risk"?, "priority"?, "assignee"?, "research"?}` |
| plan | `{"kind": "plan", "findings", "steps", "writes"?, "external_effects"?, "grade"?, "assignee"?, …}` or `{"kind": "decompose", "rationale", "children": [≥2 work items, "after"?]}` or `{"kind": "needs_input", "question"}` |
| approve | `{"decision": approve \| reject, "note"}` (the note is required to reject) |
| execute | `{"outcome": success \| partial \| failure \| noop, "summary", "documentation", "artifacts"?, "blockers"?, "next_intent"?}` |
| verify | `{"verdict": pass \| fail, "note"}` |
| retro | `{"summary", "cause", "actions"?: [work items]}` |

A harness that cannot write files may print the object as the last JSON in its output (driver
`result = "stdout-json"`).

**Non-compliant output.** An observe-stage run fails and its stage returns to the board. An
execute run's output is wrapped as a `partial` result for an independent verifier to judge, so
the agent never decides. A late result, after the lease expired, is discarded.

## 3. Drivers (push mode)

One TOML file per harness in `$HOLON_HOME/config/drivers/` (adapters install theirs):

```toml
name      = "my-harness"
command   = "my-agent --system-file {identity_md} --message {spawn}"
cwd       = "{workdir}"
timeout_s = 1800
result    = "file"          # or "stdout-json"
read_only = "--plan-only"   # inserted at {read_only} for observe stages
stages    = ["plan", "execute"]   # optional; default every stage
read_only_enforced = true   # the harness denies writes in observe stages itself
[env]
SOME_VAR = "value"
```

Placeholders (shell-quoted): `{brief_dir} {brief_md} {context_md} {identity_md} {result_path}
{workdir} {run_id} {task_id} {stage} {role} {posture} {employee_id} {employee_slug} {read_only}
{prompt}` (the BRIEF.md text), `{spawn}` (a one-line message pointing at BRIEF.md) and `{holon}`.
Double literal braces: `{{ }}`. A driver that still lists `duties` is read as the matching stages.

**Read-only.** For observe stages the kernel fingerprints the working directory before and
after the run and fails the run if it changed. A harness that enforces read-only itself declares
`read_only_enforced`; the kernel still checks unless the instance sets `[planner]
trust_harness_read_only = true` (for working trees other processes also write).

## 4. Leases

Every run holds a lease: research and plan 30 min, execute 60, verify and retro 20, shape and
approve 15. Renew with `holon run heartbeat <run_id>`. The heartbeat reclaims expired leases: the
stage returns to the board, and an execute stage counts as a failed attempt.

## 5. Staleness

A plan lists in `writes` the paths it will modify. The kernel fingerprints them when the plan
is submitted and again when an executor claims the work; if they changed, the execute stage is
cancelled and a fresh plan stage queued. A retry after a partial execution therefore usually
replans; that is deliberate, because the findings changed.

## 6. Identity

The CLI authenticates every call. A run token (`HOLON_RUN_TOKEN`, minted per claim) lets an
employee read, message, add to its own memory, heartbeat and submit **its own run**. An employee
key (`HOLON_KEY`, issued with `holon hr key`) does the same for pull-mode sessions. The Chair key
(`HOLON_CHAIR_KEY`, or the file at `HOLON_CHAIR_KEY_FILE`, by default
`~/.config/holon/keys/<instance>.chair`) is created by `holon init` outside the instance. Adapters
must keep it out of every agent's reach: deny `~/.config` to the harness, or run the kernel's
timer as another user. `holon help --json` lists the actor class each command requires.

## 7. Adapters

An adapter is everything one harness needs, kept in `adapters/<harness>/` so the kernel never
names it. Every adapter has the same five parts:

| Part | What it provides |
|---|---|
| Driver | `driver.toml` (§3), installed into the instance's `config/drivers/` |
| Identity | the employee's identity card reaches the agent: `{briefing}`, `{identity}` or `{identity_md}` |
| Skills | the skills library exported where the harness loads skills (`holon skill export --to …`) |
| Posture map | observe stages get a harness setting that cannot write; execute gets the approved plan's reach |
| Conformance | `holon adapter test <driver>` passes every clause: driver, plan, approve, execute, verify |

Shipped adapters: `zeroclaw` (posture aliases, its own skill bundle, observe profile without
write tools), `hermes` (skills as a Hermes category, posture toolsets) and `claude-code` (plan
mode for observe stages). `generic-shell` in the kernel is a template for anything else.
