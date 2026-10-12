# Naming for agentic efficacy: audit + proposal (v2)

**Status:** PROPOSAL (2026-10-12, v2 after operator direction). The OS is an *organization of
persistent employees* (see [INTENT.md](INTENT.md)), so org vocabulary is deliberate. v1's rule
against org-chart terms is withdrawn. The failure it guarded against (hollow role-play: a
"COO pulse" that never ran, a kaizen field empty in 1,749 of 1,749 worklogs) is handled
instead by **binding every org term to a mechanism**.

## Why names matter more here

Agents read every path, command, and field name, constantly, and act on what a name
suggests. A name is a prompt. Bad names cost in three ways:
1. **Glossary tax.** A private metaphor (`cortex`, `swarm`, `genome`, `forge`, `loo`)
   predicts nothing, so every visit costs a docs lookup.
2. **Namesake pull.** A name that already denotes a well-known tool drags the model toward
   it: `arch` (Arch Linux, the `arch` coreutil), `axe` (axe-core), `forge` (Foundry/Laravel/Minecraft),
   `compass` (MongoDB Compass), `hr` (horizontal rule), `pe` (Portable Executable), `web`. Agents
   hallucinate flags from the namesake.
3. **Hollow terms.** A word with no data or behavior behind it invites performance instead
   of work.

## Rules

| # | Rule | Test |
|---|---|---|
| N1 | **Literal in the organization's language.** Use the words a company uses for the function (CEO, HOD, department, employee, IT, marketing, policy, audit), which models know deeply. | Would a new hire know what happens there? |
| N2 | **No strong developer-tool namesake** for CLIs and top-level directories. | Does the name already denote a well-known tool? |
| N3 | **Every org term binds to a mechanism**: a table, a field, a pipeline step, or it is removed. | Can you point to the row or code it means? |
| N4 | **One name per thing.** Aliases exist only during migration, with a removal date. | Is there a second name anywhere? |
| N5 | **CLI grammar `<os> <noun> <verb>`**; every noun owned by exactly one module. | Does the noun have one owner? |
| N6 | **Whole words where agents type.** | Pronounceable and unambiguous? |

## 1. The OS name

It must say: *an autonomous organization of agents that heals and evolves itself and
develops collective intelligence*.

| Candidate | Reads as | For | Against |
|---|---|---|---|
| **Holon** (recommended) | `holon task add`, `holon hr hire`, `HOLON_HOME=~/holon` | An established multi-agent-systems term: **holonic MAS / holarchies** are autonomous, self-organizing agents nested in hierarchies, each a whole in itself and a part of something larger (employee ⊂ department ⊂ company), designed to self-repair and adapt. Matches the org chart *and* the autonomy *and* the evolution. You already think in holonic terms. | Less familiar to casual readers than a plain-English word |
| **Colony** | `colony task add`, `COLONY_HOME` | Strongest connotation of collective and emergent intelligence and self-healing (ant-colony intelligence) | Ants have no HODs or careers; weak on hierarchy and individual identity |
| **Autonomic** | `autonomic task add` | The canonical term for self-healing, self-configuring, self-optimizing systems (autonomic computing) | An adjective; says nothing about organization or collective intelligence |

Rejected: **hivemind** (a well-known ML library; also implies individuals dissolve, which is the opposite of persistent employees), **organism** (biology metaphor; the strata glossary it spawned is the problem), **delegate / mandate / agency** (v1: they name only the contractor-style task contract).

## 2. Organization layout (kernel + instance)

```
<os>/                          v1 / current                      what it is
  executive/                   cortex/                            leadership and governance
    ceo/                       strategy-incubator/ceo-office      priorities, mandates, arbitration
    charter/                   meta-governance (arch)             policies + controls (the "genome")
    audit/                     living-architecture + HR efficacy  independent sensing → findings
  operations/                  swarm/                             how the company runs
    work/                      task-grid (axe), forge store       tasks, plans, approvals, runs, results
    hr/                        workforce-ops (hr) + agent-fabric  roster, job descriptions, hiring, reviews
    comms/                     notice-board, channels.jsonl       DMs, department channels, requests
    memory/                    (new)                              institutional memory: emp/dept/org/project
  services/                    foundations/                       shared-service departments
    strategy/                  strategy-incubator (compass/traj)  goals tree, strategies
    records/                   flight-ledger (logbook)            the Chair's logbook: human evidence
    identity/                  identity-core                      who the Chair is
    crm/                       relationship-crm (rel-intel)       contacts, relationships
    marketing/                 content-campaigns                  campaigns, content, audiences
    research/                  research-vault (kosmos)            knowledge base (the KosmOS vault)
    websites/                  site-factory (web)                 site registry and scaffolding
    presentations/             presentation-engine (pe)           diagrams, decks, documents
    it/                        platform-infra (bos…)              hosts, browsers, credentials, tooling
  divisions/                   ventures/                          use-case divisions (job search, freelance…)
  archive/                     graveyard/ + archive/              retired things (one name)
```

