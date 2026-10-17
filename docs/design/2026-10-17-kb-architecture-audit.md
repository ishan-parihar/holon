---
status: proposed
date: 2026-10-17
description: "The work list from a red-team audit of the kernel as a knowledge base: what an agent can write, read, join and synthesize across the organization's stores — the defects fixable now, the design changes that wait on the Chair (a decision packet with recommendations), and the method that found it."
tags: [audit, knowledge-base, memory, skills, context, episodes, worklist]
---

# 2026-10-17: the knowledge-base audit — a work list for this repository

Status: **proposed work list** (published scrubbed; the decision packet with the Chair's
pending rulings is reproduced below). Found by tracing every store the kernel calls
knowledge end-to-end — write path, read path, and what an agent actually reaches at
runtime — at the checkout of 2026-10-17. Findings are labeled `confirmed` (code evidence)
or `plausible`. Item ids: KB-n are defects, R-n are rulings requested of the Chair.

**How to use it.** §1 items are defects: fix them now, tests first (AGENTS.md law 9), each
with a guard test and a regression-ledger row in the same commit. §2 items change a
mechanism's design: do not build them until the Chair adopts the direction. §3 is the
order. §4 is the method — read it before re-auditing.

## 0. The terrain (what the audit covered)

| Store | Format | Writer | Agent read path |
|---|---|---|---|
| memory.db — items, episodes, project_state, links, context_log | sqlite + FTS | `capabilities/memory.py` (only writer) | bundle recall/steering/state; `memory search/add/context`; MCP |
| work.db — tasks, outputs, plans, runs, messages, events | sqlite | `execution/work.py` (only writer) | bundle task/stage/feedback; `task show/tree`, `plan show`, `events` |
| skills (3 layers: `@dept` → instance → stdlib) + playbooks | markdown + frontmatter | steward/Chair; kernel bumps playbook versions | role brief + playbook in bundle; `skill list/show/invoke` |
| trajectory.db, logbook.db | sqlite, external, read-only to the kernel | outside the kernel (none exists on the audited instance) | bundle only: 12-node upward lineage; FTS top-5 logbook rows |
| law docs (INTENT/GENOME/CONTRACT, decisions, register) | markdown | humans | **none at runtime**; hand-distilled copies inside skills |
| genome manifests → registry | toml → derived md | Chair via `genome sync/emit` | pooled-units section of the bundle |

The three pillars that make the KB a capability, not a bolt-on: the **scope hierarchy**
(`emp:` → `dept:` → `org`, promotion automatic on every execute consolidation, plus the
cross-cutting `class:<name>` and project scopes), the **context bundle** (one
deterministic, budgeted, fail-open join of 15+ sources), and the **playbook bridge**
(markdown protocol versioned against sqlite evidence). The defects below are all at the
read and authorization edges; the write side is sound.

## 1. Defects: fixable now, no decision needed

### KB-1 — cross-domain recall is the bundle's first casualty

- **Where:** `holon/capabilities/context.py:332-347`. Sections render in priority order
  within `[context] budget_chars` (default 24_000); recall, wider recall, related
  episodes and logbook are the **last** sections, so a large plan, gold standard or
  lineage crowd them out. The drop is logged to `context_log` and rendered as "Context
  gaps" at the **bottom** of the bundle — the agent rarely learns what it never got.
- **Why it matters:** the integrative KB input degrades exactly on the largest tasks,
  the ones needing synthesis most. Cross-scope recall (`context.py:319-325`) is the only
  unscoped search a run receives.
- **Fix:** reserve a slice of the budget for recall (as steering already effectively
  holds its place), and render "Context gaps" at the top of the bundle when non-empty.
- **Guard:** compile a bundle whose stage material alone exceeds the budget; assert the
  "Relevant memory" and "Possibly relevant" sections survive and that the gaps block is
  rendered before the task material.
- **Pitfall:** the reservation shrinks what large stage outputs can carry; check
  `holon memory context` on a work item with a gold standard and a full plan before
  settling the slice (suggest 15% floor, `[context] recall_floor_chars`, documented in
  GENOME §6).

### KB-2 — episodes are write-rich, read-poor

- **Where:** every execute run writes an episode (`memory.py:178-195`); the only readers
  are the own-task and related-episode slices in the bundle (`context.py:286-288, 326-329`)
  and `playbook_evidence` aggregates. `memory search` (`cli.py:886-890`) covers items
  only; no CLI or MCP command searches episodes.
