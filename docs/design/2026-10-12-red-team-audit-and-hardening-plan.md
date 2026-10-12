# Red-team audit and hardening plan (2026-10-12)

Scope: the Holon distribution at `ff53c63`+1. That covers the kernel, CLI, genome, skills,
autonomy, docs, and the ZeroClaw and Hermes integrations. VPS operations are out of scope by
the Chair's direction: finalize the architecture first.

Method: I read the code, introspected the argparse tree (63 leaf commands), and checked
mechanically every `holon …` command and option referenced in skills and docs (0 broken).
I also cross-checked documented claims against implementing code, and probed the harness
extension points (ZeroClaw config schema, Hermes `profile`/`plugins`/`skills`/`hooks`).

## 1. What is in place

### 1.1 CLI architecture

One entry point, `holon`, with 63 commands in 20 groups. Human text by default, `--json` for
agents. Exit codes: 0 ok, 1 error, 2 usage, 3 nothing to do.

| Stratum | Groups (commands) |
|---|---|
| Organization | `init`, `status`, `org`, `dept` (create, list, roles, profile, profiles), `hr` (hire, fire, pause, activate, review), `emp` (list, show), `charter` (authority, autonomy) |
| Work | `task` (add, list, show, cancel, retry, replan, answer), `plan` (queue, show, approve, reject, grade), `claim`, `submit`, `run` (show, heartbeat), `verify`, `dispatch`, `tick`, `schedule` (add, list, enable, disable) |
| Communication | `msg` (send, inbox, request) |
| Knowledge | `memory` (add, search, pin, unpin, retire, promote, state, context) |
| Genome | `skill` (list, show, path, invoke, fork, new, check, export), `dept profile/roles`, `charter autonomy` |
| Integrity | `audit` (samples, judge), `events`, `drivers` |

Identity flags: global `--actor` (free text, default `chair`) and per-command `--as <emp_id>`.

### 1.2 Foundational skills (`holon/skills`, 10)

| Kind | Skills |
|---|---|
| protocol | `holon-employee` (+ `references/organization.md`) |
| role | `holon-head` (review brief, shaping, staffing, department review), `holon-researcher` (terrain), `holon-planner` (plan brief, grading), `holon-executor` (execute brief), `holon-verifier` (verify brief), `holon-steward` (retro, curation, evolution, healing) |
| operation | `holon-system-design` (profiles, job descriptions), `holon-collaboration`, `holon-memory` |

Layers per file: `@dept` → instance → stdlib. Duty briefs are rendered from role skills.

### 1.3 Integrity mechanisms

| Mechanism | Guarantees | Where |
|---|---|---|
| Forward-only migrations | schema history; no edits to applied files | `core/db.py`, `migrations/` |
| `BEGIN IMMEDIATE` claims, leases, `sweep_leases` | no double claim; dead runs recovered | `work.py` |
| `TRANSITIONS` + `events` | legal task moves only, every move logged | `work.py` |
| Envelope validators | agent output enters only in validated shape | `envelopes.py` |
| Separation rules | no self-approval, no self-verification, no self-widening | `work.py`, `service.py` |
| Grade floors + routing + earned authority + audit holdout | approvals match risk; trust is earned and lost on evidence | `grading.py`, `org.py` |
| Autonomy policy validation | invalid `[autonomy]` fails at kernel start | `autonomy.py` |
| Stale-plan fingerprints | execution never runs on changed ground | `staleness.py` |
| Read-only guard | planner and reviewer runs that change the workdir are discarded | `service.py` |
| Driver TOML validation | unknown keys and placeholders rejected | `drivers.py` |
| Genome validation | profiles cover all roles; no execute+verify slot | `genome.py` |
| `holon skill check` | skill frontmatter, links, roles | `skills.py` |
| Tests (63) incl. `Agnosticism` | stdlib-only; no harness named in kernel `.py` | `tests/` |

**There is no linter for documentation depth, redundancy or cross-artifact schema
consistency, no database integrity checker, and no authentication of who is acting.** Those are
the headline gaps below.

