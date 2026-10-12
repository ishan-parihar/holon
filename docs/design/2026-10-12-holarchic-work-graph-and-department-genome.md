# Design: the holarchic work graph and the department genome

Status: accepted (2026-10-12), being implemented. Genome, skills and autonomy shipped first (docs/GENOME.md); the work graph (G1) is next. Replaces the withdrawn "Notice Board of actions" draft, which
split work into two queues (tasks and actions). Here there is **one** work graph.

## 0. Principles

1. **One work entity.** Everything anyone does is a task node. No actions, no separate plan or
   result entities with their own lifecycles: a plan is the output of a planning task, a
   verdict is the output of a verification task.
2. **Two relations, no more.** `parent_id` (containment: the holarchy) and `needs` (order and
   dependency: the pipeline). Every long-horizon structure is built from those two.
3. **Every department is the same holon.** One genome (roles + pipeline + authority + metrics)
   instantiated per department, with declared overrides. The executive is a department too.
4. **The pipeline is data, not code.** Which stages a piece of work goes through is decided by
   genome rules evaluated per node. The organization evolves by changing that data through its
   own pipeline.
5. **Every loop has a sensor, a comparator, and an actuator that is a task.** Learning that
   does not end as a task or a memory item does not exist.

## 1. The work graph

```
ROOT  "Retire the organism repo's dead code"            (project; memory scope project:<id>)
 ├─ research   Map dead modules and their callers        → terrain written to dept KB
 ├─ shape      HOD turns terrain into scoped work items   needs: research
 ├─ WORK  "Delete lib/arch_staff.py"                      needs: shape     (a holon: has its own pipeline)
 │    ├─ plan      → output: plan + grade                 gate: approval by grade
 │    ├─ execute   needs: plan                             → output: result + documentation
 │    └─ verify    needs: execute                          → output: verdict
 └─ WORK  "Fix arch validate hang"                        needs: shape
      ├─ plan  → output kind=decompose                    (too big: becomes a container)
      ├─ WORK "Profile validate"    ── dept: IT ──        (a child may belong to another department)
      └─ WORK "Patch the slow rule" needs: "Profile validate"
```

- A **work node** (container) has children; a **stage node** (leaf) has a `stage` and is done
  by exactly one role. A container's status is derived from its children; nobody claims it.