- **Why it matters:** "what have we tried on problems like this" — the core synthesis
  query over past outcomes — is answerable only through the one pre-chosen bundle slice.
- **Fix:** `memory episodes <query> [--scope] [--limit]` over the existing
  `search_episodes`, added to MCP's `AGENT_COMMANDS` and the CLI registry (actor `any`,
  read-only).
- **Guard:** a test writes episodes in two scopes, asserts a query finds them across
  scopes and respects `--scope`; assert `mcp.tools()` lists the new tool and the broker
  allows it.

### KB-3 — the agent command fence drops the kernel's own synthesis commands

- **Where:** `mcp.py:26-28` (`AGENT_COMMANDS`) and `cli.py:30` (`BROKER_EXTRA`) omit
  `playbook list`, `playbook show` (protocol + rubric + per-criterion evidence + history —
  the refinement loop's raw material), `events` (the audit trail) and `skill path`.
  All four are actor `any`, read-only, and exist in the registry.
- **Why it matters:** an executor or verifier refining a playbook through MCP cannot
  read the evidence its refinement is supposed to learn from; it gets the bundle's
  frozen snapshot instead.
- **Fix:** add the four commands to both lists.
- **Guard:** assert `mcp.tools()` exposes them and `cli.main` with `broker=` accepts each
  (the broker fence test pattern already exists).

### KB-4 — no drift check between the law docs and their distilled copies

- **Where:** nothing in `holon/` reads `docs/INTENT.md` at runtime; agents receive
  hand-distilled copies (`holon/skills/holon-employee/references/organization.md`, role
  briefs). Lint's BIND pack checks mechanisms→INTENT (one direction); nothing checks the
  copies→source.
- **Why it matters:** the constitution agents operate under can silently diverge from
  the constitution.
- **Fix:** a lint pack (REF family) that extracts each distilled copy's factual claims
  (scope list, stage order, promotion rule, grade names) and checks them against
  `vocab.py` and the INTENT mechanism table — the same generation lint already does for
  schema and config keys.
- **Guard:** a test that edits `organization.md` to contradict `vocab.py` (e.g. a seventh
  role) fails lint.
- **Pitfall:** keep the check structural (enumerations, table rows), not prose
  similarity — free-text comparison would be flaky.

### KB-5 — search output strips source weight

- **Where:** `cmd_memory_search` renders only id/scope/kind/content
  (`cli.py:890`); `confidence`, `provenance`, `uses`, `pinned` are dropped. The bundle's
  `_fmt_items` shows kind and pinned only.
- **Why it matters:** an agent cannot weigh a Chair-pinned constraint against a
  low-confidence guess, and cannot follow provenance (`work:<run>` / `chair` /
  `promoted:<ids>`) to the source run.
- **Fix:** include confidence and provenance in both renderings (json already carries
  them).
- **Guard:** assert the text line of a search result carries both fields.

### KB-6 — the Chair's manual promote loses its graph edge

- **Where:** `cli.py:911-916` (`memory promote`) writes via `add_item` with provenance
  `promoted:<id>` but no `links` row; `promote_recurring` writes `derived_from`
  (`memory.py:295-298`).
- **Why it matters:** provenance of a promoted item should not depend on which path
  promoted it; R-4's lineage reader needs the edge in both cases.
- **Fix:** write the `derived_from` link in the promote path too.
- **Guard:** promote an item, assert the links row exists.

## 2. Design changes: waiting on the Chair (the decision packet)

Each item carries the recommendation the audit reached; nothing is built until the Chair
rules. **R-0 and R-3 are one seam** — read authorization and write authorization must be
decided together (see R-0).

### R-0 — the scope-fence seam (closes the D-class write hole; rules the reads)

**Defect it closes (confirmed):** `cmd_memory_add` (`cli.py:871-881`) fences an
employee's `emp:`/`dept:`/`org`/pin writes — but `class:<x>` and project refs are free
text, so any employee can write steering kinds (`constraint`/`decision`/`preference`)
into any task class (read as "follow these" by every run of that class, in every
department) or into another project's scope. The organic writers of shared scopes today
are Chair provenance only (plan-review notes, consolidation); the unfenced direct write
is the outlier and the cheapest poisoning path through the trust boundary.

**The coupled read question (confirmed):** `memory search` is actor `any` with no scope
filter (`cli.py:886-890`; `Kernel.__init__` hands the plain `Memory` handle,
`service.py:239`) — an employee reads every scope, including colleagues' personal
lessons. This default-open read is **load-bearing**: it is the premise of R-3(a) (facts
are found cross-scope rather than promoted) and of cross-domain synthesis itself. The
`holon-memory` skill teaches narrowing as practice, not enforcement.

**Options (write fence):** (a) identity fence only — `class:` scopes limited to task
classes the writer's department performs, project refs to the writer's own work;
(b) hybrid — (a) for non-steering kinds, steering kinds outside own scopes become
consolidation-or-Chair-only; (c) `class:` never directly writable.
**Options (read):** (i) keep default-open org transparency (recommended); (ii) fence
personal `emp:` scopes to owner + head + Chair — if (ii), R-3(a) is false and
fact promotion (R-3b) becomes necessary.
**Recommendation: (b) + (i).** Guard: an employee cannot add a steering item to a foreign
class scope; consolidation still can; the search path is asserted unchanged.

### R-1 — a joined-recall surface (`holon recall "<question>"`)

Today synthesis is split: the bundle pushes one pre-chosen slice per run; `memory search`
pulls items only. Recommendation: one deterministic command joining items + episodes +
skill-protocol text (in-memory FTS scan of the resolved layers; no index, no new store) +
playbook rubrics + gold standards, budgeted like a bundle section, actor `any`, exposed
through MCP. ~100 lines, no schema change. It also logs recall-vs-use, which is the
sensor R-2 needs. Rejected alternative: MCP-only tool (fragments the surface for no gain).

### R-2 — the ranking ceiling

Recall is bm25 over OR'd words ≥3 chars (`memory.py:56-66`); ranking has no semantics and
no scope-proximity tie-break. Options: (a) keep lexical; (b) the `[classifier]` Decider
re-ranks top-N lexical candidates at bundle compile and `recall` — **reorders only, never
drops** (satisfies the decisions rule that a model may only make the organization more
careful; the deterministic lexical top-k is the floor; the outage safe-default pattern is
already guarded by `test_a_decision_model_that_is_down_is_never_silent`); (c) embeddings
(rejected: new service dependency). Recommendation: (a) now — the KB is young; revisit
(b) when R-1's sensor shows real cross-domain misses at volume (the pending import of
external pools is the expected trigger).

