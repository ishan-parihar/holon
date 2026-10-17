# Intent

Holon is an agentic operating system that runs as an organization: a self-healing,
self-evolving company of persistent AI employees, organized in departments under heads, led by a
CEO, governed by a charter and supervised by a human Chair. Every piece of work leaves the
company smarter than before. Knowledge accumulates in employees, departments and the company,
and the feedback loops between them produce intelligence no single agent has, which in turn
evolves the operating system itself.

It is **not** a task runner that hires anonymous contractor agents that do a job and vanish.
Contractors add nothing up. Employees do: they keep an identity, roles, a memory, a record,
colleagues and a manager.

## The organization

```
CHAIR (the human) — sets the charter, approves what it reserves, steers priorities
  └── CEO (executive office: strategy, audit, charter)
        ├── Head, department A — shapes and approves its work, staffs it, reviews it
        │     ├── employee (researcher, planner, executor, verifier, steward slots)
        │     └── employee  ←→  colleagues message each other and ask other departments for work
        └── Head, department B …
```

Every department, the executive included, is a copy of the same genome (docs/GENOME.md):
six roles, a profile of slots, a pipeline, tailored skills, and an autonomy policy.

## Every word is a mechanism

| Term | What it is | Mechanism |
|---|---|---|
| Chair | the human principal; final approver; owns the charter and the Chair key | `holon init`, `holon plan approve` |
| CEO | the head of the executive department; plans and approves company-level work | `execution.org.Org.ceo` |
| Department | mission, head, profile, memory scope `dept:<slug>` | `holon dept create` |
| Head | owns a department's outcomes: shapes work, approves within authority, staffs | `execution.work.Work.submit_shape` |
| Employee | persistent identity: roles, job description, manager, memory `emp:<id>`, record | `holon hr hire`, `holon emp card` |
| Role | one of six functions, each performing pipeline stages | `core.vocab.ROLE_STAGES` |
| Work | a node in the holarchy: a pipeline of stages and/or child work | `execution.work.Work.add_work` |
| Delegation | a plan's decomposition into child work in any department, with order | `execution.work.Work.decide` |
| Interaction | messages and cross-department requests that become work there | `holon msg send`, `holon msg request` |
| Review | an employee's record computed from evidence, not narrative | `holon hr review` |
| Earned authority | approval rights that grow with verified results and shrink on failures | `execution.org.Org.earned_authority_change` |
| Probation | a new hire's period, ended by a confirmation their head decides | `execution.service.Kernel.triggers` |
| Collective intelligence | lessons promoted from employees to departments to the company | `capabilities.memory.Memory.promote_recurring` |
| Self-healing | leases, retries, stale-plan detection, incident work when runs keep dying or failing | `holon tick` |
| Self-evolving | retros whose actions become work that changes skills, staffing and the genome | `execution.work.Work.submit_retro`, `execution.service.Kernel.apply_skill_changes` |
| Integrity | artifact rubric, live-state checks, and machine checks on every result | `holon lint`, `holon doctor`, `execution.service.Kernel.run_checks` |
| Lane | how work runs, cheapest first: a script the kernel runs, one fast session for routine work, or the full pipeline | `execution.work.Work.fast_track`, `execution.service.Kernel.run_script` |
| Routine | a recurring job (a cron job) in the cheapest lane that does it reliably, approved once | `holon routine add`, `execution.work.Work.instantiate_recurring` |
| Rubric | what each result of a job is judged on, by a decision model before any verifier is spent | `execution.service.Kernel.judge` |
| Gold standard | the last verified result of the same job: shown to its executor and its judge | `execution.work.Work.gold_standard` |
| Playbook | a job's protocol and rubric as a versioned department skill, refined from its own evidence after verification | `capabilities.playbooks.read`, `holon playbook show` |
| Objective | an outcome pursued over time: decomposed into work and routines, reviewed against its key results until met | `holon objective add`, `execution.service.Kernel._objective_reviews` |
| Smoke check | a command proving the infrastructure a department runs on works; a critical failure stops its agents | `holon smoke`, `execution.service.Kernel.smoke_blocked` |
| Trust boundary | agents run as their own Unix user and reach the organization only through the broker | `execution.isolation.serve_broker`, `holon isolation plan` |
| Decision model | a fast classifier with calibrated probabilities for lane choice, judging, triage and routing | `capabilities.decisions.Decider`, `holon classify` |
| Scope fence | which memory scopes an employee writes directly: own emp:/dept:, a class their department performs, their own project scopes; steering kinds in shared scopes are consolidation-or-Chair-only | `holon memory add` (employee fence), `capabilities.memory.Memory.promote_recurring` |
| Joined recall | one deterministic answer fusing memory, episodes, skills, playbooks and verified results — the pull-mode counterpart of the context bundle | `holon recall`, `capabilities.skills.Library.search` |
| Lineage | an item's provenance graph: supersedes chain, derived-from sources, links | `holon memory show` |
| Law surface | the distribution's own law docs, served read-only to every agent (distilled copies stay checked by lint) | `holon law show`, `integrity.lint.pack_drift` |

## The feedback loops

| Loop | Sensor | Actuator (always work or memory) | Mechanism |
|---|---|---|---|
| Heal (minutes) | leases, driver exits, schedule failures | stage back on the board; incident work for IT | `holon tick` |
| Rework (per work) | verdicts, decisions | `execute#n` or `plan#n` with the feedback in its context | `execution.work.Work.verify` |
| Employee (per run) | every stage's learnings and decisions | memory; record; earned authority | `capabilities.memory.Memory.consolidate` |
| Department (on failure, and periodically) | rework, failures, colleagues' proposals, metrics | a retro whose actions become department work | `holon metrics` |
| Playbook (per job, after verified runs) | judgments per criterion, verifier failures, judge/verifier disagreements | refinement work that rewrites the protocol and rubric; the next version is judged | `execution.service.Kernel._playbook_refinements` |
| Objective (per cycle of its pool) | the pool's results against the key results | new work, new routines, refinement; closing the objective | `execution.service.Kernel._objective_reviews` |
| Company (on request) | department metrics, audit samples, promoted lessons | genome and charter changes, approved like any work | `holon audit samples` |

## Design laws

1. **Harness-agnostic.** Employees run on any harness; the kernel names none, and harnesses plug
   in through adapters that pass `holon adapter test`.
2. **Persistent identity, portable body.** Identity and memory belong to the organization, so an
   employee can change harness and keep its career.
3. **Every word is a mechanism** (the tables above, checked by `holon lint`). A term with no
   mechanism is deleted or marked planned.
4. **Authority is explicit and authenticated.** What the Chair, the CEO and each head may approve
   is data, enforced by the pipeline, and every CLI call is authenticated.
5. **Learning is measured, not narrated.** Promotion, reviews and retros use counted evidence,
   each metric paired with a counter-metric.
6. **The organization changes itself only through its own pipeline**, under the same approvals as
   any other work.
