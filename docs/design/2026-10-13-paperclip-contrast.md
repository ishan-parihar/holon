# Paperclip contrast and red-team (2026-10-13)

Status: audit record. The ranked gaps in §4 are proposals; items marked **Chair** need a decision
before they are built. §3 lists what this audit fixed directly.

## 1. What Paperclip is

Paperclip (`agentic-harness/parent-projects/paperclip`, TypeScript, MIT) is a control plane for
companies of AI agents: an Express server with Postgres/PGlite, a React board, a CLI and adapter
packages. "If OpenClaw is an employee, Paperclip is the company." Its docs worth reading are
`doc/GOAL.md`, `doc/PRODUCT.md`, `doc/SPEC-implementation.md` (the V1 contract) and
`doc/execution-semantics.md` (assignment, status, liveness and recovery).

Its core model: companies → goals (company → team → agent → task) → projects → issues
(single assignee, parent/sub-issues, blockers, comments, documents, work products) → heartbeat
runs (process, HTTP or plugin adapters) → cost events, approvals, activity log. Agents run
elsewhere and phone home with scoped API keys.

## 2. Where the two systems differ

| Concern | Paperclip | Holon | Verdict |
|---|---|---|---|
| Trust boundary | a server owns the database; agents hold scoped API keys | the CLI is a library: authority is checked inside the `holon` process, which runs as the agent's Unix user | **Holon gap** (G1) |
| Unit of work | issue with one assignee; review is a policy on the issue | work node with a staged pipeline (research … retro), each stage a separate run by a role | Holon is stricter; costs more per small task (G3) |
| Separation of duties | review policy `anyone / not_creator / human_only` | enforced: planner ≠ approver, executor ≠ verifier, graded routing | Holon stronger |
| Learning | skills studio, evals, instruction revisions | memory per employee/department/org with promotion, earned authority, retros, metrics with counter-metrics | Holon stronger |
| Goals | `goals` table; every issue must trace to a goal | `project_ref` → Compass `trajectory.db` (read-only); optional, unvalidated, no rollup | gap (G5) |
| Liveness | contract: every non-terminal issue has a live, waiting or recovery path; monitors; bounded escalation | leases, back-off, incidents, `graph.stuck`; now `graph.unstaffed` (§3) | partly closed (G6) |
| Recurring work | routines with concurrency policy and revisions | recurring plans fired by schedules; now skip-if-open and stop-on-cancel (§3); approval never re-graded | partly closed (G9) |
| Budgets | monthly per company/agent/project; 80 % alert; 100 % pauses | run counts per department; tokens recorded, never enforced | gap (G7) |
| Secrets | versioned company secrets bound per project/routine | none per department; runs inherit the dispatcher's environment minus Holon keys | gap (G8) |
| Intake | agents and integrations create issues with provenance | `task add` needs the Chair key; employees can `msg request` | gap (G4) |
| Dashboard | the board reads its own database; automation hidden by default | Notion mirror of every work node, stage and run; full scans | gap at scale (G2) |
| Threads, deliverables | comments, documents, work products, attachments | messages (not mirrored), result summaries; files only as paths | gap (G10, G11) |
| Portability | company package (`COMPANY.md`, agents, teams, tasks, skills), import dry-run, timers off on import | genome profiles; no department export/import | gap (G12) |
| Evals | runner and product E2E evals; failure taxonomy (product / model / grading / infrastructure) | `holon adapter test` (protocol conformance) | gap (G13) |
| Harness independence | adapters per runtime | drivers + adapters; the kernel names no harness (lint) | equal |
| Dependencies | Node, Postgres, React | Python stdlib + SQLite | Holon simpler |

What not to copy: Paperclip's surface (chat channels, connectors, announcements, Storybook, cloud
billing) serves a product with many users. Holon serves one Chair; its leverage is in the loops.

## 3. Fixed by this audit

| Defect (found by a probe against the kernel) | Fix | Guard |
|---|---|---|
| A recurring plan fired a new instance on every due tick while the last was still open (3 fires → 3 open instances) | skip the firing, log it on the schedule | `test_recurring_plan_creates_preapproved_work` |
| A recurring schedule kept firing after its work was cancelled | the schedule turns itself off | same test |
| A stage whose assignee is paused or retired, or that nobody in reach performs, stayed `ready` forever; doctor and tick were silent | doctor `graph.unstaffed` (an error, so an incident on the heartbeat) | `test_a_stage_nobody_can_claim_is_reported_not_left_waiting` |
| Nothing backed the instance up; the organization exists only in its databases | the heartbeat runs `holon backup` every `[healing] backup_every_h` (24) into `backup_to` | `test_the_heartbeat_backs_the_instance_up` |
| `docs/design/2026-10-13-system-red-team.md` §4 and §9 contradicted each other (CI, doctor, Notion) | corrected | — |

## 4. Ranked gaps

Ranked by leverage: what the gap blocks (safety first, then running the ventures), divided by
the effort to close it.

### G1. Authority can be bypassed by any agent with a shell (safety; Chair)

The kernel checks run tokens, separation of duties and grades **inside the `holon` process**. That
process runs as the same Unix user as the agent, so an agent with a general shell can read the
Chair key (`~/.config/holon/keys/*.chair`, mode 600, same owner), read the Notion and model
tokens, or write `work.db` directly. CONTRACT §6 asks adapters to keep `~/.config` out of reach;
only ZeroClaw does (`forbidden_paths`); the Hermes and jcode adapters give the agent an
unconfined shell.

- Now: doctor should report a driver that does not declare filesystem confinement while the Chair
  key is readable by the user running drivers (a `confines_fs` driver flag, S).
