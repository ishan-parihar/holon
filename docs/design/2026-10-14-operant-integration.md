# Holon on Operant: audit and integration plan (2026-10-14)

Status: **proposed**, awaiting the Chair. **§3 and §5 are refined by
`2026-10-14-holon-operant-boundary.md`** (the Rust/Python rule, the ownership table, the
interfaces) and by Operant's `docs/agentic-os/` (module disposition, phases A–I). The rest stands. Nothing in either repository changes behavior until a
phase below is approved. Operant's repository carries a pointer to this record
(`operant/docs/HOLON-INTEGRATION.md`).

## 1. The answer

**Run Holon as a sidecar kernel, and give it native seams inside Operant.** Do not port Holon into
Operant.

- **Holon owns the organization.** That means the work graph, approvals, verification, playbooks,
  objectives, memory promotion, authority and incidents. It stays one Python process family with
  one writer per database.
- **Operant becomes Holon's primary body**, its best harness. It is reached through an adapter
  (`adapters/operant/`, like ZeroClaw's), and it also becomes the Chair's conversational channel
  (its Telegram gateway).
- **Operant gains a small, feature-gated `[holon]` integration** where an in-process hook does
  what an outside kernel cannot:
  - enforce the approved plan on every tool call;
  - record the trajectory into the run directory;
  - turn its own self-learning into proposals the kernel verifies, instead of edits that go live
    unchecked.
- **Operant's own org layer** (`crates/operant-core/src/org/`, ~21.6k lines) stops being a
  second organization. Its useful enforcement parts read Holon's data. Its cast crons are replaced
  by Holon routines. Its stores are kept as an archive and never deleted.

§4 gives the reasons. The short version: an organization that improves its own body needs its
evaluator outside the thing it evaluates. And Holon's first design law (any harness, portable
identity) is what lets the organization outlive any one harness.

## 2. What Operant is (the terrain)

Operant is `~/Documents/github/my-projects/agentic-harness/operant`, a Rust workspace of 17 crates
and about 711k lines (README badge). Version 0.2.1 is installed at `~/.local/bin/operant`, and
`operant-gateway.service` is running. Development is a multi-agent fleet working iteration by
iteration (iter-817 on 2026-10-14), deploying after every push (its `AGENTS.md`).

| Subsystem | Where | State on this workstation |
|---|---|---|
| ReAct agent loop: fallbacks, healing retries, guardrails, tiered context eviction | `operant-core/src/agent/`, `context_management.rs` | live; `operant run -q` works headless (probed: 12 s, exit 0, no TTY needed) |
| 70+ JSON-schema tools; MCP client (stdio/HTTP, deferred loading) and server | `operant-core/src/tools/`, `mcp.rs` | live; stdio MCP children inherit the environment (`mcp.rs:647`), so a run token passes through |
| Skills: one `root_dir`; `skill_manage`; background review (memory and skill nudges, every 10 iterations); curator (usage, archive, consolidation) | `tools/skills_tool.rs`, `agent/background_review.rs`, `curator/`, `cmd_curator.rs` | live; the agent writes skills directly, with no verification |
| Memory: memory-wire in process (BM25 + overlap, two-tier org bank planned) or builtin `MEMORY.md` | `memory_wire.rs`, `memory_provider.rs` | live config uses **builtin** (`~/.operant/MEMORY.md`, 609 KB) |
| Persistent Python kernel and continual-harness lessons (prompt notes, subagent specs, snapshot rollback) | `kernel-sidecar/`, `docs/kernel.md` | built, **off** |
| Harness kernel: hot-swappable providers, `architecture.toml` + patches, reversible effects | `operant-harness/`, `docs/harness-kernel.md` | built, **off** |
| Gateway and channels: Telegram live; 7 gateway platforms, 31 channel features | `gateway/`, `operant-channels/` | live (`telegram_enabled = true`) |
| Cron scheduler: agent and `no_agent` script jobs, deliveries | `cronjobs/`, `cmd_cron.rs` | live: 11 jobs, 594 runs |
| **Org layer** (native port of the legacy organism) | `operant-core/src/org/` (21.6k lines), `cmd_org.rs` | live (see §3) |
| Runtime crate (ZeroClaw-derived: SOP engine, routines, SkillForge, SkillImprover, trust, verifiable intent, sandboxes) | `operant-runtime/` (84k lines) | **mostly dormant**: the CLI uses its daemon signal helper and an observer; `operant sop` only lists TOML files |
| Trajectories (ReAct steps for fine-tuning), advisor (transcript watchdog), checkpoints, MoA | `cmd_trajectory`, `[advisor]`, `[checkpoints]` | trajectories on demand; advisor on |

