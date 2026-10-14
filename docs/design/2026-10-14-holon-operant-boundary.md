# Holon and Operant: the boundary (2026-10-14)

Status: **proposed**, awaiting the Chair. This record refines §3 and §5 of
`2026-10-14-operant-integration.md`; the decision to run Holon beside Operant rather than inside
it stands. It is the canonical statement of what belongs to Holon (Python) and what belongs to
Operant (Rust). Operant's side, module by module, is in Operant's `docs/agentic-os/`:
- `README.md`
- `BOUNDARY.md`
- `ORG-LAYER-DISPOSITION.md`
- `IMPLEMENTATION-PLAN.md`

The two systems will ship as one package. Inside that package there is **one organization**. Each
capability has exactly one owner, and each database exactly one writer. The language of each part
is chosen by a rule, not by where the code happens to be today.

## 1. The rule: what is Rust, what is Python

**Rust (Operant) owns the runtime.** A capability is Rust when any of these holds:

| # | Test | Why |
|---|---|---|
| R1 | it is **resident**: a daemon, a server, a supervisor waiting on an agent, a watcher | resident memory is paid all day and multiplies with concurrent runs |
| R2 | it runs **per model turn or per tool call**: enforcement, metering, trajectory capture, fitting context into the model's window | it is on the hot path |
| R3 | it acts **on the operating system for agents**: processes, Unix users, sandboxes, sockets, run directories | this is the security boundary; it must be small, typed and resident |
| R4 | it talks to **the outside world in real time**: channels, webhooks, the Chair's chat, MCP | these connections are long-lived |

**Python (Holon) owns the organization's rules and records,** and only under all of these
conditions:

| # | Condition | Why |
|---|---|---|
| P1 | it is a **decision about the organization**: what work exists, who may approve it, what counts as good, what is remembered, what changes | this is the surface the self-improvement loops rewrite |
| P2 | it runs **per stage, per tick or per Chair command** (minutes apart), never per token or tool call | interpreter overhead is irrelevant at that frequency |
| P3 | it runs as a **short-lived process**: zero resident memory between invocations | there is no Python daemon to keep in memory |
| P4 | it **must be changeable by the organization's own pipeline**: read, patched and tested by any agent on any harness in seconds, with stdlib only and no toolchain or compile | a self-improving kernel that takes a 5–10 minute compile and a fleet deploy per change improves slowly and is hard to roll back |

**Shared rules**

| # | Rule |
|---|---|
| S1 | **One writer.** Only Holon writes Holon's databases. Operant reads them only through the published read API (§4.1). Operant writes only its own stores: sessions, memory-wire, trajectories, the runtime ledger. Holon never reads Operant's databases; Operant *reports* to Holon through the run contract and the CLI. |
| S2 | **One definition.** Vocabulary and result shapes are defined once in Python (`core/vocab.py`, `core/contract.py`). Operant consumes the generated JSON Schemas (`holon contract schemas --write`) and the read-API version, and a Rust test fails when they drift. |
| S3 | **One organization.** Operant without Holon is a personal agent: ungoverned chats, personal skills, memory and automations, behaving exactly as before. Operant with Holon takes **all** organizational state from Holon. There is no third mode and no Rust-side org store. |
| S4 | **Policy is declared in Holon and enforced in Operant.** Holon compiles what a run may do (plan, scope, posture, budget) into data. Operant enforces that data on every call and reports what it refused. |

### 1.1 Measurements behind the rule (workstation, 2026-10-14)

| Process | Peak memory | Wall time | Resident? |
|---|---|---|---|
| `holon status` | 27.7 MB | 0.5 s | no |
| `holon task list` | 29.3 MB | 0.5 s | no |
| `holon doctor` | 30.8 MB | 1.0 s | no |
| `holon lint` | 44.3 MB | 2.0 s | no |
| `operant --version` | 14.6 MB | 0.0 s | no |
| `operant run` (one model turn) | 40.0 MB | 10.4 s | for the run |
| `hermes chat -q` (failed before its first model call) | 100.4 MB | 13.0 s | for the run |
| `operant-gateway` (daemon, Telegram, 2 AFT bridges) | 58 MB (+27 MB bridges) | — | yes |
| `holon-notion` (mirror daemon) | 32.6 MB | — | yes |