- Proper fix (L, Chair): split users. The kernel's state, keys and heartbeat belong to a `holon`
  user; agents run as another user and reach the organization only through a broker (a small
  socket service in the kernel) that authenticates run tokens. This is what makes Paperclip's
  API keys real.

### G2. The Notion mirror's cost grows with all history (scale)

`push` reads every row every 20 s; `reconcile` queries every page of four data sources every
15 min; `verify` re-reads every page and relation hourly. Every stage and run is a page. With the
organism's 61 venture crons moved into Holon, each firing adds four to six pages: about 10 000
pages a month, after which a full pass needs 100+ requests at Notion's ~3 requests/s and delays
Chair edits. Close it with a mirror window (open work, plus terminal work and runs from the last
N days; older pages archived, never deleted from SQLite) and an incremental push keyed on
`updated_at` (M).

### G3. Small and recurring work costs four cold agent sessions (throughput; Chair) — built

Built as lanes, routines, rubrics and decision models: `2026-10-13-lanes-and-decision-models.md`.


Plan, approve, execute and verify are separate runs; each harness session starts with a 15–18k
token base prompt. jcode conformance on free-stack: plan 6.2 min, approve 23.3, execute 10.4,
verify 11.3: 51 minutes of agent time for a trivial change, with `max_running_per_dept = 1`.
Proposal: lanes by grade. G0 work in a trusted task class runs plan-and-execute in one run, with
machine checks and sampled verification (the autonomy policy's sample rate); agent approval of
G1 plans becomes a short `holon plan approve` by the head's model rather than a full session.
Size per-department concurrency from the backlog metric (M).

### G4. External intake needs the Chair key (ventures)

Venture scripts (inbox scanners, signal feeds, scrapers) cannot file work without Chair
authority. Proposal: intake keys scoped to a department and its task classes, actor class
`intake`, `source=external`; intake work always enters at shape or plan, so nothing skips approval
(S–M).

### G5. Goal linkage is optional and unchecked (alignment)

Briefs carry "why this matters" from `trajectory.db` when `project_ref` names a node, but nothing
requires a root work to name one, nothing checks the node exists when work is created (a missing
database or node only degrades the brief), and no progress rolls up to a goal. Proposal: validate
`project_ref` on creation when `trajectory.db` is present; doctor warns on open roots without one;
`holon metrics --goal` rolls up verified results; mirror goals in Notion (M).

### G6. Liveness: gates and waits are not aged (operations)

A Chair-gated approval or question is announced once (`notify` only sends new events); nothing
re-surfaces it as it ages. An agent that must wait on an external system (a deploy, a reply) has
no wait-until primitive, so it either finishes early or burns a session polling. Proposal:
re-notify items older than `[notify] remind_after_h` in a digest; a `wait_until` on a stage that
returns it to `ready` at a time (Paperclip's one-shot monitor); doctor checks that a blocked
chain's leaf has a live path (S–M).

### G7. Budgets count runs, not tokens (cost)

Usage is recorded per run (now including stopped jcode runs) but never limits anything. Proposal:
`[budget] tokens_per_dept_per_day`; the existing `over_budget()` exclusion applies; a Chair
notification at 80 % (S). Unknown usage is unknown, not zero.

### G8. Secrets are not scoped per department (ventures, safety)

Ventures need credentials (Telegram bots, mail, exchange APIs). Runs inherit the dispatcher's
whole environment minus Holon keys, and there is no per-department binding. Proposal:
`[secrets.dept.<slug>]` names environment variables and the 0600 files they are read from, loaded
into that department's runs only; doctor checks permissions. Depends on G1 for real isolation (S–M).

### G9. A recurring approval never expires (governance)

A recurring plan approved once fires indefinitely at the grade it had then; a later scope or
configuration change does not re-grade it. Proposal: re-grade on each firing; if the floor rises
above the approved grade, disable the schedule and route the plan back to its approver (S).

### G10–G13 (lower)

- **G10 Threads.** Messages on a work are not mirrored to Notion, and Chair comments there are not
  read. Mirror them into the page body; read Notion comments as Chair notes (M).
- **G11 Deliverables.** Execute results describe files in prose. Add `artifacts: [{path, title}]`,
  checked to exist and mirrored as links (S).
- **G12 Portability.** No department export/import; onboarding each venture by hand. A department
  package (profile, JDs, classes, checks, scope, skills, recurring plans) with a dry-run import
  and schedules off (M). This is also the venture onboarding path (§5).
- **G13 Evals.** Conformance tests the protocol, not judgment. Classify run failures (product,
  model, infrastructure) in incidents and keep verified work as a regression set for planners and
  verifiers (M).

## 5. Running the ventures on Holon

The organism's ventures (`~/.hermes/organism/ventures/*`) each have an `_org.yaml`, `cron-jobs/`
(61 agent cron protocols in total), `smoke.yaml`, scripts and data. A venture maps onto Holon as:

| Organism | Holon |
|---|---|
| venture pool | department (`light` profile until it proves it needs more) |
| `_org.yaml` `services_consumed` | cross-department requests (`msg request`) |
| `cron-jobs/<job>.md` protocol | a recurring plan whose steps are the protocol, approved once |
| `smoke.yaml`, `arch validate` | `[checks.dept.<slug>]` |
| venture directory | `[scope.dept.<slug>] roots` |
| strategy refs / Compass path | `project_ref` (G5) |
| venture credentials | `[secrets.dept.<slug>]` (G8) |

Order: G1 interim check, G4, G8 and G2 before the first venture moves; then one venture as a pilot
department (the Chair's standing rule: job and freelance work stay pilot-only, no outreach).