### 2.1 The org layer, and how it runs the legacy organism

The design is in `docs/ORGANISM-ARCHITECTURE.md` (ratified iter-609),
`ORG-AUTHORITY-ARCHITECTURE.md`, `PERMISSION-SCOPING-PLAN.md` and `WAVE1-DECISIONS.md`. It
collapsed the organism's three swarm pools into one `crew` department and seeded a **cast of nine
standing seats** (`org/cast.rs`), each with a digest cron. Its parts:

- **Identity:** an employee registry, departments, a reporting tree, and a fail-closed identity
  gate on every cron tick.
- **Authority:** per-seat policies (`yolo`/`standard`/`scoped`/`lockdown` with allow and deny
  globs), consulted **in the tool loop**; TTL'd grants; a durable escalation queue a senior
  resolves.
- **Records:** a framework-written **worklog** (one objective row per run) and a subjective log
  plus decision objects with dissent, enforced by a **write barrier** (a run is not complete
  until its rows exist).
- **Communication:** a notice board, DM threads with a three-turn budget, and daily
  "socialization" pairings.
- **Context and budgets:** a mechanical chief-of-staff synthesis into an org memory bank, ranked
  DM/feed context injection, and per-seat token budgets.

Live data, read on 2026-10-14:

| Table | Rows | What it shows |
|---|---|---|
| `employees` | 10 | the nine cast seats and one backfilled cron employee |
| `worklog` | 385 | 192 rows from `unknown`, 96 from `identity-warden`, 56 from `dispatcher`, a handful from the rest |
| `worklog.improvement_proposal` | **0** filled | the column exists; nothing writes or consumes it |
| `cron_jobs` | 11 (10 enabled) | 6 last ran in **error**: 120 s request timeouts, provider streams dying, one interrupted |
| `tasks` (kanban) | 17 | 10 todo, 6 done, 1 blocked |
| `notices` | 23 | |
| `authority_grants` | 1 | |
| `pending_requests` | 0 | |
| `seat_policies` | 1 | |
| `subjective_log` | 385 | |
| `org_decisions` | 1 | |

Also: `[genome] unattended_posture = "yolo"` (unattended runs auto-approve dangerous tools), and
socialization is on.

**Reading.** Operant reproduced the organism's *structure* faithfully: seats, digests,
logs, authority. The records are written, but nothing reads them back to change how the work is
done:
- no work graph with plan, approval, execution and verification;
- no rubric, no gold standard, no independent verifier;
- no playbook or objective loop;
- no lessons promoted by counted recurrence;
- no incident work when the crons fail. Six digests failing on model timeouts is exactly the case
  Holon's heal loop and smoke gate exist for.

The cast does the legacy organism's work. It does not close the legacy organism's loops.

## 3. Overlap: who owns what after integration

