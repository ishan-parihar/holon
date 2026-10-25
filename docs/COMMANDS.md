# Commands

Every command the distribution registers, generated from the registry (`holon help` prints
the same table; regenerate with `python3 -m holon.tools.commands`). A command that exists and
is not in this file fails `holon lint` — so this file can never drift from the code.

**Actor classes** (law 8): every command declares who may call it.

| Class | Who |
|---|---|
| `chair` | the Chair (a `--token`, or the Chair key on a trusted path) |
| `employee` | an employee, with their own key or a run token |
| `intake` | a department's intake key: it may file work into that department, nothing else |
| `any` | anyone holding any credential, plus the kernel calling itself |
| `kernel` | the heartbeat's own calls, never a person or an agent |

## getting started

*what the organization is, who works in it, and who you are to it.*

| Command | Actor | What it does |
|---|---|---|
| `holon org` | any | the org chart |
| `holon hr activate` `--reason` <employee> | chair | activate an employee |
| `holon hr fire` `--reason` `--successor` <employee> | chair | retire an employee; memory passes to the successor |
| `holon hr hire` `--name` `--title` `--dept` `--jd` `--slot` `--roles` `--driver` `--reason` `--head` `--reports-to` `--task-classes` `--skills` `--max-concurrency` `--status` | chair | hire an employee into a slot (or roles) of a department |
| `holon hr key` `--revoke` <employee> | chair | issue an employee key for pull-mode sessions (replaces any previous key) |
| `holon hr pause` `--reason` <employee> | chair | pause an employee |
| `holon hr reassign` `--slot` `--roles` `--reason` <employee> | chair | change an employee's roles: the slots it now fills, or explicit roles |
| `holon hr review` <employee> | any | an employee's record, computed from evidence |
| `holon hr set-driver` `--driver` `--reason` <employee> | chair | rebind an employee to another harness driver: they keep their record, authority and memory (see `holon drivers`) |
| `holon emp card` <employee> | any | the identity card a harness injects into an employee's system prompt |
| `holon emp list` `--dept` `--all` | any | employees |
| `holon emp show` <employee> | any | one employee |
| `holon intake key` `--floor` `--task-class` `--revoke` <dept> | chair | issue (or revoke) a department's intake key: the Chair-key-free external intake, scoped to one department, one grade floor and one set of task classes |
| `holon intake list` | chair | each department's intake key: its floor and its task classes, never the key itself |
| `holon whoami` | any | who you are authenticated as, and the run you are in |
| `holon chair-channels` | any | the Chair's channel refs ([chair] channels) — a runtime that fronts a channel runs Chair commands only for these identities (H8) |
| `holon drivers` | any | available harness drivers |

## the work graph

*work flows through a fixed pipeline; these move it.*

| Command | Actor | What it does |
|---|---|---|
| `holon task add` `--intent` `--acceptance` `--project` `--parent` `--after` `--dept` `--assign` `--priority` `--task-class` `--risk` `--workdir` `--due` `--max-attempts` `--lane` `--intake-key` <title> | intake | add work (a root, or nested under --parent). The Chair acts directly; an intake key (G4) files into its own department only, at its floor |
| `holon task answer` <task_id> <text> | chair | answer a planner's question |
| `holon task cancel` `--note` <task_id> | chair | cancel work and its open stages and children |
| `holon task list` `--status` `--project` `--dept` `--assignee` `--stage` `--stages` `--limit` | any | work nodes (or stages with --stages) |
| `holon task replan` `--note` <task_id> | chair | queue a fresh plan stage |
| `holon task retry` <task_id> | chair | give failed work another execution attempt |
| `holon task show` <task_id> | any | one work node: its pipeline, outputs, children and conversation |
| `holon task supersede` `--note` <task_id> | chair | withdraw a finished result whose record no longer resolves (the remedy doctor's artifacts.dangling names: the work's history stays, its standing changes, its artifact pins are retired) |
| `holon task tree` <task_id> | any | the holarchy under a work node |
| `holon plan approve` `--note` `--recurring` <ref> | chair | approve a plan (approval, plan or work id) |
| `holon plan grade` `--note` <ref> <grade> | chair | regrade a pending plan (reroutes it) |
| `holon plan queue` `--approver` | any | plans awaiting approval |
| `holon plan reject` `--note` <ref> | chair | reject a plan with what must change |
| `holon plan show` <ref> | any | a plan awaiting (or past) approval: by approval, plan or work id |
| `holon claim` `--as` `--stage` `--driver` `--task` | employee | claim the next ready stage you may do (exit 3 if none) |
| `holon submit` `--file` <run_id> | employee | hand in a run's result (result.json, or --file) |
| `holon run finish` `--exit` <run_id> | kernel | a supervisor hands a finished body in: salvage result.json, submit it, run the checks and the judge (CONTRACT §12) |
| `holon run heartbeat` <run_id> | employee | extend a run's lease on long work |
| `holon run show` <run_id> | any | a run and its stage |
| `holon run-script` <stage_id> | kernel | run a ready script-lane stage (the heartbeat spawns this) |
| `holon verify` `--note` <task_id> <verdict> | chair | verify a result yourself |
| `holon dispatch` `--as` `--stage` `--task` `--schedule` `--prepare` `--runs-as-agent-user` | kernel | claim a stage for an employee and run its harness driver |
| `holon post-execute` <work_id> | kernel | run a handed-in result's machine checks and rubric judge (the heartbeat spawns this) |
| `holon audit judge` `--note` <ref> <verdict> | chair | re-judge a sampled approval |
| `holon audit samples` `--all` | any | employee approvals sampled for the Chair to re-judge |