**The finding.** Holon's kernel does not need much memory. Each command is a ~30 MB process that
exits in a second, and nothing stays resident. Holon's real memory cost is two Python processes
held open **per running agent**:
- the dispatcher that waits on the harness;
- the stdio MCP server the harness mounts;

plus resident helpers (the broker, the Notion mirror), and above all a Python **body** (Hermes,
≥100 MB before doing anything). The plan therefore:
- moves every resident or per-run Python process into Operant;
- makes Operant the body;
- keeps Python only where P1–P4 hold.

The Python that remains justifies itself by P4 (the self-improvement surface), not by needing
memory.

## 2. Ownership by capability

| Capability | Owner | Language | Justification |
|---|---|---|---|
| Vocabulary, result contract, schemas | Holon | Py | P1, P4; Operant consumes generated schemas (S2) |
| Work graph: intake, stages, claims, leases, rework, retro, cancel | Holon | Py | P1–P4: the state machine every loop acts through |
| Grading, approval routing, authority, earned authority, audit sampling | Holon | Py | P1: authority is data the organization changes |
| Departments, employees, roles, genome profiles, hire and fire, reviews, probation | Holon | Py | P1, P4 |
| Institutional memory: consolidation, promotion, pins (writer of `memory.db`) | Holon | Py | P1: what is known is a decision; promotion is counted evidence |
| Skills library, playbooks (protocol + rubric), versioning, apply-after-verify, export | Holon | Py | P1, P4: the instructions the loops rewrite |
| Rubric judge, gold standard selection, judgments, refinement triggers | Holon | Py | P1, P4; one decision-model call per result |
| Objectives, key results, reviews | Holon | Py | P1 |
| Lane policy: classification, routine approval, routine import | Holon | Py | P1, P4 |
| Schedules and routine definitions, including **event triggers** | Holon | Py | P1: what runs when is an organizational decision |
| Context *selection*: which memory, lessons, playbook, gold standard and history a stage sees (the brief) | Holon | Py | P1, P4: what context improves results is learned |
| Metrics, retros, incidents, smoke-check *definitions*, doctor and lint *rules* | Holon | Py | P1, P4 |
| Run policy compilation (`policy.json`: plan, scope, posture, budget) | Holon | Py | P1: declared once (S4) |
| Isolation setup plan (the one-time root script and ACLs) | Holon | Py | P2: run once; generates data |
| **Agent loop, tools, MCP client, models, fallbacks** | Operant | Rust | R2, R4 |
| **The body for Holon employees** (driver target) | Operant | Rust | R1, R2: 40 MB against ≥100 MB for a Python body |
| **Run supervision**: claim through Holon, spawn, heartbeat, wait, salvage, hand in | Operant | Rust | R1, R3: replaces one resident Python dispatcher per run |
| **Runner**: agent processes as the agent user, sandbox backends | Operant | Rust | R1, R3 |
| **Broker**: the organization served to isolated agents (reads via the read API, writes via `holon`) | Operant | Rust | R1, R3 |
| **Native Holon tools** for agents (whoami, memory search, message, submit), replacing `holon mcp` | Operant | Rust | R1, R2: replaces one resident Python MCP server per run |
| **Policy enforcement per tool call** (`policy.json` through `seat_policy::decide`) | Operant | Rust | R2, R3 (S4) |
| **Token and cost metering**, budget enforcement at the turn boundary | Operant | Rust | R2; caps declared in Holon (S4) |
| **Trajectory capture** into the run directory | Operant | Rust | R2 |
| **Learning capture**: memory and skill nudges, `skill_manage`, curator, which in a Holon run become result annotations | Operant | Rust | R2; Holon verifies them (P1) |
| **Context *fitting***: token budget against the model window, cache-stable prefix, eviction | Operant | Rust | R2 |
| **The clock**: firing `holon tick` (replaces the systemd timer in the package) | Operant | Rust | R1 |
| **Event intake**: channel, webhook and file events matched against Holon's triggers | Operant | Rust | R1, R4; *what* a trigger means stays Holon's |
| **The Chair's channel**: Telegram commands, notify delivery, identity verification | Operant | Rust | R4 |
| **Views**: TUI panels and `operant org` over the read API | Operant | Rust | R4 (presentation only) |
| Smoke and doctor *probe execution* (as the agent user) | Operant | Rust | R3; the checks are defined in Holon |
| Notion mirror | Holon adapter | Py, event-driven | its webhook endpoint moves into Operant's gateway (R4), which invokes the Python handler per event, so nothing stays resident; reconciliation runs from `holon tick` |
| Decision-model client | both | both | Holon: decisions that change the organization (judge, lane, triage, routing). Operant: real-time routing in the gateway (Chair intent, notify urgency). One `[classifier]` configuration, read by both |