- **Pipeline = a chain of sibling stage nodes linked by `needs`.** A node becomes `ready`
  when all of its `needs` are done. Outputs flow along `needs`: a stage's context bundle
  includes the outputs of what it needs (the planner sees the terrain and the HOD's shaping).
- **Decomposition is recursion.** A plan whose output is `decompose` turns its work node into
  a container: it creates child work nodes (any department, with `needs` between them), each
  expanded by its own department's pipeline. Depth is unlimited; that is the holarchy.
- **Cross-department work is just a child in another department.** A request from Charter to IT
  is a work node with `dept=IT` under the requester's node; the requester's next stage `needs`
  it. No separate request mechanism.
- **History is append-only.** Rework adds new stage nodes (`execute#2`, `verify#2`), and a
  replan adds `plan#2` that supersedes `plan#1`. Nothing is overwritten, so audit and learning
  can always see what happened.

### Schema (work.db; replaces `plans`, `results`, and the per-task state machine)

```sql
tasks (                                   -- every node
  id, root_id, parent_id, depth,          -- holarchy
  dept_id, role, stage,                   -- stage NULL ⇒ work node (container)
  title, intent, acceptance, task_class, project_ref, workdir,
  priority, risk, grade, grade_reasons,   -- grade lives on the plan stage; inherited by its successors
  status,                                 -- blocked|ready|claimed|gated|done|failed|cancelled|needs_input
  assignee, claimed_by, run_id, lease_until, attempt,
  gate_approver, gate_decided_by, gate_note,  -- approval is a gate on a stage, not an entity
  supersedes, source, requested_by, due, created_at, updated_at, ext_id, sync_dirty)
task_needs (task_id, needs_id)            -- the DAG; ready = all needs done
outputs (id, task_id, run_id, kind, envelope_json, documentation, created_at)  -- one per submission
runs, events, messages, schedules, departments, employees, authority, audit_samples  -- kept
```

Status set per node is small and uniform: `blocked → ready → claimed → (gated) → done`,
plus `failed`, `needs_input`, `cancelled`. The ten-state task machine disappears because each
stage is its own node. `claim(employee)` becomes one query:
*ready stage nodes whose role the employee holds, in its department (or assigned to it),
ordered by priority and age*, respecting separation rules.

## 2. The department genome: roles

Six roles, one per cybernetic function (Viable System Model in brackets). Roles are **slots**;
employees fill one or more. A department needs at least three employees to keep the
separation rules.

| Role | Function | Stages it performs | VSM |
|---|---|---|---|
| **Head** | Owns the department's outcomes. Shapes incoming work, approves within authority, assigns, staffs (hire/fire), runs the department review | shape, approve (gate), review | S3 control |
| **Researcher** | Maps the terrain: what exists, what is known, what is risky. Writes **terrain** to the department knowledge base so it is reused, not re-discovered | research | S4 intelligence |
| **Planner** | Turns shaped work + terrain into a plan or a decomposition, with a grade | plan | S3 → S1 |
| **Executor** | Does the approved plan; documents it | execute | S1 operations |
| **Verifier** | Independently checks the result against acceptance criteria and the plan | verify | S3* audit |
| **Steward** | Keeps the department healthy and learning: curates memory and the KB, runs retros on failures and rework, maintains the department's playbooks and genome, watches backlog and cycle times, opens incident and improvement work | retro, curate, incident, genome-change | S2 coordination + learning |

Separation rules (enforced at claim time):
- a plan is never approved by its planner; a result is never verified by its executor;
- a steward never approves a genome change it proposed;
- the head may hold any role except verifier of work it executed.

Minimum department (3): **Head** (+ Steward) · **Maker** (Planner + Executor) ·
**Checker** (Researcher + Verifier). It grows by adding employees to the busiest role, then
by splitting into sub-departments (each a full copy of the genome) when the Head's span exceeds
the genome limit (default 7 direct reports).

### The executive is the same genome, one level up

| Executive role | Holder | Does |
|---|---|---|
| Head | CEO | shapes Chair objectives, approves within delegated authority, arbitrates across departments |
| Researcher | Strategy | company-level terrain: goals (trajectory.db), evidence (logbook.db), department reports |
| Planner | Portfolio planner (CEO may hold it) | decomposes objectives into department work nodes |
| Verifier | **Audit** | samples approvals and results across departments; holdout checks for the Chair |
| Steward | **Charter** | maintains the org-wide genome, policies and controls; promotes cross-department lessons |
| Executor | — | the executive does not execute; its "execution" is delegation to departments |

The **Chair** sits above as S5 identity: edits the charter, approves what the charter reserves,
and is the final approver for genome changes that affect every department.

## 3. The department genome: pipeline

The base pipeline every department inherits:

```
intake → research? → shape? → plan → [gate] → execute → verify → (retro?)
```

Stages marked `?` are **conditional**, decided per work node by genome rules when the node is
expanded. Start rules (each is evidence the steward can later tune):

| Stage | Runs when | Skipped when |
|---|---|---|
| research | task class has fewer than 3 verified successes; risk ≥ high; or the head asks | the KB holds fresh terrain for this class/project (age < genome TTL) |
| shape | source is the Chair or another department and the node is unshaped | the node came from a parent plan's decomposition (already shaped) or a recurring template |
| plan | always | a recurring task instantiated from an approved template |
| gate | grade > the planner's own authority | grade ≤ earned authority of the decider (G0 work by trusted planners) |
| execute | always (except pure research/analysis work, whose output *is* the plan stage's) | — |
| verify | always for G≥1 | G0 with a track record above the genome threshold, then **sampled** (default 20%) |
| retro | verify failed, attempt > 1, the gate rejected twice, or a root project closed | otherwise (learnings ride on every run's envelope anyway) |

Departments override by declaring a diff, e.g. Research: `execute: skip` (its product is
terrain); IT: `verify: always` (infrastructure); Pilot ventures: `gate: chair for external
effects`. Overrides are recorded and visible; they never fork the base.

### Gate routing (unchanged mechanism, now on the plan stage)

Effective grade = max(planner grade, objective floor). The gate routes to the lowest of
**Head → CEO → Chair** whose authority covers it. Earned authority still moves with verified
outcomes and audit disagreement. This is what keeps approvals from piling up: as a
department's record grows, its Head and then its planners clear more of their own work.

## 4. Knowledge: one place per kind of thing

| Store | Holds | Written by |
|---|---|---|
| work.db | the graph, outputs, runs, events, messages, schedules, org, authority | the kernel |
| memory.db | **the knowledge base**: terrain (research output), lessons, constraints, decisions, preferences, project state; scopes `emp → dept → org`, `project`, `class` | consolidation after each run; steward curation; promotion |
| departments.genome + holon/genome/*.toml | each department's profile (slots and roles), snapshotted on the department row; changes are events. Skills (playbooks) resolve dept → instance → stdlib | the Chair (profile), the steward via approved tasks (skills) |
| trajectory.db | goals (why) | existing system, read-only to Holon |
| logbook.db | human evidence | existing system, read-only to Holon |

Research output is not a separate document store: terrain is a memory item kind (`terrain`)
with sources, freshness and scope, so it shows up in every later bundle for that class or
project, and staleness is computable.

## 5. The feedback loops (self-healing and self-evolving)

| Loop | Sensor | Comparator | Actuator (always a task or memory write) | Owner |
|---|---|---|---|---|
| **L0 Heal** (minutes) | leases, heartbeats, driver exit codes, stale writes fingerprints | lease expired; same node failed twice | release and re-ready the node; second failure → incident work node for IT | kernel |
| **L1 Rework** (per node) | verifier verdict, gate decision | acceptance met? plan sound? | `execute#n` / `plan#n` appended with the feedback in its bundle; attempts > max → escalate to Head | pipeline |
| **L2 Employee** (per run) | run envelope: learnings, decisions, blockers, cost | verdict vs self-report | memory under `emp:`; performance record; earned authority up or down | consolidation |
| **L3 Department** (weekly + on triggers) | first-pass verify rate, rework rate, gate latency, cycle time per stage, backlog age, research reuse rate, cost per verified result | genome KPI targets, with counter-metrics | Steward opens **retro** and **genome-change** work: edit a playbook, a pipeline rule, a JD; Head hires/fires/re-assigns | Steward + Head |
| **L4 Company** (monthly) | department reports, Audit samples, cross-department lesson recurrence | charter targets | Charter promotes lessons to `org`; proposes base-genome changes; CEO re-mandates | Charter + CEO, Chair approves |

Examples of the system improving itself through these loops:
- research stage on class `charter-cleanup` was skipped 8 times with no rework → no change;
  it ran 5 times and the plan cited the terrain each time → keep. A stage that never changes
  outcomes is proposed for removal (Goodhart guard: measured by rework, not by speed).
- `verify` fails cluster on "missed caller in another repo" → steward adds a playbook step to
  the planner's role playbook and a terrain query; the next 10 plans are watched.
- gate latency at the Chair > 48h for G2 in a department with 15 clean results → Head's
  authority proposal moves G2 to the Head, sampled by Audit.

Every change in L3/L4 is itself a work node, graded and gated like any other, and its effect
is measured by the same metrics afterwards (double-loop learning with a check).

## 6. Context injection (carried over, mapped to roles)

Spawn = identity + protocol; the rest is pulled.

| Layer | Content | Delivered by |
|---|---|---|
| L0 | what Holon is, laws, authority ladder, how to call `holon` | system prompt (static) |
| L1 | identity card: name, roles, department, manager, working style, authority, record | system prompt via the harness's identity mechanism |
| L2 | role playbooks (one per stage the employee performs), from the department genome | skill `holon-employee` with `references/roles/*.md`; `holon skill show` for skill-less harnesses |
| L3 | desk: ready nodes I can claim, messages, steering memory | `holon desk` |
| L4 | the node's bundle: its work node, ancestry, outputs of its `needs`, terrain, memory, lineage | `holon claim` / `holon node open` |

Playbooks are genome data, so the steward's playbook edits reach every employee's next spawn
without touching harness configuration. Harness adapters (ZeroClaw IDENTITY.md/SOUL.md +
skill bundle; Hermes `-s holon-employee` + identity-prefixed query) are unchanged from the
previous draft.

## 7. Scaling to complex systems

- **Depth:** unlimited nesting; `root_id` + `depth` make subtree queries and rollups O(index).
- **Breadth:** role pools inside a department; departments split into sub-departments by the
  span-of-control rule, each a genome instance with inherited overrides.
- **Throughput:** readiness is computed on completion (dependents of the finished node),
  not by scanning; claims are one indexed query under `BEGIN IMMEDIATE`; WIP limits per
  role prevent one department flooding the harness.
- **Long horizon:** each root is a project with its own memory scope, project state and
  budget (cost/time) that rolls up from its subtree; stalled subtrees surface in the Head's
  report by backlog age.
- **Load:** SQLite WAL is sufficient for thousands of nodes per day on one host; archiving
  closed roots older than N days keeps the hot set small. A server DB is a later adapter,
  not a redesign.

## 8. Migration from the current kernel

| Today | Becomes |
|---|---|
| task + 10-state machine | work node + stage nodes with 6 statuses |
| `plans` (versions, grade, approver) | plan stage nodes (`supersedes` for versions) + `outputs`; gate fields on the node |
| `results` | `outputs` of execute/verify nodes |
| `claim_for_planning/execution/verification/review` | one `claim(employee)` |
| `decompose` creating children | the same, now the only way containers form |
| duties (plan/execute/verify/review) | roles (6) from the genome |
| `schedules` kind plan/execute/… | schedules that instantiate templates (subtrees) |
| templates/*.md | role playbooks in the genome, shipped via the skill |

Phases (each ships with the test suite green):
1. **G1 Graph core.** Migration: `task_needs`, `outputs`, stage/role/gate columns; one-off
   conversion of existing tasks/plans/results; `claim(employee)`; readiness propagation.
2. **G2 Genome v1.** genome.db with the base template (roles, pipeline rules, KPIs); expansion of
   work nodes into stage chains; department overrides; role assignment on employees.
3. **G3 Research + shape stages.** Terrain memory kind with freshness; Head shaping playbook.
4. **G4 Injection.** Identity card, role playbooks via skill, `holon desk`; ZeroClaw and Hermes
   adapters.
5. **G5 Loops L3/L4.** Metrics views, Steward retro and genome-change work, Audit sampling of
   skipped stages, span-of-control split proposals.
6. **G6 Dashboard.** Notion mirror of the graph: tree view per root, Chair gate queue,
   department health.

Wave 0 tasks on the VPS are migrated in G1 rather than run twice; the one plan awaiting the
Chair can still be approved now and is converted with the rest.

## 9. Decisions (Chair, 2026-10-12)

1. The six standard roles are accepted. The executive's Audit and Charter are its Verifier and Steward.
2. Light systems use the 3-slot `light` profile. Every new system is configured for its
   requirements: choose a profile, then tailor each slot's job description and skills.
3. Trusted G0 work is verified on a sample: 20% by default, configurable in `[autonomy]`.
4. Widening authority needs an approval. Safe defaults (`supervised`) can be raised per
   department to `delegated` or `autonomous` once trust is established.
5. A standard skills library operationalizes the OS, managed and invoked through `holon skill`.