| Concern | Holon today | Operant today | Owner after |
|---|---|---|---|
| Departments, employees, roles, hierarchy | `execution/org.py`, genome, six roles | cast, `employee_db`, `hierarchy_edges`, `department_db` | **Holon**; Operant reads it |
| Work: intake, plan, approve, execute, verify, rework, retro | `execution/work.py`, `service.py` | kanban tasks (no stages) | **Holon** |
| Recurring jobs | routines in three lanes, classifier-enforced, playbooks | cron jobs (agent or `no_agent`) | **Holon** decides; Operant's scheduler may *fire* `holon tick` (a `no_agent` job) instead of the systemd timer |
| Authority to approve | grades, earned authority, Chair key | grants, escalation queue, seniors' `/approve` | **Holon** (approval is of plans) |
| Enforcement inside a run | after the fact: fingerprint, held skill writes, checks | **in-loop**: seat policy consulted per tool call | **Operant**, fed by Holon's approved plan (§5, Phase 2a) |
| Verification, rubric, gold standard, judgments | built | none | **Holon** |
| Memory: org, department, employee lessons; promotion | `memory.db`, consolidation, promotion | memory-wire or `MEMORY.md`; org bank planned | **Holon** for org knowledge; Operant's memory stays session-scale |
| Skills | department-layered library, changed only after verification; playbooks versioned and judged | `skill_manage`, nudges, curator: live edits | **Holon** for org skills; Operant's self-authored skills become proposals in a Holon run (Phase 2b) |
| Logs | hash-chained events, runs, outputs, judgments | worklog, subjective log, write barrier | **Holon**; the write-barrier idea is already Holon's result contract |
| Healing | leases, salvage, incidents, doctor, smoke, lint | doctor (one engine), cron retries | **Holon** for the org; `operant doctor` becomes a Holon smoke check |
| Chair interface | Notion mirror, `[notify]` | Telegram gateway, TUI | **both**: Notion stays the dashboard; Telegram becomes the conversational Chair channel (Phase 3) |
| Budgets and cost | `runs.tokens_in/tokens_out/cost` filled from a driver's `usage_line` or the result's `usage`; run-count budgets per department (`[budget]`) | seat token budgets, per-session cost | **Operant** meters and reports usage per run; Holon stores it and budgets by department; token budgets per employee are Operant's seat budgets, keyed by the Holon employee |
| Trajectories | gold standard is the verified *result* | ReAct trajectories on demand | **Operant** records; Holon keeps the verified one as the **golden trajectory** (Phase 2c) |
| Harness self-modification | none | harness kernel (off), persistent-kernel lessons (off), a fleet developing Operant | **Holon governs**; Operant's development becomes a department (Phase 5) |

## 4. Sidecar or native: the reasons

1. **The evaluator must not live inside what it evaluates.** Recursive self-improvement of the
   *harness* means changing Operant (a fleet already ships builds after every push). If the
   organization lives inside the Operant process:
   - a bad build takes down the memory, the authority and the work graph with it;
   - nothing outside the build can measure whether it got better.

   As a sidecar, Holon can run `holon adapter test operant` against a new build. It can record
   which build ran every stage, compare judgments per playbook version *and* per harness build,
   and route employees back to the previous body when a build regresses.
2. **Portable identity is a design law, and it is what makes the organization durable.**
   Employees keep their memory, record and authority across harnesses. Today that is Hermes,
   jcode, ZeroClaw and Claude Code (deferred), and tomorrow whatever is fastest. Natively
   embedding Holon would make every employee an Operant process forever.
3. **The trust boundary needs a separate process.** Holon's isolation keeps the Chair key and the
   databases out of reach of the agent's Unix user; agents reach the organization only through
   the broker. An org layer inside the agent process holds its secrets in the agent's address
   space. Operant's own `unattended_posture = "yolo"` shows the pressure that design is under.
4. **One writer per database.** Operant runs many processes at once (the gateway daemon, TUIs,
   cron runs, fleet agents). Holon's SQLite stores have exactly one writer each, behind the
   kernel. A Rust port would have to re-earn that across Operant's process model.
5. **The kernel evolves fast and evolves itself.** Holon has 9.5k lines of stdlib Python whose
   tests run in seconds. Its changes go through its own pipeline and regression ledger. A port
   into a 711k-line Rust workspace with a fleet's compile-and-deploy cycle would freeze it.
6. **Some things only a native hook can do, and those go native.** Examples: refusing a tool call
   outside the approved plan as it happens, capturing the full trajectory, and intercepting the
   skill nudge. These are small, feature-gated seams in Operant (§5 Phase 2), not a second kernel.