## 3. What moves, what retires

**From Holon (Python) to Operant (Rust).** Holon keeps each Python piece as the *portable fallback*
for deployments without Operant (the VPS runs ZeroClaw). Both implementations pass one conformance
suite. Each Python piece retires when the last deployment without Operant migrates. That is a
Chair decision, tracked in STATUS.

| Holon today | Operant replacement |
|---|---|
| `holon dispatch` + `drivers.run_driver` (a resident Python process per run) | the run supervisor |
| `isolation.serve_runner` | the runner |
| `isolation.serve_broker` | the broker |
| `holon mcp` (a resident Python MCP server per run) | native Holon tools |
| `adapters/systemd` heartbeat timer | Operant's clock |
| `adapters/notion` resident webhook server | the gateway's webhook route invoking `holon notion apply` per event |
| `[notify]` command hook | Operant channel delivery |

**From Operant's org layer to Holon.** `crates/operant-core/src/org/` (21.6k lines) and its
satellites keep only the mechanisms §2 gives to Operant. The rest retire to the Holon mechanism
they duplicate. The full module table is in Operant's `docs/agentic-os/ORG-LAYER-DISPOSITION.md`.
In short:

| Retires to Holon | Operant modules |
|---|---|
| departments, employees, hierarchy | `cast`, `employee_db`, `department_db`, `hierarchy_edges`, `identity_gate`, `schema` |
| authority and approval of org work | `authority` (grants), `pending_requests` |
| messages and requests | `notice*`, `resolver` |
| runs and events | `worklog*`, `write_barrier`, `decisions_db` |
| memory promotion and metrics | `synthesis` |
| collaboration routines | `socialization` |
| the work graph | kanban |
| playbook refinement | `operant-runtime` `sop`, `skills/improver`, `skillforge` |
| verified skills and memory | kernel-sidecar's continual-harness lesson store |

**Kept and rewired in Operant:**
- the pure policy engine (`seat_policy::decide`);
- budget enforcement and the usage accumulator;
- escalation, which becomes refusals reported as blockers;
- channel-sourced context injection;
- session-to-employee binding and rolling summaries;
- the scheduler, as the clock;
- the event matcher (`operant-runtime` `routines`);
- the sandbox backends.

## 4. The interfaces

### 4.1 The read API (Holon publishes, Operant reads)

A migration adds SQL views (`api_*`) over Holon's databases and an `api_version` row.
Operant opens the databases **read-only** (SQLite `mode=ro`), as law 3 already allows other
capabilities to.