## a system (department)

*a department is one holon: a mission, staff, reach and checks.*

| Command | Actor | What it does |
|---|---|---|
| `holon dept create` `--name` `--mission` `--parent` `--profile` <slug> | chair | create a department from a bylaws profile |
| `holon dept export` `--to` <slug> | chair | write a department's archive: its profile, its people (never their keys), its reach, its checks, its secret PATHS, its routines and its knowledge bases (G12) |
| `holon dept import` <archive> | chair | create a department from an archive (G12). The archive's checksums are verified first: a tampered archive is refused, and nothing is created. Every routine lands unapproved: an import is not an approval |
| `holon dept list` | any | departments |
| `holon dept profile` `--reason` <slug> <profile> | chair | change a department's bylaws profile |
| `holon dept profiles` | any | available bylaws profiles |
| `holon dept roles` <slug> | any | the department's role slots and who fills them |
| `holon venture import` `--dept` `--apply` <path> | chair | read a venture's four artifacts (_org.yaml, cron-jobs/, smoke.yaml, scripts/) and make them a department. A dry run unless --apply; every routine lands unapproved, because an import is not an approval |
| `holon bylaws draft` `--charter` `--service` `--command` `--accepts` `--returns` `--note` `--args` `--health` `--pool-of` `--owner` <slug> | employee | propose a unit manifest from a capability your department keeps running ad hoc (A10): it lands in bylaws/drafts/ and changes nothing until the Chair promotes it |
| `holon bylaws emit` | chair | derive bylaws/registry.md from the synced units (U3): the unit tree, the services each offers, and the consumption graph |
| `holon bylaws promote` <slug> | chair | move a draft unit into bylaws/units/ after validation (A10): the ruling that makes a proposed capability part of the organization |
| `holon bylaws sync` | chair | regenerate the units tables from $HOLON_HOME/bylaws/units/*.toml (the only writer; refuses on a manifest that does not parse) |
| `holon scope` `--dept` | any | what each department's employees may reach beyond their work's workdir ([scope]) |
| `holon smoke` `--dept` `--cached` | chair | run the smoke checks: the infrastructure each department runs on ([smoke]); a critical failure blocks that department's agents |

## knowledge

*what the organization has learned, and the rules it operates from.*

| Command | Actor | What it does |
|---|---|---|
| `holon kb blueprint check` <name> | employee | genome blueprint check on a draft or a catalog blueprint |
| `holon kb blueprint draft` `--base` <name> | employee | draft a blueprint from a base; it seeds nothing until the Chair promotes it |
| `holon kb blueprint promote` <name> | chair | promote a passing draft into the catalog: the Chair's ruling |
| `holon kb list` | any | the knowledge bases you can see |
| `holon kb run` <slug> <verb> <tokens> | any | one genome verb in a knowledge base: kb run SLUG VERB [ARGS...] |
| `holon kb seed` `--dept` <blueprint> <slug> | chair | create a knowledge base from a blueprint, bound to a department or to the organization |
| `holon memory add` `--as` `--scope` `--kind` `--content` `--provenance` `--confidence` `--pin` `--literal` | employee | add to institutional memory |
| `holon memory context` `--as` `--budget` <task_id> | any | preview the context bundle a stage would receive |
| `holon memory episodes` `--scope` `--limit` <query> | any | search past run digests for problems like this |
| `holon memory pin` <item_id> | chair | pin an item (every run in its scope sees it) |
| `holon memory promote` `--to` <item_id> | chair | promote an item to a wider scope |
| `holon memory retire` <item_id> | chair | retire an item |
| `holon memory search` `--scope` `--limit` <query> | any | search institutional memory |
| `holon memory show` <item_id> | any | an item: its content, weight, and its provenance graph |
| `holon memory state` `--summary` `--focus` `--next` <scope> | chair | read or set a project's checkpoint |
| `holon memory unpin` <item_id> | chair | unpin an item |
| `holon recall` `--limit` `--dept` <question> | any | one joined answer from the organization's knowledge: memory, episodes, skills, playbooks |
| `holon law show` <doc> | any | the distribution's law docs (read-only): intent, bylaws, contract |
| `holon ad find` <query> | any | search the organization's findings, decisions and design records by topic |
| `holon ad index` | chair | rebuild the architecture index from the corpus (the ledger, the design records, the decisions): refuses to complete while a ledger row names a test the suite does not carry |
| `holon ad show` <id> | any | one finding, decision or design record by id |
| `holon playbook list` `--dept` | any | playbooks: each routine's and task class's protocol and rubric, with its version |
| `holon playbook show` `--dept` <name> | any | a playbook: its protocol, rubric, and the evidence its next refinement will read |

## skills

*the standard skills library: list, read, tailor, extend and export.*

| Command | Actor | What it does |
|---|---|---|
| `holon skill check` `--dept` | any | validate every skill (frontmatter, links, roles) |
| `holon skill export` `--to` `--name` `--dept` | chair | write resolved, rendered skills to a directory a harness loads skills from |
| `holon skill fork` `--file` `--dept` <name> | chair | copy a skill (or one file) into the instance or --dept layer to tailor it |
| `holon skill invoke` `--as` `--dept` <name> <file> | any | render a skill for an employee and record the use |
| `holon skill list` `--as` `--dept` `--role` | any | skills available (an employee's own with --as) |
| `holon skill new` `--kind` `--description` `--roles` `--dept` <name> | chair | scaffold a new skill in the instance (or --dept) layer |
| `holon skill path` `--as` `--dept` <name> <file> | any | where a skill file resolves |
| `holon skill show` `--as` `--dept` <name> <file> | any | print a skill file |

## recurring work and objectives

*work that repeats on a clock, and work that owns an outcome.*

| Command | Actor | What it does |
|---|---|---|
| `holon schedule add` `--as` `--kind` `--cron` `--stage` `--plan` | chair | when an employee wakes (wake) or a recurring plan fires (recurring) |
| `holon schedule disable` <schedule_id> | chair | disable a schedule |
| `holon schedule enable` `--force` <schedule_id> | chair | enable a schedule (refuses an unstaffed lane unless --force) |
| `holon schedule list` | any | schedules |
| `holon routine add` `--intent` `--dept` `--cron` `--on` `--lane` `--command` `--rubric` `--why` `--deliver` `--workdir` `--task-class` `--risk` `--project` <title> | chair | a recurring job, in the cheapest lane that does it reliably (script, fast, full); the classifier enforces the lane and the routine goes to its approver |
| `holon routine fire` `--payload` `--payload-file` <schedule_id> | kernel | fire an event routine: the runtime that saw a matching event calls this (once per cooldown, with the payload on the work) |
| `holon routine import` `--dept` `--apply` <files> | chair | propose routines from legacy cron-job protocols (cron-jobs/*.md); a dry run unless --apply |
| `holon routine list` | any | routines: lane, schedule, firings and outcomes |
| `holon objective add` `--intent` `--dept` `--key-result` `--project` `--workdir` <title> | chair | an objective: decomposed into work and routines, reviewed against its key results until they are met |
| `holon objective show` <objective_id> | any | an objective's dashboard: key results, its work, its routines and their playbooks |

## communication

*asking a colleague, and reaching another department.*

| Command | Actor | What it does |
|---|---|---|
| `holon msg inbox` `--as` | employee | your messages (and your department's) |
| `holon msg reply` `--as` `--literal` <message> <body> | employee | answer a message, in the thread it belongs to |
| `holon msg request` `--as` `--intent` `--acceptance` `--service` `--priority` <dept> <title> | employee | ask another department for work (it becomes work there) |
| `holon msg send` `--as` `--task` `--kind` `--literal` <to> <body> | employee | message a colleague, a department, the organization or the Chair |
| `holon msg thread` `--as` <message> | employee | read a conversation: the message and every reply to it |

## intactness

*checks that watch the live instance, and the housekeeping they trigger.*

| Command | Actor | What it does |
|---|---|---|
| `holon lint` `--pack` `--strict` | any | check the distribution and instance artifacts: schema, references, bindings, depth, redundancy, agnosticism, tables |
| `holon doctor` | any | check the instance's live state: integrity, invariants, audit chain, security |
| `holon gc` `--apply` `--force` | chair | collect what [retention] says is expired: run directories of closed work (a gold standard's and a judged run's result.json and trajectory.json are kept) and old messages. Dry run by default; --apply refuses without a recent backup |
| `holon backup` `--to` `--with-runs` | chair | a consistent copy of this instance (databases, config, skills, bylaws; never secrets) |
| `holon restore` `--force` `--verify-only` <path> | chair | restore a backup into this instance (verifies its checksums first) |
| `holon keys gc` `--keep` `--apply` `--force` | chair | collect Chair key files no live instance claims (a key whose own instance database is still here is live, wherever the sweep runs from): they move to keys.quarantine/, so a recovery is a mv |
| `holon keys list` `--orphans` | chair | the Chair key files in the default key dir, and which belong to this instance -- a scratch instance's key outlives it; deletion is yours, by hand |
| `holon guards` | chair | the registered incident guards (K-2): what re-checks a fixed defect on every heartbeat, and the incident each came from |
| `holon incident waive` `--note` <work_id> | chair | record why a closed incident's fix carries no guard (a deliberate decision, not an omission; `holon doctor` checks for it) |

## isolation: the trust boundary

*the Chair's boundary between agents and credentials.*

| Command | Actor | What it does |
|---|---|---|
| `holon isolation apply` `--force` | chair | apply the isolation ACLs (agents reach only their trees, run directories and binaries; every other entry on the way is denied to them) |
| `holon isolation plan` `--write` | chair | the one-time setup that runs agents as [isolation] agent_user: a script for root and the ACLs this user applies |
| `holon broker` | kernel | serve the organization to agents running as [isolation] agent_user (a user service) |
| `holon mcp` | any | serve the organization as MCP tools over stdio (for MCP-capable harnesses) |
| `holon adapter test` `--workdir` `--clauses` `--timeout` `--supervisor` <driver> | chair | run the harness conformance suite against a driver, or the supervisor conformance suite against a supervisor command (CONTRACT §12) |

## the harness contract

*how a harness proves it speaks the contract.*

| Command | Actor | What it does |
|---|---|---|
| `holon contract schemas` `--write` `--stage` | any | the result contract as JSON Schemas (--write DIR to publish) |
| `holon classify` `--intent` `--command` <title> | employee | ask the decision model which lane a job needs (script, fast, full) |
| `holon route` <path> | any | which department's reach owns a path ([scope] roots; the deepest root wins) |

## the instance

*the machine the organization runs on.*

| Command | Actor | What it does |
|---|---|---|
| `holon init` `--rotate-chair-key` | chair | create the instance, the executive department and the Chair key |
| `holon status` | any | what needs you, and how the organization is doing |
| `holon tick` `--foreground` | kernel | self-healing sweep, feedback-loop triggers, due schedules (call from any timer) |
| `holon events` `--id` `--limit` | any | the audit trail |
| `holon charter authority` `--set-for` `--task-class` `--max-grade` `--reason` | any | who may approve which grade (--set-for: Chair only) |
| `holon charter autonomy` | any | effective autonomy policy per department (holon.toml [autonomy]) |
| `holon metrics` `--dept` `--days` | any | the feedback loops' sensors, each with its counter-metric |
| `holon help` | any | this command registry (--json: the manifest agents read) |