## 2. Findings

Severity: **S1** breaks a core guarantee · **S2** hollow or inconsistent architecture ·
**S3** robustness or hygiene.

### A. Integrity and authority (the guarantees can be bypassed)

| # | Sev | Finding | Evidence | Fix |
|---|---|---|---|---|
| A1 | S1 | **Nobody is authenticated.** Any process that runs `holon` acts as the Chair by default, and `--as` accepts any employee id. An executor can approve its own plan as `chair`, or verify as a colleague. Separation of duties, grading and earned authority are advisory against a misbehaving agent. | `cli.py` `--actor` default `HOLON_ACTOR` or `chair`; `Kernel(actor=…)` trusts it | Run-scoped capability tokens: the kernel mints `HOLON_RUN_TOKEN` per run, bound to (employee, run, duty), and CLI calls from agents must present it. Chair actions require a Chair key stored outside every agent's allowed roots. Without a token or key, the CLI is read-only. |
| A2 | S1 | **Instance state is writable by agents.** The ZeroClaw worker profile's `allowed_roots` include `$HOLON_HOME`, so an agent can edit `holon.toml` (set `mode = "autonomous"`) or write `work.db` directly. | `adapters/zeroclaw/install.py`: `roots = [str(home)] + …` | Split the instance: `state/` (DBs, config) is never in an agent's roots; agents get only their run directory and workdir. Every write goes through the CLI under a token. |
| A3 | S1 | **The read-only posture leaks.** The observer profile allows `git` (commit, reset, checkout all write) and `sqlite3` (can write any DB, including `work.db`). The kernel's workdir diff check is skipped when `read_only_enforced = true`. | `config.snippet.toml` `holon-observer.allowed_commands` | Remove `sqlite3`; replace `git` with read-only wrappers (`git-ro`: log, show, diff, status, grep), or keep the kernel diff check on as defense in depth even when the harness claims enforcement. |
| A4 | S2 | **The audit trail is mutable.** Events can be updated or deleted by anything that can write the DB. | `events` table, no chain | Hash-chain events (`prev_hash`, `hash`); `holon doctor` verifies the chain. |
| A5 | S2 | **The Hermes driver runs `--yolo`.** Read-only is only the post-run workdir diff, and writes outside the workdir go undetected. | `drivers/hermes.toml` | Hermes plugin (F3) with a per-posture toolset (`-t`) and no `--yolo` for observe duties. |
| A6 | S2 | **Chair-only rules are scattered** (`regrade`, `dept profile`, `set_authority`), each checked ad hoc. New commands will forget. | `service.py:118`, `cli.py` dept profile | Declare each command's required actor class in a command registry (E1) and enforce it in one place. |

### B. Genome coherence (the genome exists on paper and partly in code)

| # | Sev | Finding | Evidence | Fix |
|---|---|---|---|---|
| B1 | S2 | **Two work models coexist.** There are 4 duties and 6 roles. Researcher and steward have no duty, so they cannot be dispatched: roles without a mechanism, against law 3. `ROLE_OF_KIND` still says `reviewer`. | `genome.ROLES` duties `[]`; `briefs.ROLE_OF_KIND` | G1 work graph: stages = roles, so `research` and `retro` become claimable stages and duties disappear. |
| B2 | S1 | **The self-evolving loops are open.** `proposals` are stored and never read. No retro is triggered. Probation never ends or is reviewed. INTENT's "failures automatically become incident tasks for IT" is not implemented. | grep: `proposals` written in `work.py` only; no incident code | Wire L1–L4 (§4): a triggers table evaluated by `tick` posts retro, incident and review stages; proposals become steward inbox items. |
| B3 | S2 | **Only executors learn.** Planner, verifier and reviewer envelopes have no `learnings`, so their lessons are lost. | `envelopes.py` | Add `learnings`/`proposals` to every envelope; consolidate for every duty. |
| B4 | S2 | **Silent default genome.** Departments created before migration 0003 have no profile, and `staffing()` silently assumes `light`. | `org.staffing` | Lint error "department without profile"; never default silently. |
| B5 | S3 | **A department's profile is a snapshot with no version.** Editing `light.toml` does not reach existing departments, and nothing reports the drift. | `departments.genome` | Profile `version`; `holon lint` reports departments behind their profile; the Chair applies the change. |
| B6 | S3 | **Dead code paths:** empty `holon/governance`, unused `Home.genome_db`, unused `ext_id`/`sync_dirty` (reserved for the dashboard). | grep | Delete or mark reserved, with the lint "reserved" allowlist. |

