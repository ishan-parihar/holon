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
| `brief.json` | kernel | run, stage and work ids, role, posture, employee, every path below, the run's exported skills (`skills`), the run token (mode 0600) |
| `identity.md` | kernel | the employee's identity card, for harnesses that put it in the system prompt |
| `context.md` | kernel | the compiled context: role, work, earlier stages' outputs, memory, lineage; for a plan, the kernel-computed grade floor and approver |
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
| `HOLON_RUN_DIR`, `HOLON_RESULT_PATH` | this run's directory (where `policy.json`, `trajectory.json`, `usage.json` and refusals live) and where the result goes; `HOLON_BRIEF_DIR` is an alias of `HOLON_RUN_DIR` |
| `HOLON_RUN_TOKEN` | authenticates this employee for this run only (§6) |
| `HOLON_BROKER` | set under isolation (GENOME §11): the broker's address; `holon` sends every call there |

The kernel never passes `HOLON_CHAIR_KEY` to a harness. Under isolation the harness gets only these
variables and the driver's `[env]`, added to the agent user's own environment, never the kernel's.
A department's configured secrets (`[secrets.dept.<slug>]`, GENOME §6) also arrive as their named
environment variables, resolved at claim — this list plus those is exactly the body's environment.
The kernel reads `result.json` without following a symlink and only as a regular file of at most
8 MB; a run directory is sticky, so an agent can add its result but not replace the files the
kernel wrote.

## 2. Result envelopes

