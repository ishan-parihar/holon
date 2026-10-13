# Status, context and standing decisions

Read this after INTENT. It says where Holon sits in the Chair's wider system, what is deployed,
which decisions the Chair has already made (do not re-open them), and what comes next. Update it
in the same commit as any change it describes. Hosts, ids and keys stay out of it (AGENTS.md).

Last updated: 2026-10-13.

## 1. Where Holon sits

- **The Chair** runs a personal agentic ecosystem, the Hermes **organism**: system pools grouped into
  strata (`cortex`, `swarm`, `foundations`, `ventures`) under a repository law (`arch`, ADs and
  RGs). Its ventures (career pipeline, freelance agency, quant trading, SaaS studio, brands,
  research, community and others) run on agent crons today.
- **Holon is the operating system meant to run that organization**: departments, persistent
  employees, one work graph, approvals and learning loops. The organism's ventures move into Holon
  as departments only after the Chair validates Holon (§3). Until then they are separate systems that
  use the same words (`docs/design/2026-10-13-system-red-team.md` §5).
- **Harnesses** are the employees' bodies. ZeroClaw is the production body; Hermes and jcode are
  validated alternatives; Claude Code is deferred. All run models through **free-stack**, a local
  OpenAI-compatible gateway; it is the only model pipeline.
- **The Chair's dashboard** is Notion: `adapters/notion` mirrors departments, employees, work and
  runs, and takes the Chair's decisions, notes, answers and new work back.
- **Goals** live in the Compass strategy system (`trajectory.db`, read-only to Holon); work links to a
  goal through `project_ref`.

## 2. Deployments

| Instance | Role | State |
|---|---|---|
| VPS | production; canonical for the organism's data | ZeroClaw adapter; first supervised wave since 2026-10-12; heartbeat on; runs the build from the morning of 2026-10-13. Later fixes are **not deployed**: the redeploy is deferred by the Chair. |
| Chair's workstation | development and validation | `~/holon`; jcode adapter (conformance 5/5); Notion mirror with webhooks through a dedicated tunnel, both as user services; heartbeat installed but **off** |

Deploying to an instance: pull, run the tests there, `holon doctor`, re-run any adapter installer
whose inputs changed (AGENTS.md).

## 3. Standing decisions (Chair)

| Date | Decision |
|---|---|
| 2026-10-12 | Org vocabulary is intentional (CEO, heads, employees, hire/fire, reviews); every term binds to a mechanism. No contractor agents. |
| 2026-10-12 | One holarchic work graph (tasks, parent, needs); six roles; `light` profile for small departments; safe-default autonomy. |
| 2026-10-12 | Job and freelance work run only as a temporary pilot department, for testing. Divisions are not canonicalized until the core OS is done. |
| 2026-10-13 | Keep hermes-agent until the ZeroClaw cron migration is done; never modify it on the VPS. Legacy organism migration starts only after the Chair validates Holon. |
| 2026-10-13 | First wave in supervised mode; per-department scope. |
| 2026-10-13 | free-stack is the only model pipeline; Claude Code is deferred. |
| 2026-10-13 | Work locally until robustness is verified; the VPS redeploy waits for the Chair. |
| 2026-10-13 | Notion is the dashboard: SQLite and Notion stay in sync both ways, schema typed (selects, relations), webhooks through a tunnel on the Chair's domain, with the edited-since poll as the fallback. |
| 2026-10-13 | The workstation instance runs the Notion sync and tunnel; the heartbeat stays off until the Chair turns it on. |
| 2026-10-13 | Trust boundary (G1): separate Unix users and a kernel broker, not per-harness filesystem confinement. |
| 2026-10-13 | Lanes (G3): script, then a fast single session, then the full pipeline, cheapest first and enforced at intake by a classifier; each routine has a rubric, and its last verified run is its gold standard. |
| 2026-10-14 | Routine architecture approved. Fast and full lanes are coupled to playbooks that refine their protocol and rubric after verification; objectives decompose into pools of work and routines that review themselves; smoke checks gate departments as in the legacy organism. |
| 2026-10-14 | Clef (`api.experientiallabs.ai/v1/systemone`) is a decision-model endpoint; it answers in ~274 s, so it is the patient fallback (rubric judging), not the first endpoint. Key rotation deferred: trusted environment. |
| 2026-10-13 | Decision models (classifiers) run on OpenRouter's decisions endpoint (`[classifier]`), outside free-stack; free-stack stays the only pipeline for generative models. |

## 4. Rules for anyone working on Holon

- Never print, log or commit a secret. Read keys from files into the environment or a 0600 file.
  No agent ever gets the Chair key.
- Never delete data that cannot be regenerated or is not backed up.
- Pilot tasks never send outreach, applications or email.
- Anything outward-facing or hard to reverse (deploys, tunnels, DNS, enabling timers, changes on
  the VPS) needs the Chair's approval first.
- `pgrep -f` / `pkill -f` match the calling shell's own command line; use a bracket pattern
  (`[h]olon …`).

## 5. Open decisions for the Chair

- Redeploy the VPS with everything since the morning of 2026-10-13.
- Turn on the workstation heartbeat (`holon-tick.timer`).
- Run the one-time isolation setup on the workstation (root creates `holon-agent` and its runner;
  `docs/design/2026-10-13-trust-boundary.md` §3). Until then `holon doctor` warns `isolation.off`.
- Decision-model quota: OpenRouter's free tier allows 50 decisions a day (1000 after a one-time
  10-credit top-up); the Cline API cannot serve these models.
- Which venture moves first, as a pilot department of routines (`docs/design/2026-10-13-lanes-and-decision-models.md` §4).

## 6. What is next

The ranked list is `docs/design/2026-10-13-paperclip-contrast.md` §4. G3 is built (lanes, routines,
rubrics, decision models: `2026-10-13-lanes-and-decision-models.md`) and so is G1 (the agent user,
runner and broker: `2026-10-13-trust-boundary.md`; its root setup waits for the Chair). In order:
intake keys (G4), department secrets (G8), the Notion mirror
window (G2), then one venture as a pilot department of routines.

## 7. Reading order for a new agent

1. `docs/INTENT.md`: what Holon is and the laws.
2. This file.
3. `AGENTS.md`: the code map, the laws lint enforces, the regression ledger.
4. `docs/GENOME.md` (departments, roles, configuration) and `docs/CONTRACT.md` (the harness
   contract).
5. `docs/design/` newest first: `2026-10-14-playbooks-objectives-smoke.md`, `2026-10-13-trust-boundary.md`, `2026-10-13-lanes-and-decision-models.md`, `2026-10-13-paperclip-contrast.md`, then
   `2026-10-13-system-red-team.md`; older records explain why things are the way they are.
6. The README of the adapter you touch (`adapters/<name>/README.md`), which records what the real
   harness does.