### C. Schema and redundancy (more than one source of truth)

| # | Sev | Finding | Evidence | Fix |
|---|---|---|---|---|
| C1 | S2 | **Envelope truth lives twice:** `schemas/*.json` (hand-written; the plan schema has no top-level properties) and `envelopes.py`. Nothing checks they agree. | `schemas/plan-result.schema.json` | One spec in code (`holon/core/contract.py`); validators and JSON Schemas are both generated from it, and lint fails on drift. |
| C2 | S2 | **Vocabularies are duplicated:** duties appear in 6 places (`org.DUTIES`, `runs` CHECK, `schedules` choices, CLI choices, `ROLE_OF_KIND`, `BRIEF_OF_KIND`). Memory kinds appear in Python and an SQL CHECK. Statuses appear in `TRANSITIONS` and the docs. | grep | `holon/core/vocab.py` as the single registry; SQL CHECKs and docs are verified against it by lint. |
| C3 | S2 | **Grade routing is explained in 5 places** (CONTRACT §2, `holon-planner/references/grading.md`, `holon-employee/references/organization.md`, GENOME.md, INTENT.md). | read | One canonical reference (`holon-planner/references/grading.md`); others link to it. Enforced by the redundancy lint. |
| C4 | S3 | **Role skills repeat their own briefs** (e.g. verifier "read-only, do not fix" in both). | `holon-verifier/SKILL.md` vs `briefs/verify.md` | SKILL.md = when, why, principles; briefs = procedure and output. The lint flags overlap. |
| C5 | S3 | **Legacy `[authority]` config is merged silently.** | `autonomy.policy` | Lint warning with the migration hint; removed in the next minor version. |

### D. Documentation

| # | Sev | Finding | Evidence | Fix |
|---|---|---|---|---|
| D1 | S2 | **CONTRACT.md is stale:** it lists env var `OG` (from the old name), omits `HOLON_DUTY`, and says nothing about briefs coming from skills, `brief.json.skill`, or the skills export. | `docs/CONTRACT.md:22` | Regenerate the env and brief tables from code (lint checks they match). |
| D2 | S2 | **INTENT promises mechanisms that do not exist** (probation reviews, incident tasks, weekly and monthly reviews, department channel semantics). | INTENT tables vs grep | Every INTENT/GENOME term gets a `mechanism:` binding (code symbol or CLI command) checked by lint; unbuilt ones are marked *planned* with a phase. |
| D3 | S3 | AGENTS.md law numbering is out of order (7 before 5). | AGENTS.md | Fix, and lint numbered lists. |
| D4 | S2 | **Nothing measures documentation depth**: a skill could be a stub and pass `skill check`. | `skills.check` checks only frontmatter and links | Depth rubric (§3). |

### E. CLI architecture

