---
status: proposed
date: 2026-10-16
description: "The work list from a red-team audit of the capability layer (units, pooling, policy.json, the brief) and its seam with the operant runtime: what is broken and fixable now in this repo, what waits on a decision, how to sequence the cross-repo pieces, and the method that found it — so the next agent can fix and re-audit without redoing the discovery."
tags: [audit, capability-layer, units, policy, operant, notion, worklist]
---

# 2026-10-16: the capability-layer audit — the units, the pools, and the fences around them

Status: **proposed work list** (published scrubbed; the full record with operator context lives
in the private overlay). It was found by reading the kernel at `a513f4b`, the operant runtime's
source and a live instance read-only. Item ids (R-nn) match the operant repository's
`docs/agentic-os/AUDIT-2026-10-16-capability-layer.md`, so a fix on either side can name its
counterpart.

**How to use it.** §1 items are defects: fix them now, tests first (AGENTS.md law 9), each with a
guard test and a regression-ledger row in the same commit. §2 items change a mechanism's design:
do not build them until the Chair adopts the direction (they are listed so the fix for §1 does not
paint them into a corner). §3 is the cross-repo order. §4 is how the audit was done: read it
before you re-audit anything.

## 1. Defects: fixable now, no decision needed

### R-01 — the pooled-units render skips plan, research and shape

- **Where:** `holon/capabilities/context.py:246`. The condition names five stages, but the block
  is nested inside `if stage in ("execute", "verify"):` at `context.py:227`, so only execute and
  verify ever reach it.
- **Why it matters:** the planner chooses the tools and the grade, and it plans without knowing
  what its department pools. The executor inherits that plan.
- **Fix:** dedent the block to the stage-material level (after the `approve` / execute-verify
  blocks, before `retro`). Keep the five-stage condition.
- **Guard:** extend `tests/test_genome.py::test_pooled_units_join_the_reach_and_render_in_briefs`
  (today it asserts only the executor) with a plan-stage claim on a fresh work item:
  `"Pooled units" in context.md` at plan.
- **Pitfall:** the bundle renders in priority order within `[context] budget_chars`. Moving the
  block earlier means it costs every plan stage's budget. Check `holon memory context <stage>`
  on a department with three or more pooled units, and look at "Context gaps".

### R-06 — pooled roots are writable, grade as in-bounds, and are granted at the OS boundary

GENOME §14 says pooling grants reads and "never the pen". Four consumers disagree, because
`Kernel.scope()` adds every pooled unit's roots to the one `roots` list
(`holon/execution/service.py:1685`):

| Consumer | What it does with `roots` | File |
|---|---|---|
| grading | a write inside the reach is in bounds, so writing into a pooled unit escapes the G3 floor (and the `[pilot]` G4 floor that keys on it) | `holon/execution/grading.py:59` |
| policy.json | `roots` goes to the harness; the operant runtime's write fence treats `roots` as **writable** | `holon/execution/policy.py:38` |
| isolation | the agent user is granted **write** on every scope root | `holon/execution/isolation.py:330` |
| sandboxes | ZeroClaw's allowed roots | `adapters/zeroclaw/install.py:78` |

- **Fix (backward compatible, no runtime change needed):** `scope()` returns pooled roots in a new
  `read_roots` list and keeps `roots` for the writable reach (the workdir, `[scope]` and
  `[scope.dept]` roots). Then:
  - `compile_policy` emits `read_roots` beside `roots`. A runtime that fences writes by `roots`
    (operant does today) refuses pooled writes immediately, without a code change on its side.
  - grading floors a write into a `read_roots` path at G3, with the reason "writes a pooled unit
    (work for its owner: msg request)".
  - `isolation.reach()` puts `read_roots` into `reads`, not `writes`.
  - briefs' "Your reach" lists them as read-only; ZeroClaw grants them read-only, or the
    installer says it cannot and refuses.