### R-3 — promotion beyond lessons

Only `kind=lesson` auto-promotes (`service.py:1059-1074`, threshold
`[memory].promotion_threshold`, both levels in one pass). Steering kinds reach wider
scopes only through the Chair — the gate is the feature (auto-promoting a `constraint`
would launder one team's rule into org law with no approver). Recommendation: keep
lesson-only **given R-0 rules reads open**; if reads are fenced, revisit fact promotion.

### R-4 — the links graph: bind or mark planned

`links` declares five relations; only `supersedes`/`derived_from` are ever written and
nothing reads the table — no SELECT, no command, not in the `api_memory` view
(`migrations/memory/0001_init.sql:43-48`). `depends_on`/`contradicts`/`about` bind to no
mechanism (law 7 in spirit). Recommendation: bind the two live relations with a
`memory show <id>` lineage reader (supersedes chain back, derived_from sources,
promoted-from scopes; needs KB-6's edge); mark the three dormant relations planned in
docs (no migration churn narrowing a CHECK a later mechanism may widen). Deferred: a
`contradicts` mechanism (verifier/judge findings write conflict edges, doctor reports
unresolved pairs to the Chair) — the deepest cross-scope primitive, but a new write path
through the judgment machinery that deserves its own record once the KB has volume.

### R-5 — the law layer reachable at runtime

Nothing in `holon/` reads the law docs; agents see hand-distilled copies (KB-4 checks
their drift). Options: (a) human-only + KB-4's lint; (b) plus `holon law show
intent|genome|contract` — serves the distribution's own docs read-only (actor `any`,
through the broker/MCP; instance and overlay material is structurally out of reach).
Recommendation: (b). ~20 lines; the source itself is served, so no second copy to drift.

### R-6 — the strategy/logbook seam: contract it while dormant

`trajectory.db` and `logbook.db` are read-only external stores joined by exactly two
fail-open SELECTs (`context.py:38-89`); their schemas exist only in those SELECTs —
implicit, versionless, and (on the audited instance) **the files do not exist**: the
seam has never run against a real artifact. Recommendation: document the two schemas as
a versioned contract (an `api_version`-style meta row the kernel checks, a doctor
clause, a lint pack) so the day an external writer appears, drift cannot fail silently;
add read commands only when one does.

## 3. Order

1. §1 now (KB-1..KB-6), tests first, ledger rows in the same commit.
2. R-0 first of the Chair items (it closes a live poisoning path), then R-1/R-4/R-5 as
   one stream, R-6 as docs+doctor.
3. R-2 waits on R-1's sensor and on the KB growing (the pending import of external
   pools is the volume event); the deferred `contradicts` mechanism waits with it.

## 4. Method (read before re-auditing)

Trace, do not read claims: one value through every consumer (a scope means "writable"
to the fence, "readable" to search, "steering" to the bundle); grep the other side of
each seam for the reader before believing a store is reachable (the `links` table and
the episode corpus both looked alive from the write side); open the fence code itself
(`cli.py` handlers + `AGENT_COMMANDS`/`BROKER_EXTRA` + `cli.main`'s broker path) because
the registry's actor classes are not the whole authorization story; and check the live
instance's store *existence* (mode=ro / ls) before drawing conclusions from schemas —
an absent store degrades fail-open and shows up only in `context_log`.

## Addendum (2026-10-17, red-team against the memory-infra boundary)

The Chair's red-team challenge ("the operant runtime already integrates memory-wire; prove
P0 creates no redundancy") traced the boundary as built. Verdict: **P0 stands unchanged** —
KB-1..KB-6 concern the kernel's own KB reachability and overlap nothing in the runtime. The
red-team found the boundary leaks instead, at the *body-memory* side, all three confirmed
against live state (`adapters/operant/install.py`, `memory_wire.rs`, `~/holon` live):

- **KB-7 — the body's memory store is outside the backup scope.** `integrity/backup.py`
  copies work.db, memory.db, holon.toml and the `config`/`skills`/`genome` trees; the live
  store `adapters/operant/home/memory/memory_wire.sqlite` (+ WAL) is none of those. An
  instance migrated by `holon backup`/`restore` silently loses the bodies' working memory
  — the one seam the re-architecture promises. Fix: decide it (add `adapters/*/home` to
  TREES, or declare body memory disposable and gc it); the store grew unchecked today
  (206 KB WAL for 44 runs; `gc` touches only run dirs and messages).
- **KB-8 — one bank for every body.** The runtime's provider takes a `storage_dir` and
  lands on `DEFAULT_BANK` (`memory_wire.rs:248-272`), so every employee's session memory
  shares one namespace inside the instance home: no per-employee scoping, no portability,
  cross-recall between colleagues' working contexts. The runtime already has a per-seat
  scoped variant (its plan-11E seam) that the adapter does not use. Fix: bank the provider
  per employee (or per run) at the adapter — the identity and portability law's
  operational half.
- **KB-9 — the two-surface routing is unenforced.** Bodies in a run hold operant's memory
  tools (writing the shared body store) *and* the holon memory commands (writing the
  institutional store), and the `holon-memory` skill says which to recall — but nothing
  constrains writes: an institutional lesson retained into the body bank is stranded where
  no scope, promotion, audit or supersession reaches it. Fix: brief-level routing rule +
  the skill's write section naming what belongs in each store; KB-8's per-employee
  banking makes the body store non-organizational by construction.
- **KB-10 (latent, not P0) — isolation vs the body store's path.** The store lives inside
  the instance tree (`adapters/operant/home/memory`); whether the agent user may write it
  is decided by `isolation.reach()` when the boundary lands. Today isolation is off on the
  live instance, so nothing is broken now; verify the grant when R-08 lands, and do not
  grant the adapter home by default.

The boundary itself (runtime = the body's working memory; kernel = institutional memory,
boundary doc S1/S3) is correct and stays. The work is making it enforced: KB-7/8/9 join
§1 as fixable now (KB-8's exact shape — per-employee vs per-run banking — is a
Chair-ruling-sized choice; the recommendation is per-employee).