| # | Sev | Finding | Fix |
|---|---|---|---|
| E1 | S2 | The CLI is one 700-line module with if/elif dispatch. It has no command registry, so commands cannot carry metadata (stratum, required actor, output schema), be listed for agents, or be linted. | A command registry: each command declares `group, name, actor: chair\|employee\|any\|kernel, mutates: bool, output: schema`. `holon help --json` emits the manifest; harness skills and lint consume it. |
| E2 | S2 | **Two identity flags with different semantics** (`--actor` free text, `--as` employee id). | One identity source: token (agents) or Chair key (human); `--as` only for the Chair impersonating for tests. |
| E3 | S1 | **There are no integrity commands.** | `holon lint` (artifacts), `holon doctor` (state), `holon metrics` (loops' sensors). |
| E4 | S3 | `--json` output shapes are undocumented. | Output schemas in the registry; lint checks the manifest against handlers via a smoke run. |

### F. Harness plugins

| # | Sev | Finding | Evidence | Fix |
|---|---|---|---|---|
| F1 | S1 | **The ZeroClaw skills went into the wrong place.** They landed in ZeroClaw's shared `hermes` skill bundle, and the `holon-*` agents declare no `skill_bundles`, so whether Holon agents load them is unverified. It also collides with the user's own bundle namespace. | install error: "Destination skill already exists: …/shared/skills/hermes/holon-employee"; `config.snippet.toml` agents lack `skill_bundles` | Own bundle `[skill_bundles.holon]`; agents set `skill_bundles = ["holon"]`; installer verifies an agent can list the skill. |
| F2 | S2 | **ZeroClaw identity is per duty, not per employee.** All employees share `holon-plan` and so on, and ZeroClaw's native per-agent `IDENTITY.md`/`SOUL.md` is unused. | snippet | Per-employee, per-posture aliases with rendered identity files, regenerated by `holon adapter zeroclaw sync` on hire, fire and review. |
| F3 | S2 | **The installer edits the user's main `config.toml`**, which is shared with their assistant: a large blast radius. | `install.py` | Prefer an isolated config dir (`zeroclaw --config-dir ~/.zeroclaw-holon`) cloned from provider settings; fall back to the marked block. |
| F4 | S1 | **There is no Hermes plugin**, only a driver TOML. Hermes offers per-profile identity (`hermes profile`, `SOUL.md`), skills (`hermes skills`, `-s`), plugins and hooks, `-t` toolsets, and `--pass-session-id`; none is used. | `holon/execution/drivers/hermes.toml` | `adapters/hermes/`: profile per employee (identity), skills install, posture toolsets, driver without `--yolo` for observe duties, installer and uninstaller. |
| F5 | S2 | **No adapter conformance suite.** Each plugin is tested by hand on a live host. | — | `holon adapter test <driver>`: runs the contract with a deterministic brief (read-only planner, executor writing one file, JSON-last-message fallback, lease heartbeat, identity check) and reports pass or fail per clause. |

### G. Operational robustness

| # | Sev | Finding | Fix |
|---|---|---|---|
| G1 | S2 | **No state integrity check or backup.** | `holon doctor`: `PRAGMA integrity_check`, `foreign_key_check`, invariants (one running run per executing task; one pending plan per awaiting task; approvers resolvable; no historical self-approval; event chain valid), file permissions; `holon backup` / `restore` with the online backup API. |
| G2 | S3 | **No budget controls.** Runs have token and cost columns that drivers never fill, and nothing caps spend per department or project. | Drivers may report usage; `[budget]` per department with WIP and cost caps; the steward's metrics read them. |

## 3. Rubrics and linters: `holon lint`

One command, stdlib only, rule packs with ids. Each rule is an error (fails CI) or a warning.
`--json` output; `--fix` only where the fix is mechanical.

| Pack | Rules (examples) |
|---|---|
| **SCHEMA** | envelope validators match generated JSON Schemas; skill frontmatter (name = dir, kind, roles ⊂ ROLES, `description` contains "Use when"); genome profiles valid and versioned; every department has a profile and is current; driver TOMLs valid; `holon.toml` has no unknown sections or keys; applied migrations unchanged (checksum); SQL CHECK lists equal `vocab.py` |
| **REF** | every `holon <cmd> [--opt]` in skills and docs exists (CLI manifest); every `` `references/…` `` and `` `briefs/…` `` link resolves; every brief placeholder is one the kernel substitutes and every substituted one is used; documented env vars equal the env the driver sets; markdown links resolve |
| **BIND** (law 3) | every org term in INTENT and GENOME tables has a `mechanism:` that resolves to a code symbol or CLI command, or is marked *planned (phase)*; every role has ≥ 1 claimable stage; every config key is read by code and documented in GENOME/CONTRACT |
| **DEPTH** (rubric) | per skill kind, required sections: protocol (Protocol, Always), role (Duties, Principles, and a brief or a procedure reference), operation (When to use, Protocol); body within word bounds (no stubs, no bloat: role SKILL.md 150–600 words); ≥ 1 runnable command; roles with side effects have a "Never" section; docs have a purpose line and an owner |
| **DRY** (redundancy) | shingled-Jaccard similarity across all markdown paragraphs (skills, docs, briefs): ≥ 0.6 warns, ≥ 0.8 errors unless one links to the other as canonical; enumerations defined once in `vocab.py` (lint flags literal copies in code) |
| **AGNOSTIC** | stdlib-only imports; no harness named anywhere under `holon/`, including skills, briefs and genome |

`holon doctor` is the runtime counterpart (state, not artifacts), and both run in the test
suite, so the repository cannot merge a broken genome.

Scoring for the Chair's dashboard (per artifact, 0–3 each): **Depth**, **Grounding** (runnable
commands and evidence), **Non-redundancy**, **Schema validity**, **Binding**. Errors gate;
scores trend.