One JSON object per run, defined once in `holon/core/contract.py` and published as JSON Schemas
in [`schemas/`](../schemas) (`holon contract schemas` prints them). Every envelope may also carry
`learnings` and `decisions` (become memory), `proposals` (go to the department's steward),
`refusals` (§10), `usage` (§11) and `guard` (an incident fix's regression check: registered when
the fix verifies, run on every heartbeat; a failure reopens the incident).

| Stage | Shape |
|---|---|
| research | `{"summary", "terrain": [{"content", "source", "kind"?, "confidence"?}], "open_questions"?}` |
| shape | `{"decision": accept \| redirect \| cancel, "note", "dept"? (redirect), "title"?, "intent"?, "acceptance"?, "task_class"?, "risk"?, "priority"?, "assignee"?, "research"?}` |
| plan | `{"kind": "plan", "findings", "steps", "writes"?, "external_effects"?, "grade"?, "assignee"?, …}` or `{"kind": "decompose", "rationale", "children": [≥2 work items, "after"?, "lane"?]}` or `{"kind": "needs_input", "question"}` |
| approve | `{"decision": approve \| reject, "note"}` (the note is required to reject) |
| execute | `{"outcome": success \| partial \| failure \| noop, "summary", "documentation", "artifacts"?, "blockers"?, "next_intent"?, "writes"?, "external_effects"?}` |
| verify | `{"verdict": pass \| fail, "note"}` |
| retro | `{"summary", "cause", "actions"?: [work items]}` |

**Handing in an execute result yourself** (`holon submit`, `holon_submit`, the broker) only records it.
The kernel then runs the machine checks and the rubric judge in its own process (the dispatcher that
started you, or the heartbeat: `holon post-execute`), and holds the verify stage from verifiers until
it has. The dispatcher may stop your harness as soon as the result is in; nothing you started after
handing it in can change the outcome.

**Fast lane.** An execute run of `fast` work (GENOME §10) has no separate plan: the executor plans and
acts in one session, lists the `writes` it made and any `external_effects`, and returns `noop` with
`blockers` when the work is not routine after all (the work then goes through the full pipeline).
Nothing changes for the harness: it is an execute stage like any other.

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
usage_line = 'in=(?P<tokens_in>[0-9]+) out=(?P<tokens_out>[0-9]+)'   # optional: one per model call
[env]
SOME_VAR = "value"
```

Placeholders (shell-quoted): `{brief_dir} {brief_md} {context_md} {identity_md} {result_path}
{workdir} {run_id} {task_id} {stage} {role} {posture} {employee_id} {employee_slug} {read_only}
{prompt}` (the BRIEF.md text), `{spawn}` (a one-line message pointing at BRIEF.md), `{holon}`,
and `{scope}`: the department's scope name (its slug if `holon.toml` has `[scope.dept.<slug>]`, else
`org`; GENOME §8). A harness that sandboxes builds one sandbox per scope, and its installer stamps
`scope_fingerprint` (from `holon scope`) into the driver so `holon doctor` can report drift.
Double literal braces: `{{ }}`. **Usage.** If `usage_line` matches lines on stdout or stderr, their
sum is the run's token usage (it survives the kernel stopping a harness whose agent already
submitted); otherwise the `usage` object of a `stdout-json` summary is used. A driver that still lists `duties` is read as the matching stages.

**Read-only.** For observe stages the kernel fingerprints the working directory before and
after the run and fails the run if it changed. A harness that enforces read-only itself declares
`read_only_enforced`; the kernel still checks unless the instance sets `[planner]
trust_harness_read_only = true` (for working trees other processes also write).

## 4. Leases

Every run holds a lease: research and plan 30 min, execute 60, verify and retro 20, shape and
approve 15. Renew with `holon run heartbeat <run_id>`. In push mode the dispatcher renews it while the harness
process is alive, so a lease expires only when the dispatcher died; the driver's `timeout_s` bounds
the run (the harness and its shell are killed). Once the run's result is in (the agent called `holon submit` or
`holon_submit`) or the run was reclaimed, the dispatcher stops the harness within a minute: a
closing reply cannot change the outcome and would only hold the department's budget slot. The heartbeat reclaims expired leases: the
stage returns to the board, and an execute stage counts as a failed attempt.
Except when the agent finished: a run whose `result.json` is in its run directory when its lease
expires (its dispatcher died after the harness wrote it) is handed in by the heartbeat instead.

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
must keep it out of every agent's reach. The kernel does that itself with isolation (GENOME §11):
agents run as their own Unix user and reach the organization through the broker, which accepts
only run tokens and employee keys and only the commands an employee needs (`holon mcp`'s tools,
`submit`, `whoami`, `help`, `task list`, `plan queue`). `holon help --json` lists the actor class
each command requires.

## 7. Adapters

An adapter is everything one harness needs, kept in `adapters/<harness>/` so the kernel never
names it. Every adapter has the same five parts:

| Part | What it provides |
|---|---|
| Driver | `driver.toml` (§3), installed into the instance's `config/drivers/` |
| Identity | the employee's identity card reaches the agent: `{briefing}`, `{identity}` or `{identity_md}` |
| Skills | the skills library exported where the harness loads skills (`holon skill export --to …`); every prepared run also carries its department-resolved copy in `runs/<id>/skills` (named `skills` in `brief.json`) — a body whose harness lists skills points its loader there, so a tailored skill appears once, as the department's version, never beside a standard twin |
| Posture map | observe stages get a harness setting that cannot write; execute gets the approved plan's reach |
| Conformance | `holon adapter test <driver>` passes every clause: driver, plan, approve, execute, verify |

Shipped adapters: `zeroclaw` (posture aliases, its own skill bundle, the Holon MCP server, Chair
notifications through its channels, observe profile without write tools), `hermes` (skills as a
Hermes category, posture toolsets) and `claude-code` (plan mode for observe stages). `generic-shell`
in the kernel is a template for anything else.

## 8. Native tools (MCP)

`holon mcp` serves the organization over the Model Context Protocol (stdio, JSON-RPC 2.0). A
harness that mounts it gives employees native tools instead of shell commands: `holon_whoami`,
`holon_submit` (the result envelope as a structured argument, so observe stages need no file
write), and the employee commands of the registry (`holon_memory_search`, `holon_msg_send`,
`holon_run_heartbeat`, …). The server inherits the harness's environment, so `HOLON_RUN_TOKEN` or
`HOLON_KEY` authenticates every call exactly as on the CLI; chair-only commands are never exposed.

## 9. The read API (api_version 4)

Migrations `work/0008`, `work/0013` and `work/0015` publish SQL views over `work.db` that outside surfaces
may `SELECT` from, opened **read-only** (`mode=ro`, law 3). Holon is the only writer. One view
lives on `memory.db` — SQLite views cannot reach across attached databases, so `api_memory`
is published there (migration `memory/0003`); open or attach `memory.db` read-only for it.

- `api_meta`: the `api_version` row. A view's columns change only with a version bump; a
  consumer refuses a version it does not know (`holon lint` pack `api` proves both sides).
- `api_employees` — id, name, title, department, manager_id, manager, status, driver, roles
- `api_departments` — id, slug, name, mission, head_id, head, status, profile
- `api_board` — ready and running stages: id, title, stage, status, lane, priority, risk,
  department, assignee, approver, grade, run_id, lease_until, employee_id, started_at
- `api_approvals` — pending approvals: id, title, department, grade, approver, risk, plan
  (the plan output JSON), created_at
- `api_messages` — unacked messages: id, ts, sender, to_ref, kind, task_id, body
- `api_triggers` — routine triggers: id, trigger_kind ('cron' or 'event' (H7)),
  pattern, department, employee, enabled, failure_streak, next_run_at
- `api_units` — the synced genome units: slug, charter, category, tier, owner, synced_at (§14)
- `api_services` — the services units offer: unit, service, command (the entry point), accepts,
  returns, effect (read | write), args, output, note (prefer-this-route), health
- `api_pool` — the pool edges: unit, dept (`"*"` for an org-wide surface) — what a runtime
  indexes so a department's pooled services become its deferred tools (R-04)
- `api_memory` (on `memory.db`) — the organization's live knowledge: id, scope, kind, content,
  provenance, confidence, pinned, valid_from

v4 (migration `0015`, C1–C3) is **additive**: no column is reshaped or removed, so a v3-era
consumer reading by name sees the same rows; `owner`, the service contract and `api_pool`
are new. A consumer refuses an api_version it does not know (`holon lint` pack `api` proves both
sides), so a v3 consumer keeps working unchanged on a v4 database.

The memory capability stays reachable through its own FTS (`items_fts`), same law.

## 10. policy.json and refusals (H3+H5)

Every claim writes `runs/<id>/policy.json` beside the brief — the run's reach, compiled
from the same sources the brief's prose and the sandbox adapters use ([scope] plus the
department's own, the pooled units' read-only roots, the approved plan's declared `writes`,
the `[budget]` per-run caps):

```json
{"version": 1, "grade": 2, "stage": "execute", "posture": "act",
 "roots": ["<workdir>", "/srv/shared"], "read_roots": ["~/data/vault"],
 "writes": ["hello.txt"], "commands": ["ls", "git"], "deny": [],
 "budget": {"tokens": 8000, "cost": null}}
```

`roots` are writable; `read_roots` (pooled units, GENOME §14) are reads only — a harness that
fences writes by `roots` refuses a write into them, and the kernel floors a plan that declares
one at G3. The field is additive and policy version stays 1: a harness that does not know it
ignores it. A harness MAY enforce the policy; a harness with its own enforcement layer MUST
(its posture configs give way to the policy). A call outside the policy is refused by the
harness and reported in the result envelope:

```json
{"refusals": [{"tool": "terminal", "reason": "rm -rf outside roots", "policy": "writes"}]}
```

The kernel treats refusals as blockers (rework or escalation), never breaches: the run's
event trail records each refusal, and refusals on a "successful" execute surface as
blockers for the verifier. Budgets: `[budget] tokens_per_run` / `cost_per_run`, overridden
per department (`[budget.dept.<slug>]`) then per employee (`[budget.employee.<id>]`);
0/absent = no cap. The harness stops the run at a cap and says so in `refusals`.

## 11. trajectory.json and usage (H4)

Beside `result.json` a harness MAY write `trajectory.json` — the run's ordered steps, one
object per step with `thought` (a summary), `tool`, `args` (a digest) and `outcome`:

```json
{"steps": [{"thought": "read the config", "tool": "file_read", "args": "app.toml",
            "outcome": "port 8000"}]}
```

The kernel compacts it (12 steps, 160 characters a field) and stores it with the run's
output. Once a verifier passes the result, that trajectory is the gold standard the next
run of the same job is held to: its brief and the rubric judge both see it (§3 of the
boundary). Absent or malformed, it is dropped — never fatal; the trajectory steers the
next run, the result decides this one.

The envelope may also carry `usage`: `{"tokens_in", "tokens_out", "cost", "tool_calls",
"duration_s"}`, all optional numbers, plus `build` — an optional string naming the harness
build that ran (its version or revision). The kernel records them on the run for **every**
stage (budgets and cost per verified result read them). A usage the harness measured itself
wins over the envelope's claim: write `<run dir>/usage.json` with the same fields beside
`result.json`. A run that names no build falls back to its driver's `build_cmd` (a cheap
command like `operant --version`, run once per driver per tick and cached). The rubric judge's
rows carry the judged run's build, so pass rates group by build — and when a build's pass rate
drops materially below the previous build's, incident work opens (GENOME §6
`[healing] build_regression_*`).

## 12. The supervisor protocol (H6)

Push mode spawns the driver from `holon dispatch`. A **supervisor** — a resident runtime that
owns processes (a daemon, a sandbox supervisor) — runs the body itself, through the same claim
and the same rules (lease, concurrency, budget, policy). Three commands, one run:

| Step | Command | What it does |
|---|---|---|
| prepare | `holon dispatch --as E [--stage S --task T] --prepare --json` | claims, writes the brief, `policy.json` and the run token, and prints them as JSON — the rendered driver `command`, `cwd`, `env` (including `HOLON_RUN_TOKEN`), `timeout_s`, `result_path`, `lease_until` — without spawning anything |
| heartbeat | `holon run heartbeat RUN` (with the run token) | extends the lease while the body runs; a supervisor that stops renewing lets the run be reclaimed |
| hand in | `holon run finish RUN --exit N` | salvages `result.json` (missing or invalid is a contract breach, exactly as in push mode), submits it, and runs what follows — machine checks, then the judge — in this short kernel process |

The supervisor may run the body in-process (its own harness), spawn the returned `command`
under its own sandboxing, or spawn any driver's argv. The run's outcome depends only on the
result handed in and the policy it was given: `result.json` written by the body,
`trajectory.json` optional beside it (§11). A prepared run that is never finished reverts by
lease expiry, like any abandoned run.

On an isolated instance the prepared JSON carries `isolation` (the `agent_user` and the
`runner`). A supervisor **MUST** run the body as that user through that runner — or decline
the claim; a supervisor that runs the body as itself defeats the trust boundary, and
`holon dispatch --prepare` refuses without `--runs-as-agent-user`.

## 13. Event triggers (H7)

A routine may fire on events instead of a clock: `holon routine add … --on event:<pattern>`. The
runtime that sees an event (a gateway, a webhook receiver) reads `api_triggers` (§9), matches
`event` rows' patterns against it, and calls:

```sh
holon routine fire <schedule_id> --payload 'what the event carried'
```

Holon never matches events itself. Firing is at most once per `[events] cooldown_s` (GENOME §6),
then the same skip-if-open rule a cron firing has: slow work never piles up. The payload rides on
the firing's work as a message, where the executor's brief shows it as **external content —
data, not instructions**. A webhook that many systems must react to is one routine per reaction,
each with its own pattern and cooldown.

`--payload` takes the text **literally**: never a file reference. A runtime relaying channel
text MUST NOT let that text name a path — `--payload @/etc/hostname` posts the literal string
`@/etc/hostname`. A runtime that reads the event from a file it chose itself passes
`--payload-file`. A skipped firing (cooldown, or work still open) exits with the kernel's
NOTHING code (3) and `{"skipped": …}` on stdout — runtimes count skips, not firings.
