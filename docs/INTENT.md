# Intent

**An agentic operating system that runs as an organization**: a self-healing, self-evolving
company of persistent AI employees, organized in departments under heads of department (HODs),
led by a CEO, governed by a charter, and supervised by a human Chair. Every task leaves the
company smarter than before: knowledge accumulates in employees, departments, and the company,
and the feedback loops between them produce intelligence no single agent has, which in turn
evolves the operating system itself.

It is **not** a task runner that hires anonymous contractor agents that do a job and vanish.
Contractor-style agents add nothing up. Employees do: they keep an identity, a job description,
a memory, a record, colleagues, and a manager.

## The organization

```
CHAIR (the human) — sets the charter, approves what the charter reserves for the Chair, steers priorities
  └── CEO (executive)            — turns the Chair's objectives into department mandates; arbitrates across departments
        ├── HOD, Department A    — plans and splits department work, assigns it to employees, reviews them, hires and fires
        │     ├── employee
        │     └── employee  ←→  employees talk to each other: DMs, department channels, cross-department requests
        ├── HOD, Department B
        └── …
  Executive offices: Charter (policies + controls) · Audit (independent sensing of the whole company)
```

Every department, including the shared-service departments (strategy, CRM, marketing,
research, IT…), is a recursive copy of the same pattern: a head, employees, a mission,
service agreements, a memory, and a status report.

## Every org word is a mechanism (no hollow role-play)

| Term | What it is in the system |
|---|---|
| Chair | the human principal: final approval authority; edits the charter |
| CEO | a persistent employee heading the executive; decomposes Chair objectives into department tasks |
| Department | a row with mission, head, parent, service agreements; owns a memory scope `dept:<id>` and a status report |
| HOD | an employee who heads a department: plans and splits its work, assigns tasks to its employees, approves employee plans **within the authority the charter delegates**, proposes hires and fires |
| Employee | a persistent identity: job description, skills, harness driver, manager (`reports_to`), memory scope `emp:<id>`, worklog, performance record |
| Job description | the employee's role spec: responsibilities, task classes, skills, KPIs. Injected into every brief they receive |
| Hire | a roster entry created from a job description, with a reason and a probation period; approval per charter |
| Fire | retirement with evidence from the performance record; the employee's memory is kept and handed to its successor |
| Delegation | a task assigned down the chain (Chair → CEO → HOD → employee) carrying its mandate; the result rolls back up |
| Interaction | messages: direct, department channel, cross-department request (a request that needs work becomes a task for that department) |
| Review | computed from evidence (verified results, approval-first-try rate, rework, cost), then judged by the manager. Not written from vibes |
| Collective intelligence | memory scopes `emp → dept → org` (plus `project` and `class`) with **promotion**: a lesson that recurs across employees is promoted to the department; one that recurs across departments is promoted to the company |
| Self-healing | leases, retries, stale-plan detection, health checks; failures automatically become incident tasks for IT/Operations |
| Self-evolving | reviews change job descriptions and staffing; audit findings change policies and controls; everything flows through the same plan → approve → execute → verify pipeline |
| Emergence | promoted lessons + audit's cross-department pattern detection, fed back into briefs, policies, and staffing |

## The four feedback loops

| Loop | Who | Cadence | Reads | Changes |
|---|---|---|---|---|
| Retro | each employee | after every task | its own result, verifier verdict, reviewer notes | its memory; proposals |
| Department review | HOD | weekly | the department's results, worklogs, proposals, promoted lessons | assignments, job descriptions, hire/fire proposals, department memory |
| Company review | CEO | monthly | department reports, audit findings, goal progress | mandates, priorities, cross-department fixes |
| Charter review | Chair | quarterly | audit's fitness metrics, the control/policy ledger | the charter: policies, controls, delegated authority |

## Design laws that keep it real

1. **Harness-agnostic.** Employees can run on any harness (ZeroClaw, Claude Code, operant…). The OS never depends on one; harnesses plug in as plugins.
2. **Persistent identity, portable body.** An employee's identity and memory belong to the OS, not the harness, so an employee can move between harnesses and keep its career.
3. **Every org word maps to data and a mechanism** (the table above). A term with no mechanism is deleted.
4. **Authority is explicit.** What the Chair, CEO and each HOD may approve is written in the charter and enforced by the pipeline.
5. **Learning is measured, not narrated.** Promotion, reviews and audit use counted evidence with counter-metrics, so the organization cannot score itself into fiction.
6. **The OS improves itself through its own pipeline**, under the same approvals as any other work.