| View | Columns |
|---|---|
| `api_employees` | id, name, department, roles, manager, status, driver, posture |
| `api_departments` | id, slug, mission, head, profile, scope roots |
| `api_board` | ready and running stages: work, stage, department, lane, lease, employee |
| `api_approvals` | pending plans: grade, approver, summary |
| `api_messages` | per recipient: unread messages |
| `api_triggers` | routine id, trigger kind (`cron`, `event`), pattern, department, enabled |
| `api_memory` | readable items per scope (FTS through the existing `items_fts`) |

Lint gains `api.views`: every view exists and matches `api_version`. A view changes only with a
version bump, which Operant's test checks.

### 4.2 The run protocol (one run, either direction)

| Step | Who | How |
|---|---|---|
| prepare | Operant supervisor | `holon dispatch --prepare --json --employee E` (kernel actor, **new**): claims, writes the brief, `policy.json` and the run token; returns run id, run dir, env and argv without spawning |
| run | Operant runner | spawns the body (Operant itself in-process or as a child, or any driver's argv) as the agent user in the sandbox |
| heartbeat | Operant supervisor | `holon run heartbeat` (exists) while the body runs |
| hand in | Operant supervisor | `holon run finish RUN --exit N` (kernel actor, **new**): salvages `result.json`, submits, and queues post-execute (judge and checks run in their own short Python process, as today) |
| files in the run dir | | `BRIEF.md`, `policy.json` (**new**), `result.json`, `trajectory.json` (**new**, optional), `driver.log` |

### 4.3 Contract additions (Holon `docs/CONTRACT.md`, landing with the phase that uses them)

| Addition | Meaning | Who uses it |
|---|---|---|
| `policy.json` | `{version, grade, stage, posture, roots[], writes[], commands[], deny[], budget: {tokens, cost}}` | any harness may enforce it; Operant must |
| `result.refusals[]` | calls the harness refused under `policy.json`; the kernel treats them as blockers (rework or escalation), not breaches | the harness |
| `trajectory.json` | ordered steps (thought summary, tool, arguments digest, outcome) | stored with the run; the gold standard carries the last verified trajectory to briefs and the judge (compacted) |
| `result.usage` | `tokens_in`, `tokens_out`, `cost`, `tool_calls`, `duration_s` | exists in part (`Work.set_run_usage`); fill it in |

### 4.4 Chair commands from a channel

Operant's gateway runs as the kernel's user. It may run `holon` **as the Chair** only for a message
from an identity listed in Holon's `[chair] channels` (**new**). A decision model classifies the
intent (approve, reject, answer, note, new work), and the gateway confirms before any approval.
The broker allowlist forbids every Chair command, so agents cannot reach this path.

## 5. Holon-side work packages (Python)

| # | Work | Phase (Operant plan) |
|---|---|---|
| H1 | `adapters/operant/` driver, postures, installer, conformance | A |
| H2 | read API views + `api_version` + lint `api.views` | B |
| H3 | `policy.json` compilation at claim; `refusals` in the contract | C |
| H4 | `trajectory.json` storage; the gold standard and judge include the verified trajectory | D |
| H5 | token budgets in `[budget]` (per department and employee), compiled into `policy.json` | C |
| H6 | `dispatch --prepare` and `run finish` (kernel), a shared conformance suite for external supervisors | E |
| H7 | event triggers on routines (`routine add --on event:<pattern>`), `api_triggers`, `holon routine fire ID --payload` (kernel) | F |
| H8 | `[chair] channels`; `holon notion apply` per event | F |
| H9 | `holon org import-operant`: map the nine cast seats onto departments and roles (dry run by default) | G |
| H10 | record the harness build per run; group judgments by build; a regression opens incident work | H |
| H11 | retire the Python runner, broker, MCP server and dispatcher spawn when no deployment without Operant remains | I (Chair) |

## 6. What this changes in the earlier record

`2026-10-14-operant-integration.md` §5 is replaced by the phase plan in Operant's
`docs/agentic-os/IMPLEMENTATION-PLAN.md` (phases A–I), which carries both sides' work and its
order. These parts of that record stand:
- the reasons for not porting Holon into Operant (§4);
- the RSI review (§6).