- **Docs:** CONTRACT §10 gains `read_roots` (policy version stays 1: the field is additive and
  operant's parser ignores unknown fields); GENOME §8 and §14 say which list a pooled root joins.
- **Guards:** (1) a plan writing into a pooled root grades G3; (2) the compiled policy has the
  pooled root in `read_roots` and not in `roots`; (3) `isolation.reach()` puts it in `reads`.
- **Pitfall:** `holon route` and the smoke runner (`service.py:1722`, "the department's first
  scope root") read `roots`. Smoke must still find a root for a department whose only reach is
  pooled. Decide that explicitly; do not let it fall to `[]` silently.

### R-05 — pooled services' commands never reach the fence

- **Where:** `holon/execution/policy.py:39` reads only `[scope]` commands. Operant treats an empty
  list as ungoverned (`crates/operant-core/src/org/mod.rs:106` in that repo).
- **Fix:** when a department's merged `commands` list is non-empty, append the entry binary
  (first token of `command`) of every service it pools. Lint (pack `genome`) warns about a
  department that pools units but lists no commands: "its terminal fence is open".
- **Pitfall — do not derive into an empty list.** An empty list means *ungoverned*. Appending
  pooled binaries to a department that lists nothing turns its fence **on** and refuses every
  other command (git, python, …) in its next run. Only extend lists that already exist.
- **Guard:** a department with `commands = ["git"]` pooling a unit whose service runs `kosmos …`
  compiles a policy whose `commands` holds both; a department with no commands compiles `[]`.

### R-08 / R-10 — the operant adapter pins keys the runtime no longer honours

- **Where:** `adapters/operant/install.py:113` writes `[memory] provider = "builtin"`. Current
  operant ignores it and logs `memory-wire is the only memory engine now`, so the isolation the
  comment promises does not exist. The `[kernel]` (continual-harness) lane is not pinned, and its
  store defaults outside `HERMES_HOME`.
- **Fix:** stop writing the dead `[memory] provider` key. Write `[kernel] enabled = false`
  (operant's `KernelSettings` is `deny_unknown_fields`: write only `enabled`). Real memory
  isolation is the runtime's job (operant R-08: no personal memory provider in a Holon run).
  Until it lands, the adapter README says so instead of claiming isolation.
- **Guard:** the installer test asserts both posture configs carry `[kernel] enabled = false`
  and no `[memory] provider`, and `tomllib` parses them.

### R-09 — a body sees two versions of the same skill

- **Where:** `adapters/operant/install.py:207` exports skills once, with no `--dept`, into the
  `HERMES_HOME` every department shares. Operant lists them in its cache-stable prompt prefix.
  Department forks and playbooks arrive only through `BRIEF.md`, so a tailored role skill
  appears twice: the standard one in the skill list, the fork in the brief.
- **Fix (this repo's half):** the prepared run exports the run's department layer into the run
  directory (`runs/<id>/skills/`) and names it in `brief.json`. The operant half (R-09 there) points
  the skill root at it, or hides the skill list in Holon runs.
- **Guard:** a department fork of `holon-planner` exported for a plan run is the only
  `holon-planner` under the run's skills directory.

### R-11 — the operant adapter's docs describe the previous design

- `adapters/operant/README.md:48` calls policy.json enforcement "a planned phase". Operant
  enforces it per tool call today (roots, commands, budget, deny).
- `adapters/operant/driver.toml:8` says both postures mount the Holon MCP server. Since `38ae9e9`
  the installer drops the mount when the binary carries the native tools.
- **Fix:** correct both at the next installer change, in the same commit.

### R-20 — two Notion receivers, one pusher

The operant gateway can host the webhook (`holon_notion.py apply`), but the outbound push loop
lives only in `holon_notion.py serve`. A plan that retires `holon-notion.service` in favour of the
gateway stops the mirror. **Fix:** document in `adapters/notion/README.md` that `push`/`reconcile`
must run somewhere (the service, or a runtime's clock running `holon_notion.py push` on an
interval), and that one box has exactly one webhook receiver.

## 2. Design changes: wait for the Chair's decision

These come from the same audit. Each names the smallest shape that fits the kernel's laws, so a §1
fix leaves room for it.

| Id | Change | Shape | Laws it touches |
|---|---|---|---|
| C1 | **surface units** | unit manifests with `category = "surface"`, `pool_of = ["*"]` (every brief), `owner` (the department that keeps it true), and per service: `effect` (read \| write), `note` (when to prefer this route), `args`, `output` (toon \| json \| text), `health` (a command). Lint: a surface service needs `effect` and `note`; `ref.command` must resolve every entry point. | migration (new columns), GENOME §6/§14, read API v4 (§3) |
| C2 | **the capability block per posture** | one line per reachable route, with its note and last health, rendered at plan/research/shape/execute/verify and for department-less work (`*` surfaces). Observe stages list only routes the posture can take, so a read-only stage is not shown write services or shell-only routes its harness refuses. | context bundle budget order |
| R-04 | **the runtime indexes the registry** | a view `api_pool(unit, dept)` and the C1 columns on `api_services`, so a runtime can register each pooled service as an on-demand tool. The brief keeps the one-line summary; the full schema lives in the runtime's tool index. | read API v4; CONTRACT §8/§9 |
| C6 | **the single-injector law, stated for runtimes too** | CONTRACT: the kernel's bundle is the only org guidance in a run; a runtime may index what the kernel declared, and adds no workspace, memory or lesson context of its own in a Holon run. | CONTRACT §7/§8 |
| R-13 | **holarchic config** | the department tree carries nothing today (`parent_id` is used for escalation only, `holon/execution/org.py:135`). Proposal: additive inheritance down the tree for roots, pooling, checks and secrets refs ("lists add, never replace"), plus ancestor departments in the memory scope list; and units with an `owner` department for engines that have data and a CLI but no lifecycle of their own. | GENOME §6/§8, memory scopes |
| R-14 | **a document knowledge base** | memory.db holds atomic items only. Proposal: Markdown with frontmatter as the source, a derived `kb.db` (FTS5, links, status) rebuilt by `holon kb sync` (one writer, like `genome sync`), collections and their status vocabularies declared in the owning unit's manifest, `holon kb search\|get\|upsert\|promote\|link`, a "Knowledge base" bundle section, and a native `holon_kb_search`. Stdlib-only frontmatter parsing. | laws 1, 3, 6; a new capability module |
| R-16 | **a complete system-design skill** | `holon-system-design` covers profile, create, hire, fork and autonomy. Add references for units and pooling, reach/secrets/checks/smoke, routines/lanes/import, playbooks and rubrics, budgets, delivery and migration; plus lint warnings for a department that pools units with an open fence, or a task class with no playbook. | skill depth rubric (`holon lint` pack depth) |
| N-1 | **Notion: what needs the Chair** | a status page rendered from `holon status` on each push; data sources for Routines (enable as a Chair input), Incidents (acknowledge), Capabilities (units, surfaces, health); the mirror window before page churn grows. | `tests/test_notion.py` column accounting |

## 3. Cross-repo order (the read API is the trap)

`api_meta.api_version` changes whenever a view's columns change (CONTRACT §9), and **operant
refuses any version it does not know**: its `KNOWN_API_VERSIONS` is `["1", "2", "3"]`, and an
unknown version turns org mode off. Therefore:

1. §1 items ship first. None of them touches a view. R-06 is additive in `policy.json`.
2. Before any C1/R-04 migration bumps to `api_version = 4`, operant adds `"4"` to
   `KNOWN_API_VERSIONS` (reading the v1–v3 shapes unchanged). Then the kernel's migration lands,
   with `pack_api` in `holon/integrity/lint.py` taught v4's columns.
3. Operant's registry-indexing work (its R-04) lands after the kernel publishes v4.

## 4. How this audit was done (re-use the method)

1. **A design record's claim about code is a hypothesis.** R-01 was cited by a design record as
   working. Opening the cited line showed the block's real scope. Check control flow
   (indentation, early returns), not the condition text.
2. **Trace one value end to end across every consumer.** R-06 is the same `roots` list read with
   four different meanings. `grep` the field, not the function, and tabulate what each consumer
   assumes.
3. **Grep the other side of every seam.** A published view nobody reads (no runtime reads
   `api_units`/`api_services`) means a declared capability reaches no agent.
4. **Ask what an "empty means off" default does when it turns non-empty** (R-05). The safe fix
   and the outage are one line apart.
5. **Read the deployed binary's logs, not only its config.** A runtime that warns "key ignored"
   has silently voided an adapter's guarantee (R-08).
6. **Inspect live state read-only, and run `doctor` before concluding.** Open SQLite with
   `mode=ro`. During this audit a missing stamp file looked like a dead clock; the runtime's
   doctor showed a fresh tick (the stamp lives elsewhere). Report only what the probe confirms.
7. **Docs lag code in both directions.** READMEs, adapter comments and "do not change"
   preference sections all trailed the code here. When they disagree, code and live state win.
   Fix the doc in the same commit as the code it describes.
8. **Label every finding CONFIRMED or PLAUSIBLE, and say what you did not trace.** A plausible
   item is a test to write, not a fix to ship.

**Execution status (2026-10-17, after a red-team re-audit of this list).** §1 is executed and
verified — every fix traced to its consumer, every guard reverted once to watch it fail: R-01
(`test_pooled_units_join_the_reach_and_render_in_briefs`, plan covered; three pooled units at
plan cost 2014 of 24000 chars, no Context gaps), R-06
(`test_pooled_roots_are_read_only_in_policy_grading_and_isolation`; `holon route` names only
the writable reach — a pooled root belongs to its unit's owner — and a department whose only
reach is pooled runs its smoke checks in the instance root), R-05
(`test_a_department_that_pools_but_lists_no_commands_is_never_derived_a_fence`; the re-audit
found the fix had covered only `commands` — a department fenced solely by `act_commands` got its
act fence without the pooled binaries; fixed in
`test_a_fence_that_exists_only_in_act_commands_still_names_the_pooled_services`), R-08/R-10
(`test_operant_installer_isolates_and_writes_both_postures`; operant iters 880/881 hold the
runtime side), R-09 (`test_a_run_carries_its_departments_resolved_skills`; operant iter 882
reads the run's skills directory), R-11 and R-20 (the adapter READMEs). §3 steps 1–2: operant
accepts `api_version 4` (iter 885, `a_v4_kernel_is_read_and_an_unknown_still_refuses`) while
the kernel still publishes v3 — the v4 migration waits on the Chair with C1. §2 is untouched,
waiting on the Chair.
9. **A defect fixed is a guard test plus a ledger row** (AGENTS.md). Without both, the next
   refactor reintroduces it.