## 4. Target refinements (what "finalized" means)

1. **One vocabulary, one contract.** `vocab.py` (roles, stages, statuses, memory kinds, grades,
   actor classes) and `contract.py` (envelope specs) are the only definitions; schemas, SQL
   checks, CLI choices and doc tables are derived or linted against them.
2. **Identity and authority enforced, not trusted.** Run tokens, Chair key, a command registry
   with actor classes, agent roots that exclude instance state, and a hash-chained audit trail.
3. **Work graph (G1)** with role-aligned stages: research, shape, plan, gate, execute, verify,
   retro. Duties retire.
4. **Closed feedback loops**: a triggers table evaluated by `tick`:

   | Trigger | Posts |
   |---|---|
   | verify fail, second rejection, task failed | retro stage → steward |
   | lease expired twice, driver error streak | incident → IT |
   | probation period elapsed | review → head |
   | weekly / monthly | department / company review → head / CEO |
   | proposal in any envelope | steward inbox item |

   `holon metrics` gives the sensors: first-pass rate, rework, gate latency, cycle time per stage,
   backlog age, research reuse, cost per verified result, each with its counter-metric.
5. **Plugins as a defined adapter shape.** An adapter has five parts:
   - a driver;
   - an identity renderer;
   - a skills installer;
   - a posture map (observe or act → harness permissions);
   - and it passes `holon adapter test`.

   ZeroClaw v2 and Hermes v1 both implement it.

## 5. Plan

Each phase ends with the test suite, `holon lint` and `holon doctor` green. No VPS work until R6.

| Phase | Delivers | Closes | Exit criteria |
|---|---|---|---|
| **R1 Integrity tooling** | `vocab.py`, `contract.py` (generated schemas), `holon lint` (all six packs), `holon doctor`, command registry + `holon help --json` | C1, C2, D4, E1, E3, G1 (check part) | lint and doctor in CI; current tree passes with no errors; schema drift impossible |
| **R2 Docs and skills to rubric** | fix CONTRACT, INTENT bindings, AGENTS, de-duplicate grading and role/brief overlap; depth fixes | C3, C4, D1–D3, B6 | DEPTH, DRY and BIND packs clean |
| **R3 Identity and authority** | run tokens, Chair key, actor classes enforced centrally, state split from agent roots, event hash chain | A1, A2, A4, A6, E2 | red-team tests: an agent token cannot approve, verify its own work, widen authority, or write state |
| **R4 Genome completion (G1)** | work graph with role stages; duties retired; learnings from every role; profile versioning | B1, B3, B4, B5 | every role claimable; migration converts existing data; lint BIND clean |
| **R5 Feedback loops** | triggers table, retro/incident/probation/review stages, proposals to steward, `holon metrics` | B2, G2 | each INTENT loop has a test that fires it end to end |
| **R6 Plugins** | adapter shape; `holon adapter test`; ZeroClaw v2 (own skill bundle, per-employee identity, isolated config, read-only commands fixed); Hermes v1 (profiles, skills, toolsets, no `--yolo` for observe) | A3, A5, F1–F5 | both adapters pass the conformance suite locally; then, and only then, redeploy to the VPS |