**What native integration would buy, and why it is not worth it now:** lower per-run latency (no
process spawn) and one install. Spawning a run costs seconds; model calls cost minutes (§6). It
can be revisited if Holon's contract stabilizes and Operant becomes the only body. Even then the
better move is Holon as an embedded *library process* (PyO3 or a long-lived socket service), not
a rewrite.

## 5. Implementation plan

Each phase is one or more commits with tests. It lands in Holon's repo (adapter, kernel) or
Operant's (seams, under its iteration protocol), and is approved by the Chair before it starts.

### Phase 0: prerequisites (Chair)

- **Model throughput.** Operant's crons time out at 120 s on free-stack's `small-stack`, and Holon's
  planner took 878 s per call. A faster model for plan and execute stages comes first; integration
  does not fix throughput.
- **Isolation root setup** (`2026-10-13-trust-boundary.md` §3). Operant runs as the agent user
  through the runner like any harness.

### Phase 1: `adapters/operant/` (Holon repo; no Operant change)

Same shape as `adapters/zeroclaw/`:

| Part | Implementation |
|---|---|
| Driver | `operant -c {posture_config} run --no-stream -s {identity} -q {briefing}` at a **pinned** binary path (the installer verifies `operant --version`; the fleet redeploys `~/.local/bin/operant` after every push) |
| Postures | the installer writes one config pair per scope: `holon-<scope>-observe.toml` (write and shell tools in `[tools] disabled_tools`, `disabled_toolsets`) and `holon-<scope>-act.toml`; each sets `[client]` (the model), `[memory] provider = "builtin"` with the employee's own memory dir, and `[genome] unattended_posture = "lockdown"` |
| Native tools | `[[mcp.servers]] name = "holon", transport = "stdio", command = "<repo>/bin/holon", args = ["mcp"]`. `HOLON_RUN_TOKEN` reaches it through environment inheritance (`mcp.rs:647`) |
| Skills | `holon skill export` into a directory set as `[skills] root_dir` of the Holon postures. Operant has a single skills root, so Operant's own pool is linked in, or Phase 2 adds `extra_dirs` |
| Notify | `[notify] command = "operant channel send telegram …"` |
| Conformance | `holon adapter test operant` (5 real runs in a scratch organization) |

**Acceptance:** conformance 5/5; one Tools routine (`Notes digest`) runs on Operant with a judged
and verified result; `holon doctor` is clean. Hire a pilot employee with `--driver operant`.

### Phase 2: native seams in Operant (Operant repo, feature `holon`, off by default)

Active only when `HOLON_RUN_TOKEN` and `HOLON_RUN_DIR` are set:

- **a. Plan enforcement in the loop.** Holon writes `policy.json` into the run directory: the
  approved plan's writes, commands and roots, and the grade. Operant loads it as an ephemeral seat
  policy (`org/seat_policy.rs::decide` is already pure: data in, verdict out) and refuses
  out-of-plan calls *before* they run. Holon's after-the-fact checks stay as the second line.
  - Test: a write outside the plan is refused and lands in the result's breach list.
- **b. Self-learning becomes proposals.** In a Holon run, the background review's skill and memory
  nudges, `skill_manage` writes and curator actions write `result.json` annotations instead of
  files: `skill_changes`, `learnings`, `proposals`. These are what Holon's contract already
  verifies and versions. Outside Holon runs, nothing changes.
  - Test: a nudge in a Holon run leaves `~/.operant/skills` untouched and the proposal in the result.
- **c. Trajectories as evidence.** Holon runs record the trajectory to `$HOLON_RUN_DIR/trajectory.json`.
  Holon stores it with the run. `Work.gold_standard` returns the verified result **and its
  trajectory**, and briefs and the judge receive a compacted form. This is the "golden trajectory"
  made concrete: the steps that last produced a verified result.
  - Test: the gold standard of a routine includes the steps of its last verified run.
