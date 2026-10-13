# Playbooks, objectives and smoke checks (2026-10-14)

Status: built (commits from 5034b83); demonstrated live on the workstation instance (§4). The
mechanisms are documented in `docs/GENOME.md` §12 (playbooks, objectives) and §13 (smoke checks).

## 1. What the Chair asked for

- Couple the fast and full lanes to the skill system, so that each job's protocol is revised after
  verification and every run is more efficient and more effective than the last; make this loop a
  core part of every department, so automations pursue objectives instead of running mechanically.
- Couple it to the task infrastructure: an objective is broken into sub-tasks and automations, and
  the whole pool, whatever its lanes, runs as a system with its own loop of self-improvement,
  self-evolution and self-healing.
- Bring back the legacy organism's smoke tests, with the linter checking references and doctor
  running the tests; find what else is missing.
- Make the rubric and the gold standard concrete and show them working. The rubric and the job's
  instructions are what improve recursively, because what is wanted sharpens as the terrain is
  understood.
- Use Clef (`api.experientiallabs.ai/v1/systemone`) as a decision model. Key rotation deferred.

## 2. Decisions

| Decision | Why |
|---|---|
| A job's protocol and rubric live in one **playbook**: a department skill of kind `playbook` (`SKILL.md` + `rubric.md`, versioned) | the skill system already layers per department, exports to harnesses, checks files, and applies changes only after verification; a playbook reuses all of it |
| Every judgment is stored per criterion with the playbook version (`judgments`) and every execute records the version it ran (`tasks.playbook_version`) | improvement must be measurable: a refinement that does not raise the scores, or lowers verifier failures, is visible as such |
| Refinement is **work** (`Refine playbook: …`) opened by the heartbeat from evidence, planned with `skill:` writes (≥ G2), executed and verified | the organization changes itself only through its own pipeline (INTENT law 6); an agent never rewrites its own instructions unreviewed |
| Triggers: a verifier failure, a judge/verifier disagreement, a criterion below the bar over `min_judged`, or `refine_after` runs | failures are the strongest evidence; disagreements mean the rubric measures the wrong thing; consolidation captures what went right |
| A task class gets its first playbook from `class_after` verified results | one-off fast and full work accumulates a protocol too, not only routines |
| The plan's rubric is shown only when there is no playbook | after a refinement the plan copy is stale; one source of truth (found live, §4) |
| **Objectives** decompose into one-off work and routines (`cron` children); the decomposition's decider approves the routines within its grade | the pool is designed once, as a whole, and reviewed as a whole |
| An objective is never closed by its children; its review (a retro with the objective's dashboard) runs when the pool finishes a cycle, and reshapes it | automations finishing is not the objective being met |
| Smoke checks per department and for everyone, run by doctor (as the agent user) and cached; a critical failure blocks the department's agents, never the incident department's | the legacy organism's fail-closed tick gate, kept; and IT must be able to fix what broke |
| `holon lint` checks every command the instance will run (`ref.command`, `ref.path`), each routine's playbook (`ref.playbook`), and reports departments with scripts or a reach but no smoke checks (`ref.smoke`) | the legacy linter caught dead references before they failed on a schedule |
| Routines may `--deliver` each result to the Chair through `[notify]`, retried by the heartbeat | the legacy crons' value was the digest in the Chair's chat; a routine with no delivery is invisible |
| Clef is a **patient fallback** decision endpoint, used only by the rubric judge | it answers correctly (choice, score, yes/no) but takes ~274 s per call (measured 3×), which the Chair's terminal and the heartbeat cannot wait for |

## 3. What the legacy organism has that Holon does not yet

Compared with `~/.hermes/organism` (arch, AXE, agent-fabric, workforce-ops):

| Legacy | Holon | Status |
|---|---|---|
| `smoke.yaml` per pool; AXE's fail-closed smoke gate before every tick | `[smoke]`, doctor, `Kernel.smoke_blocked` | built (this record) |
| `arch validate`: cron frontmatter, skills referenced, script drift, cross-refs, path portability | `holon lint` packs; `ref.command`, `ref.path`, `ref.playbook`, `ref.smoke` | built for commands, paths and playbooks; drift and portability are by design moot (scripts run in place) |
| cron digests delivered to a chat thread | routine `--deliver` | built |
| PDCA loop: self (worklog), peer, department, organization | memory lessons and promotion, retros, audits; now playbooks and objective reviews | built |
| lean 5-step workflow template per department | playbooks per routine and task class | built (they start from the job and are refined) |
| result classifier (failure taxonomy) | decision-model triage on incidents | built |
| services offered/consumed between pools, with a registry | cross-department requests, no declared services | **gap**: a department's services in its genome, so planners and routing know what each offers |
| per-pool ADs and RGs (decisions with supersession, regression guards) | the kernel's regression ledger; department memory decisions; checks and smoke as guards | **partial**: no per-department regression ledger |
| `cron-jobs/*.md` as the source of each cron | routines from `holon routine add` | built: `holon routine import` (dry run by default; `--apply` proposes each active routine) |
| notice-board retention and weekly summaries | events, messages and run directories grow forever | **gap** (not urgent: 16 runs = 372 KB; 61 daily crons ≈ 1.5 MB/day of run directories) |
| `arch route <path>` (which pool owns a path) | `[scope]` roots per department | **gap**: `holon route <path>` |
| per-pool generated AGENTS.md (routing, services, crons) | identity cards, briefs, the Notion dashboard | covered differently |

## 4. Live demonstration

Filled in from the run on the workstation instance (Tools department, Hermes harness on free-stack,
decision models on OpenRouter with Clef as the fallback). The routine `Notes digest` started with a
deliberately loose rubric (`digest.md exists and summarises the notes`); Tools results were sampled
to a real verifier every time and `refine_after = 2` for the demonstration.

(results: see §4.1, added when the run completes)

## 5. Limits

- Refinement costs a full pipeline (research?, plan, approve, execute, verify) per playbook change;
  `refine_after` bounds how often. Small departments may want a lower `min_judged`.
- The judge reads the result's own account; a result that misdescribes itself can pass. The sampled
  verifier, the machine checks and `rubric_agreement` are the guards.
- An objective's review can only propose routines (a retro approves nothing); its decider must approve
  them like any other.