Order rationale: the linters (R1) make every later phase checkable, and identity (R3) has to
come before the loops (R5) let the organization change itself. Plugins come last, because they
consume the registry, tokens and identity renderer built before them.

## 6. Status (2026-10-12, after execution)

All six phases are implemented on `main`; 99 tests pass; `holon lint --strict` reports
0 errors and 0 warnings on the distribution; `holon doctor` is clean on a fresh instance.

| Finding | Closed by |
|---|---|
| A1, A6, E2 | `holon/execution/security.py` (Chair key, employee keys, run tokens) + the command registry's actor classes |
| A2, A3 | ZeroClaw adapter v2: roots exclude instance state; observer has no git/sqlite3; the kernel guard stays on unless trusted |
| A4 | hash-chained events (`core/db.log_event`), verified by `holon doctor` |
| A5, F4 | Hermes adapter: real flags (the old driver's flags no longer existed), posture toolsets, no `--yolo` while observing |
| B1, B3 | the work graph (migration 0004): seven stages, one per role; learnings from every stage |
| B2 | triggers: retros, incidents, probation confirmations, department retros over proposals; `holon metrics` |
| B4, B5, B6 | lint SCHEMA (profile on every department, profile versions); dead paths removed |
| C1–C5 | `core/vocab.py`, `core/contract.py` (schemas generated), lint DRY and SCHEMA |
| D1–D4 | docs rewritten; lint REF, BIND and DEPTH |
| E1, E3, E4 | registry-based CLI, `holon help --json`, `holon lint`, `holon doctor` |
| F1, F2, F3, F5 | ZeroClaw `holon` skill bundle, identity in every message, `--config-dir`, `holon adapter test` |
| G1 | `holon doctor` (backup/restore remains open) |
| G2 | partially: cost per verified result in `holon metrics`; budget caps remain open |

G1 and G2 were closed afterwards: `holon backup` / `holon restore` (online, checksummed, no
secrets) and per-department budget caps (`[budget]`).

## 7. Real-harness validation (2026-10-12, this laptop)

| Harness | Result |
|---|---|
| Hermes Agent v0.19.0, aroute `free-stack` | conformance 5/5 (~2.5 min); end to end: a `light` department of three Hermes employees took a real coding task through research, shape, plan, Chair approval, execute and verify; terrain, lessons and a proposal landed in memory; doctor and lint clean |
| Claude Code (`claude -p`) | driver, plan, approve pass; execute and verify not yet run (the account's session limit cut the run off) |
| ZeroClaw | not installed locally; on the VPS only |

Defects the real runs found, all fixed with regression tests:
1. Exported skills baked one instance's path into `$holon`, so a harness serving another instance
   used the wrong instance with its token. Exports now render `${HOLON:-holon}`.
2. Hermes ignores `OPENAI_BASE_URL`; it reads `CUSTOM_BASE_URL`.
3. Hermes disables stream-stall detection for local endpoints; one stalled stream hung a run
   for 15 minutes. The driver sets stall and API timeouts.
4. The read-only guard counted `__pycache__/` (from running tests while researching) as a change.
   Tool caches are now ignored.
5. A breach found after an agent self-submitted crashed the dispatch; it is now recorded.
6. Claude Code confines tools to the working directory, so the agent could not read its context.
   The driver adds the run directory with `--add-dir` and puts the identity in the system prompt.

Still open: Claude Code's execute and verify clauses; ZeroClaw conformance and the VPS migration
(the Chair's go-ahead); model latency on free-stack (50–115 s per large-context call).