- **d. Usage.** Operant reports each run's tokens and cost (a `usage_line` the driver matches,
  or the result's `usage`). Holon already stores them on the run (`Work.set_run_usage`), but no
  harness on the workstation reports them today. With them, cost per verified result becomes a
  counter-metric in `holon metrics`, and Operant's seat budgets can be set per Holon employee.

### Phase 3: the Chair channel (both repos)

- Inbound: the gateway recognizes the Chair's verified Telegram identity and classifies the message
  with Holon's decision models (approve, answer, new work, note). It then calls `holon` **as the
  Chair from the gateway's user**, never the agent user. Everything else stays a normal chat.
- Outbound: `[notify]` lands in the same thread, so approvals, failures and routine deliveries
  arrive where the Chair already talks.
- **Acceptance:** "approve <id>" in Telegram approves a pending plan, logged as a Chair event.
  The agent user cannot send that command (broker allowlist test).

### Phase 4: one organization (both repos)

- **Map the cast onto Holon:**

  | Operant seat | Becomes in Holon |
  |---|---|
  | `premiere`, `chief-of-staff` | the executive department (CEO and staff) |
  | `compass` | the strategy owner, reading the Compass goals Holon already links to |
  | `governor` | the executive audit (`holon audit samples`, doctor triage) |
  | `identity-warden` | a routine in the department that owns identities |
  | `hrmaster`, `crew-chief`, `dispatcher` | kernel mechanisms already: reviews and probation, authority, claims and tick. They are not agents |
- **Replace the cast crons** with Holon routines, through the classifier, cheapest lane first; then
  disable them. Six of them fail today.
- **Archive, never delete:** `operant_kanban.db`, `operant_decisions.db`, worklog and notices stay
  on disk as read-only history.
- Operant's `org` code keeps working for non-Holon chats. In a Holon run it reads identity and
  authority from Holon (`holon whoami` through MCP) instead of its own tables.

### Phase 5: the harness improves under the organization's loop

- **Smoke checks for the body:** `operant --version` at the pinned path, `operant doctor`, and a
  one-turn `operant run -q` as a critical smoke check of the departments that run on Operant.
- **Every run records its harness build** (driver version string), so judgments and verifier
  failures can be grouped by build. A build that lowers the scores of a playbook opens incident
  work. The Chair decides whether drivers pin the last good build.
- **An "Operant" department** (or Tools, with its scope) takes Operant's `TODO.md` and `BUGS.md`
  as intake. The fleet's iterations become work with plans, verification and retros, and the
  department's playbooks hold its protocol: the iteration rules now in Operant's `AGENTS.md`.

### Phase 6: documentation (every phase)

Each phase updates, in the same commit:
- Holon: `docs/STATUS.md`, `AGENTS.md` (map and ledger), `adapters/operant/README.md` (what the
  real harness does);
- Operant: `docs/HOLON-INTEGRATION.md`, its `AGENTS.md` section, `operant.example.toml`.

A fresh agent in either repository finds the other through those pointers.

## 6. Recursive self-improvement: the Chair's list, and what it misses

The Chair named seven factors. Each is below, with how Holon implements it now.

| Factor named by the Chair | In Holon now | Note |
|---|---|---|
| Reusable skills the agent learns | department-layered skills and **playbooks** (protocol plus rubric, versioned, judged per criterion) | skills change **only after verification** (held `skill_changes`, `_hold_direct_skill_writes`). An agent rewriting its own instructions unreviewed is exactly what the live demo caught going wrong (§7) |
| Memory, managed automatically and by the agent | the kernel consolidates structured annotations (learnings, decisions) from every result into employee, department and company scopes; lessons are **promoted by counted recurrence** | agents propose; the kernel decides what is kept. Operant's free-form `MEMORY.md` (609 KB) shows what happens otherwise |
| Lessons (decisions, regression guards) at the architecture level | the kernel's regression ledger (every live defect gets a guard test, checked by lint); decisions as a memory kind; smoke checks and machine checks as guards | **gap:** no per-department regression ledger yet |
| System-level feedback loops | seven loops with named sensors and actuators (INTENT): heal, rework, employee, department, playbook, objective, company | each actuator is *work or memory*, so every change goes through approval |
| Procedural lanes | script, fast, full; classifier-enforced, cheapest first; escape hatch upward | |
| Hierarchy of employee agents, collective intelligence | departments × six roles, heads, CEO, Chair; earned authority; probation; reviews from evidence | |
| A CLI that runs the whole OS | `holon` with an actor class per command; MCP for harnesses; broker for isolated agents | |