The kernel distribution ships `executive/` and `operations/`. `services/` and `divisions/` are
departments installed per instance; generic ones (strategy, crm, research, it) can later
ship as a standard library of departments.

## 3. Vocabulary

### People and structure
| Term | Meaning (mechanism) | Replaces |
|---|---|---|
| Chair | the human; approval authority per charter | "Ishan", "operator", "waiting-on-ishan" |
| CEO | head of `executive` | ceo-office "stance organ" |
| department (`dept`) | unit with mission, head, members, memory scope | pool, system, department (kept) |
| HOD | employee who heads a department | (new as a mechanism) |
| employee (`emp_…`) | persistent identity + JD + memory + record | agent / employee / AgentSpec (three records today) |
| job description (JD) | role, responsibilities, task classes, skills, KPIs | persona, AgentSpec workflow |
| hire / fire | roster create / retire with evidence | `hr deploy/retire`, `af spec apply` |
| review | evidence-computed performance + manager judgment | `hr review`, efficacy digest, COO pulse |
| colleague links | who works with whom (from real interactions) | bonds |

### Work
| Term | Meaning | Replaces |
|---|---|---|
| task, plan, run, result | the pipeline objects | AXE tasks, forge tasks, hermes kanban |
| brief (`BRIEF.md`) | what an employee reads to do a run | `BRIEF.md`, brief |
| findings | what the planner found | `findings` |
| writes | paths the plan will modify | `writes` |
| recurring plan | a plan approved once, run on a schedule | recurring plan, cron job + .md |
| `waiting_on_subtasks` | a parent task whose work was split | `waiting_on_subtasks` |
| assignment | task given to a department/employee with a mandate | delegation chain |
| request | a cross-department ask that becomes a task | notice with `--to-dept` |

### Governance
| Term | Meaning | Replaces |
|---|---|---|
| charter | the company's binding rules | genome, architecture law |
| policy | a forward decision with an enforcer | AD (architectural decision) |
| control | a check that keeps a known failure from recurring | RG (regression guard) |
| service agreement | what a department provides to others | `services_offered/consumed` contract |
| `provides:` / `uses:` | declared dependencies | pooling, `pooled_sub_systems` |
| finding | something audit detected | drift report, efficacy flag |
| status report | department's machine-readable state | STATUS.md (kept as the file) |
| health check | fail-closed liveness probe | smoke.yaml (kept as the file) |
| spin-out | a strategy becoming a division | graduate / graduation |

## 4. CLI

One entry point, nouns owned by one module:

```
<os> task add|list|show|cancel|retry|replan|answer      operations/work
<os> plan queue|show|approve|reject                     operations/work
<os> claim | submit | tick | run heartbeat              operations/work
<os> dept create|list|show                              operations/hr
<os> emp list|show                                      operations/hr
<os> hr hire|fire|review|transfer                       operations/hr
<os> msg send|inbox|channel                             operations/comms
<os> memory add|search|promote|context                  operations/memory
<os> policy|control new|list|show                       executive/charter
<os> audit run|findings                                 executive/audit
```

Legacy CLIs map: `arch` → `<os> policy|control|audit`; `axe` → `<os> task|plan|tick|msg`;
`af`/`hr` → `<os> emp|hr`; `forge`/`traj`/`compass` → `<os> task` + `goals` (services/strategy);
`rel-intel` → `crm`; `kosmos` → `research`; `web` → `websites`; `pe` → `present`;
`bos*` → `it browser`.

## 5. Divisions (applied after the core lands)

| Current | Proposed |
|---|---|
| career-pipeline (job-engine) | divisions/job-search |
| freelance-agency (freelance-engine) | divisions/freelance |
| architectural-leads (design-aesthetics) | divisions/design-aesthetics-leads |
| credibility-funnel (technical-authority) | divisions/technical-brand |
| brand-integrating | divisions/personal-brand |
| brand-polarizing | divisions/undercurrent |
| community-loo | divisions/law-of-one-community ("loo" reads as *toilet* to a model) |
| vitality-research | divisions/health-research |
| saas-studio | divisions/products |
| quant-trading | divisions/quant-trading |