**What the list is missing.** Holon already has the first five; the rest are open. Without the
first two, the loop does not improve; it drifts.

1. **Independent verification as the gate on learning.** A loop that learns from its own reports
   of success optimizes for the reports. Holon learns only from verified results:
   - a verifier that did not do the work;
   - machine checks;
   - a decision-model judge against a rubric and a gold standard.

   The live demo is the proof: the refinement's executor claimed a byte-exact match; the verifier
   showed it had dropped the criterion it was meant to add.
2. **Measurement with counter-metrics.** Every improvement claim is a number per criterion per
   playbook version, paired with a counter-metric (cost, rework, verifier failures). Without it,
   "self-improvement" cannot be told apart from change.
3. **Direction: objectives with key results.** Improvement toward what. Objectives decompose into
   routines and work and are reviewed against their key results. The rubric and the job's protocol
   co-evolve as the target sharpens.
4. **Governed self-modification.** The organization changes itself only through its own pipeline:
   approval by grade, a trust boundary, held writes. Self-evolution without this is
   self-corruption with extra steps.
5. **Self-healing sensors:** leases, salvage, incidents, doctor, smoke, lint. Healing needs
   sensors before it needs agents.
6. **Throughput and cost as first-class constraints** (open). Both systems are bottlenecked on
   model latency today. A loop that needs hours per refinement improves slower than the terrain
   changes. Holon stores tokens and cost per run, but no harness reports them yet (Phase 2d).
7. **Evaluation of the harness itself** (open; Phase 5). Today the organization evaluates its work
   but not its bodies.
8. **Retention.** Events and run directories grow without bound. Operant's 609 KB `MEMORY.md` and
   81 MB `database.db` show the same pressure (open; not urgent).

**Where the Chair's picture is behind the build:**
- skills, memory and lessons are *verified, versioned and counted* in Holon, not learned
  automatically;
- the loops have explicit sensors and actuators;
- the gold standard is concrete: the last verified result, and with Phase 2c its trajectory.

**Where the build is behind the picture:** per-department regression ledgers, declared services
between departments, reported cost, and the harness loop.

## 7. The live demonstration, and what it says about this plan

The playbook demo on the workstation (Tools department, routine "Notes digest", Hermes on
free-stack; `2026-10-14-playbooks-objectives-smoke.md` §4) ran two judged and verified runs. It
opened a refinement from the evidence, and its verifier failed an executor's false claim. The replan
never finished: three hours of plan attempts on free-stack, and the playbook is still v1.

The loops work. The model throughput is what limits them. That is why Phase 0 comes first, and why
integration is not expected to speed anything up by itself.

## 8. Risks

- **Operant moves fast.** The fleet deploys after every push. Pin the binary in the driver and
  keep its smoke check critical.
- **Two org layers drift** until Phase 4. Until then Holon is authoritative for any work it holds,
  and Operant's cast crons run only what they run today.
- **`unattended_posture = "yolo"`** applies to Operant's own crons today. The Holon postures set
  `lockdown`; the Chair decides Operant's global posture.
- **Telegram Chair commands are a new authority path.** Phase 3 needs the broker-allowlist test and
  a verified-identity check before it ships.

## 9. Decisions for the Chair

1. Sidecar kernel with native seams (this record), or a native port.
2. Phase order. The proposal is 0, then 1, then 2a to 2d, then 3, then 4, then 5.
3. Operant's global `unattended_posture` (now `yolo`).
4. Whether Operant becomes the production body on the workstation (Hermes today) after Phase 1's
   conformance.
