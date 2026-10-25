# Bylaws: roles, stages, profiles, skills, autonomy

## Contents

- [1. Roles and stages](#1-roles-and-stages)
- [2. Profiles](#2-profiles)
- [3. Pipeline rules](#3-pipeline-rules)
- [4. Skills](#4-skills)
- [5. Autonomy](#5-autonomy)
- [6. holon.toml reference](#6-holontoml-reference)
- [7. Operations](#7-operations)
- [8. Scope: what a department may reach](#8-scope-what-a-department-may-reach)
- [9. Machine checks: acceptance a command can decide](#9-machine-checks-acceptance-a-command-can-decide)
- [10. Lanes, routines, rubrics and the decision model](#10-lanes-routines-rubrics-and-the-decision-model)
- [11. Isolation: the trust boundary](#11-isolation-the-trust-boundary)
- [12. Playbooks, refinement and objectives: the department's learning loop](#12-playbooks-refinement-and-objectives-the-departments-learning-loop)
- [13. Smoke checks: is the infrastructure there](#13-smoke-checks-is-the-infrastructure-there)
- [14. Units and the bylaws layer](#14-units-and-the-bylaws-layer)

The bylaws is what makes every department the same kind of holon while letting each one be
configured for its purpose. It has five parts, each defined once in code and checked by
`holon lint`: the roles and their stages, the department profiles, the pipeline rules, the skills
library, and the autonomy policy. This page is the reference for all five and for `holon.toml`.

## 1. Roles and stages

| Role | Stage(s) | Posture | Skill | Mechanism |
|---|---|---|---|---|
| head | shape, approve | observe | `holon-head` | `execution.work.Work.submit_shape`, `execution.work.Work.decide` |
| researcher | research | observe | `holon-researcher` | `execution.work.Work.submit_research` |
| planner | plan | observe | `holon-planner` | `execution.work.Work.submit_plan` |
| executor | execute | act | `holon-executor` | `execution.work.Work.submit_execute` |
| verifier | verify | observe | `holon-verifier` | `execution.work.Work.verify` |
| steward | retro | observe | `holon-steward` | `execution.work.Work.submit_retro` |

An employee may claim a stage only if one of its roles performs it. Approve stages are claimable
only by their approver, and verify stages never by the employee who executed the work.

**Task classes narrow the claim further.** `[classes]` names the classes a department's work may
carry (`org` plus the department's own; empty = any). An employee's record may name a subset of
them (`holon hr hire --task-classes`; a name the taxonomy does not carry is refused): such an
employee may *pick up* a stage whose work carries one of *their* classes, and not one that carries
another — work that names no class is anyone's. An employee whose record names no class is not
narrowed.

The gate narrows what an employee **chooses**, never what the Chair **directs**: work assigned to
an employee stays theirs whatever its class, or it would strand — nobody else may claim assigned
work. The gate is in the claim query, so it is atomic: two employees racing for the same stage
cannot both win it. `holon doctor` reports a stage whose class no employee in reach holds
(`classes.unstaffed`) rather than leaving it waiting forever.

## 2. Profiles

A profile names a department's slots; a slot is the set of roles one employee fills. Profiles
ship in `holon/bylaws/` and an instance adds or overrides them in `$HOLON_HOME/bylaws/`.

| Profile | Slots | For |
|---|---|---|
| `light` | lead (head, steward) · maker (planner, executor) · checker (researcher, verifier) | new or small systems: three employees |
| `standard` | one slot per role | sustained load or high-risk work |
| `executive` | ceo (head, planner) · strategy · audit · charter | the executive office, which delegates execution |

Each department keeps a snapshot of its profile and version; lint reports departments on an
older version. `holon dept create … --profile`, `holon dept profile`, `holon dept roles` and
`holon hr hire --slot` work with them. The `holon-system-design` skill is the protocol for
designing a department from its requirements.

## 3. Pipeline rules

Every work node runs `plan → approve → execute → verify`. Three stages are optional, and each
is `auto`, `always` or `never` in `holon.toml [pipeline]`, overridable per profile:

| Stage | `auto` runs it when | Mechanism |
|---|---|---|
| research | the task class has fewer than `novelty_min_verified` verified successes, or risk is high or irreversible, and the department has a researcher | `execution.service.KernelRules.first_stage` |
| shape | the Chair or another department asked for the work (not a decomposition), and the department has a head | `execution.service.KernelRules.first_stage` |
| retro | verification or execution failed, a plan was rejected twice, or the work failed, and the department has a steward | `execution.service.KernelRules.on_rework` |

## 4. Skills

The standard library (`holon/skills/`) holds the protocol every employee loads first
(`holon-employee`), one skill per role with that role's stage briefs (`briefs/<stage>.md`),
and the operation skills: `holon-collaboration` (colleagues and departments), `holon-memory`
(institutional memory), `holon-operator` (the organization itself: adding, removing, refactoring,
expanding, condensing, healing and evolving its systems), `holon-system-design` (designing and
staffing one department). Skills resolve per
file through three layers, first match wins: `$HOLON_HOME/skills/@<dept>/<skill>/`, then
`$HOLON_HOME/skills/<skill>/`, then the standard library, so tailoring one file for one department
leaves the rest untouched and reaches that department's next brief.

Skills evolve through work, on any harness (agents never write the skills directory; one that does
without isolation has the edit taken back when its result is handed in, and held as a proposed
`skill_changes` entry if its plan named the file, applied only when verified): a plan names
`skill:<skill>/<file>` in its writes (floor G2), the executor returns the new file in the execute
result's `skill_changes`, and when the work is verified the kernel writes it into the department's
layer, keeps it only if `skill check` stays clean, and re-exports every directory registered by
`holon skill export`. `holon doctor` warns (`skills.export_stale`) when an export drifts.

`holon skill list | show | path | invoke | fork | new | check | export` manage them; `invoke`
records each use for the steward's metrics. `SKILL.md` frontmatter: `name`, `description` (with a
"Use when" trigger), `kind` (protocol, role, operation), `roles`, `version`. Lint's DEPTH pack is
the rubric: required sections per kind, 120–700 words, a runnable command, stated limits for
roles that act.

## 5. Autonomy

Safe by default; more autonomous only where trust was earned and the Chair says so.

| Key | supervised (default) | delegated | autonomous |
|---|---|---|---|
| `widen_approver`: who approves widening authority | chair | ceo | auto |
| `auto_promote_cap`: highest grade authority can be earned to | G2 | G3 | G4 |
| `ceo_initial_max`: the CEO's authority at hire | G1 | G2 | G2 |
| `promote_after`: clean verified successes per promotion | 10 | 10 | 10 |
| `audit_sample_permille`: employee approvals re-judged by the Chair | 100 | 100 | 50 |
| `verify_sample_rate`: trusted G0 results still verified | 0.2 | 0.2 | 0.2 |
| `trusted_after`: consecutive verified passes before an executor is trusted | 10 | 10 | 10 |

Invariants no mode relaxes: narrowing authority applies at once; widening becomes a proposal
for `widen_approver` and nobody approves their own; work closed without verification never counts
toward trust; the grade floors always apply; the Chair can always decide, regrade, cancel and
change this policy. `holon charter autonomy` shows the effective policy per department.

## 6. `holon.toml` reference

| Section | Key | Default | Meaning |
|---|---|---|---|
| `[context]` | `budget_chars` | 24000 | size of a stage's context bundle |
| `[context]` | `recall_floor_chars` | 3600 | chars of the bundle budget reserved for cross-scope recall (KB-1): sections render in priority order and the recall family is not crowded out by large stage material |
| `[context]` | `unread_cap` | 3 | how many unread messages an employee's brief carries (M1, 2026-10-24): the section renders right after `Your role`, acks on render, and the rest is a count plus the `msg inbox` pointer; 0 turns the join off (and then it acks nothing) |
| `[planner]` | `enforce_read_only` | true | fail observe-stage runs that change the working directory |
| `[planner]` | `trust_harness_read_only` | false | skip that check for drivers declaring `read_only_enforced` |
| `[planner]` | `guard_ignore` | [] | paths (globs relative to the workdir, `dir/**` for a tree) that other processes write, which the read-only check skips; commits by another process never count |
| `[grading]` | `novelty_min_verified`, `default_proposed`, `sensitive_paths` | 3, 2, [] | grade floors (see the planner skill's grading reference) |
| `[autonomy]` | `mode` and the keys above; `[autonomy.dept.<slug>]` per department | supervised | §5 |
| `[pipeline]` | `research`, `shape`, `retro` | auto | §3 |
| `[memory]` | `promotion_threshold` | 2 | colleagues who must learn a lesson before it is promoted |
| `[healing]` | `incident_dept` | it | department that receives incident work (falls back to executive) |
| `[healing]` | `lease_expiries_for_incident` | 2 | expired runs of one stage before an incident opens |
| `[healing]` | `schedule_failures_for_incident` | 3 | consecutive schedule failures before an incident opens |
| `[healing]` | `doctor_every_min` | 30 | how often the heartbeat runs `holon doctor`; each failing check opens an incident (0 = never) |
| `[healing]` | `backup_every_h` | 24 | how often the heartbeat takes `holon backup` (0 = never); a failure opens an incident |
| `[healing]` | `backup_to` | `$HOLON_HOME/backups` | where scheduled backups go; point it at another disk or a synced mount, or a dead disk takes the backups too |
| `[healing]` | `build_regression_samples`, `build_regression_drop` | 10, 0.25 | judgments a harness build needs (per department) before its pass rate counts, and the drop from the previous build that opens incident work; 0 turns the check off (H10) |
| `[retention]` | `runs_days` | 0 (keep) | days after a work closes before `holon gc` collects its run directories; a gold standard's or a judged run's `result.json` and `trajectory.json` are kept (H-14) |
| `[retention]` | `messages_days` | 0 (keep) | days before `holon gc` prunes old messages |
| `[retention]` | `backup_max_age_days` | 7 | `holon gc --apply` refuses without a backup newer than this; `--force` overrides |
| `[retention]` | `backup_keep` | 3 | backups kept past `gc --apply`; the heartbeat prunes the rest, so the newest three survive a failure run |
| `[pilot]` | `depts` | [] | pilot departments: anything the grade floor puts at G3 (outside reach) floors at G4 instead — the Chair approves each job, one by one; imported outside-reach routines also start disabled (I1) |
| `[secrets.dept.<slug>]` | `<ENV VAR>` = path | (none) | a department's secrets (G8): each key names an environment variable and points at a 0600 key file, resolved into the run's environment at claim — the value never lives in config; a key file that cannot be read refuses the run, never runs it with an empty string |
| `[intake.dept.<slug>]` | `grade_floor`, `task_classes` | G1, any | G4, the Chair-key-free external intake: a department's intake key may file work into **that department only**, at or below `grade_floor` (G0–G4) and only in the named `task_classes`. The key is issued by `holon intake key <dept>` and lives with the scanner; only its hash is stored. It can file and nothing else: it never approves, never decides, and reaches no other command |
| `[healing]` | `run_failures_for_incident` | 3 | runs of one stage that ended without a valid result before an incident opens; past it the stage waits 10 min before the next try, doubling per failure (at most a day) |
| `[cadence]` | `dept_retro_days` | 7 | how often a department retro may run over pending proposals |
| `[cadence]` | `dept_retro_min_proposals` | 1 | proposals needed before a department retro is due |
| `[metrics]` | `min_samples` | 5 | samples a rate needs before its bound counts |
| `[metrics]` | `verify_pass_rate_min`, `rework_rate_max`, `rejection_rate_max` | 0.5 each | a rate past its bound makes the department retro due (every `dept_retro_days`) without proposals, and the retro is told which; `_min = 0` / `_max = 1` turns a bound off |
| `[cadence]` | `probation_days` | 14 | days before a new hire's confirmation goes to their head |
| `[budget]` | `max_running_per_dept` | 0 (no cap) | runs a department may have in flight; `[budget.dept.<slug>]` overrides |
| `[notify]` | `command` | "" (off) | run on each heartbeat that finds something new for the Chair; `{message}` is the text (an adapter fills this in, e.g. a chat CLI) |
| `[notify]` | `dropped_marker` | "" (off) | text the command prints when it had nowhere to deliver. Exit 0 is the contract's proof of delivery, but a command that treats "nothing is configured" as a successful no-op exits 0 having delivered nothing. Name that text here and the heartbeat records a failed send and retries the digest instead of advancing its cursor. |
| `[events]` | `cooldown_s` | 300 | an event routine (`routine add --on event:<pattern>`) fires at most once per this many seconds; the runtime that saw a matching event calls `holon routine fire` |
| `[chair]` | `channels` | [] | `<platform>:<user id>` refs whose channel messages are the Chair's (H8); a runtime that fronts a channel may run Chair commands only for one of these — the broker still forbids agents every Chair command |
| `[budget]` | `max_runs_per_dept_per_day` | 0 (no cap) | runs a department may start in 24 hours; its stages wait on the board beyond it |
| `[budget]` | `tokens_per_run` | 0 (no cap) | per-run token cap compiled into `policy.json` (H5); `[budget.dept.<slug>]` then `[budget.employee.<id>]` override, last wins; a harness enforces it and reports stopping in `refusals` |
| `[budget]` | `cost_per_run` | 0.0 (no cap) | per-run cost cap in the invoice currency, same overrides and enforcement as `tokens_per_run` |
| `[scope]` | `roots` | [] | paths every employee may work in beyond its work's workdir; `[scope.dept.<slug>]` adds a department's own (§8) |
| `[scope]` | `commands` | [] | extra shell commands every stage may use (a sandboxing harness grants them) |
| `[scope]` | `act_commands` | [] | extra commands for the execute stage only |
| `[checks]` | `commands` | [] | commands the kernel runs in the workdir after every execute; `[checks.dept.<slug>]` and `[checks.class.<task_class>]` add their own (§9) |
| `[checks]` | `timeout_s` | 600 | per check command |
| `[lanes]` | `fast` | true | allow the fast lane (§10); off sends all work through the full pipeline |
| `[lanes]` | `script_timeout_s` | 600 | how long the kernel lets a script routine run |
| `[lanes]` | `external_p_max` | 0.5 | the decision model's probability that a job reaches outside the organization above which intake runs it full and a routine is floored at G3 |
| `[classifier]` | `url`, `model`, `key_file` | "" (off) | the decision endpoint, the model for choice and yes/no questions, and the 0600 file its key is read from (§10) |
| `[classifier]` | `noul_model` | "" | an optional cheaper yes/no-only model, used when every question is yes/no (the rubric judge) |
| `[classifier]` | `timeout_s` | 15 | per decision call; a timeout is "no opinion" and takes the safe default |
| `[classifier]` | `min_confidence` | 0.8 | below it, a lane or routing choice falls back to the safe default (full, or the executive) |
| `[classifier]` | `pass_p` | 0.85 | the probability every rubric criterion must reach for a result to close without a verifier |
| `[metrics]` | `rubric_agreement_min` | 0.8 | the share of rubric-passed results a sampled verifier also passes; below it the department retro is due |
| `[isolation]` | `agent_user` | "" (off) | the Unix user agents run as (§11); off, they run as the kernel's user and `holon doctor` warns |
| `[isolation]` | `broker`, `runner` | `@holon-broker-<instance>`, `@holon-runner-<instance>` | the abstract Unix socket names of the broker and the runner |
| `[isolation]` | `grants` | [] | trees the agent user may write beyond the departments' `[scope]` roots (workdirs, harness homes) |
| `[isolation]` | `reads` | [] | trees it may only read (beyond the kernel distribution and the drivers' binaries) |
| `[classifier]` | `fallback_url`, `fallback_model`, `fallback_key_file`, `fallback_timeout_s` | "", "", "", 330 | a second decision endpoint for decisions that can wait (the rubric judge, inside a dispatch), tried when the first fails; never on the Chair's terminal or the heartbeat |
| `[smoke]` | `checks` | [] | `[[smoke.checks]]` tables (`name`, `cmd`, `critical`, `timeout`, `reason`) for everyone; `[[smoke.dept.<slug>.checks]]`, or `[smoke.dept.<slug>] file` (a TOML file of `[[checks]]` kept with the department's code), per department (§13) |
| `[smoke]` | `every_min` | 30 | how long smoke results stay fresh; `holon doctor` re-runs them after that |
| `[smoke]` | `timeout_s` | 60 | per check, unless it sets `timeout` |
| `[playbooks]` | `refine_after` | 5 | runs on one playbook version before its refinement is due anyway (§12) |
| `[playbooks]` | `min_judged` | 2 | judged results a criterion needs before a low average makes refinement due |
| `[playbooks]` | `class_after` | 3 | verified results in a department's task class before its first playbook is written |
| `[objectives]` | `review_every_days` | 7 | an objective is reviewed when its pool finishes a cycle, and at least this often while anything moves |
| `[classes]` | `org`, `<dept slug>` | (any) | the task classes work may carry, organization-wide and per department; agents proposing work with another class breach the contract, `holon doctor` warns about open work outside it. Track records (novelty floors, earned authority, sampling) accrue per class, so a small shared taxonomy is what lets trust grow |

## 7. Operations

`holon backup --to DIR` takes a consistent copy of an instance while it runs (SQLite's online
backup), with a checksum manifest; `holon restore PATH --force` puts it back after verifying the
checksums. Neither touches secrets: the Chair key lives outside the instance. Take a backup before
upgrading the kernel, since migrations are forward-only.

## 8. Scope: what a department may reach

An employee works in its work's `workdir` and its run directory. `[scope]` widens that for
everyone; `[scope.dept.<slug>]` widens it for one department (lists add, never replace):

```toml
[scope.dept.it]
roots = ["~/.hermes"]                 # IT inventories and cleans the whole tree
act_commands = ["systemctl"]          # pause and resume sync services while executing
[scope.dept.charter]
commands = ["arch"]                   # planners and verifiers run the validator
```

The kernel uses scope three ways, so it holds on any harness:
- **briefs**: a "Your reach" section lists the roots and commands (`brief.json` carries `scope`);
- **grading**: plan writes inside the reach count as in bounds; a department with roots whose plan
  writes beyond them is floored at G3 (the Chair decides, or widens the scope);
- **drivers**: `{scope}` is the department's slug if it has a section, else `org`. A harness that
  sandboxes builds one sandbox per scope (ZeroClaw: agents `holon-<scope>-observe|act`).

**Pooled units widen scope with reads only** (§14): a pooled unit's roots join `read_roots`, never
the writable `roots`. The brief lists them under "You may read (never write)", `policy.json`
carries them beside `roots` (a runtime that fences writes by `roots` refuses them), grading floors
a plan that writes into one at G3, and isolation grants the agent user read-only. ZeroClaw has no
read-only root, so its installer grants them nothing and says so. When a department lists `[scope]`
commands, the pooled services' entry-point binaries join that fence — only a fence that exists:
an empty `commands` list stays ungoverned, and `holon lint` warns about a department that pools
units with no commands. A pooled root owns no route (`holon route` names the writable reach only)
and hosts no smoke-check cwd (a department whose only reach is pooled runs its smoke checks in the
instance root).

An installer that builds sandboxes records `scope_fingerprint` (`holon scope` prints it) in the
driver. `holon doctor` errors with `scope.drift` when `[scope]` changed after the install: re-run
the installer, or new scopes have no sandbox and narrowed ones keep their old reach.

## 9. Machine checks: acceptance a command can decide

A verifier judges; a command decides. Whatever can be decided by a command (tests pass, the
validator is clean, a file exists) is configured once and run by the kernel, not by an agent:

```toml
[checks.dept.charter]
commands = ["arch validate"]
[checks.class.kernel-change]
commands = ["python3 -m unittest discover -s tests -t .", "python3 -m holon --home \"$(mktemp -d)\" lint --strict"]
```

After an execute stage succeeds (or is partial), the kernel (never the agent's own process:
`execution.service.Kernel.post_execute`) runs `[checks]`, then the
department's, then the task class's commands in the work's `workdir` (the Chair's and run
tokens are removed from their environment). A failing command is recorded as a failed
verification with its output: the work goes to rework (`execute#n`) and no verifier is spent.
When all pass, the results are posted on the work, and the verifier reads them in its context;
sampled G0 work is only closed unverified after its checks pass.

## 10. Lanes, routines, rubrics and the decision model

Every piece of work runs in a lane, and the organization uses the cheapest one that does the job
reliably. Four agent sessions for a two-line change is the waste this removes.

| Lane | Who does it | Sessions | When |
|---|---|---|---|
| `script` | the kernel runs an approved command in the workdir (`holon run-script`, spawned by the heartbeat) | 0 | a routine whose job is deterministic |
| `fast` | one executor session plans and acts; no plan or approve stage | 1 | routine work: the kernel's grade floor is G0 (low risk, a task class with a verified track record), or a routine approved as fast |
| `full` | research? → shape? → plan → approve → execute → verify | 2–6 | everything else, and whatever the others are unsure about |

**Who picks the lane.** One-off work: `holon task add --lane auto` (the default) asks the decision
model; a job it judges routine and internal is asked to run fast. A planner's decomposition may
mark a child `"lane": "fast"`. Either way the kernel decides: fast work whose grade floor is above
G0 runs full, and the event says why (`execution.service.KernelRules.fast_refusal`). The executor of
fast work can still escalate (`noop` with `blockers` sends it to plan).

**Routines** are recurring jobs (the organization's cron jobs): `holon routine add` takes a title,
intent, department, cron and, for a script, its `--command`. The decision model classifies the job
and the kernel enforces the lane cheapest first:
- a job it judges deterministic must be a script: give `--command` (have the department write the
  script as work first), or say `--why` an agent must do it;
- a requested lane cheaper than the model's is raised to it; a costlier one needs `--why`, which
  is recorded in the grade reasons the approver reads;
- with the model off or unsure, the requested lane stands (default `full`).
A routine runs unattended, so it is a proposal graded at least G2 (G3 when the model judges it
reaches outside the organization) for the approver that grade names; on approval its schedule
starts (`holon routine list`). With `--deliver`, each finished firing's result goes to the Chair
through `[notify]` (a digest in the Chair's chat), retried by the heartbeat until it is sent.

**Outside-reach has two witnesses.** The decision model judges it (the `external` question, held
at G3 when `external_p ≥ [lanes] external_p_max`), and the **job's own words** name it: a title or
intent that contains an outside-reach verb — *send, post, publish, apply, outreach, email, dm,
reply, invoice, order, submit, follow-up* — is held at G3 whatever the model says, and at G4 in a
`[pilot]` department. The words are a floor the model cannot lower: a model that is off,
rate-limited or down has no opinion, and "no opinion" must never read as "internal" for a job that
says *send*. It is not a ceiling — the model can still hold a job whose words name no verb.

**An import fails closed.** A routine that arrives through `holon routine import` lands as a
proposal, and it lands **disabled** when it is outside-reach *or when the kernel could not judge
it* (no model opinion and no outside-reach verb in its words). One bulk approval is not per-job
activation; the Chair enables each one with `holon schedule enable` after reading it. A routine the
Chair wrote by hand is not affected: it is the Chair's own act and starts on approval.

A routine may fire on **events** instead of a clock: `--on event:<pattern>` (no cron), e.g.
`event:webhook:invoice.*`. The runtime that sees the event — a gateway, a webhook receiver —
matches the published `api_triggers` patterns against it and calls
`holon routine fire <schedule> --payload …`, at most once per `[events] cooldown_s`; the payload
rides on the firing's work as a message, where the executor's brief shows it. Holon never
matches events itself, and an event trigger never fires by time (`take_due` ignores it).

`holon routine import <cron-jobs/*.md> --dept <slug>` reads legacy cron-job protocol files
(frontmatter `name`, `schedule`, `description`, `no_agent` + `script`, `deliver`, `status`) and shows the
routines they would become; `--apply` proposes each active one (paused ones are skipped). Each firing of a script or fast routine enters at execute with the
standing plan; a full routine plans every firing afresh.

**Rubric and gold standard.** A routine carries a rubric (`--rubric`, repeatable; else its
acceptance). After a script or fast result passes its machine checks, the kernel grades what it did
again (a result beyond its approved grade is a *lane breach*: the Chair is told and a verifier
looks), then asks the decision model, per criterion, the probability that the result meets it, and
whether it is as good as the **gold standard**: the last result a verifier or the Chair passed for
the same routine (else the same task class and department), which the executor also sees in its
context. Every criterion at or above `pass_p` closes the work without a verifier (the result stays
unverified, so it never earns trust or becomes a gold standard); anything lower, and a sample of fast
results (`verify_sample_rate`), keeps the verifier, who reads the judge's scores. `holon metrics`
reports `rubric_agreement` (how often a sampled verifier agrees with a rubric pass) and
`work_by_lane`; agreement below its bound makes the department retro due.

**The decision model** (`[classifier]`, `holon/capabilities/decisions.py`) answers typed questions
with calibrated probabilities in about a second, without spending an agent session. Its uses:

| Use | Question | Safe default when off or unsure |
|---|---|---|
| lane at intake and for routines | choice: script, fast or full | full |
| reach outside the organization | yes/no | the requested lane; no G3 floor |
| rubric judge | yes/no per criterion, and against the gold standard | a verifier |
| failure triage on incidents | choice: harness, model, brief, environment or work | the incident without a cause line |
| routing new work (`task add --dept auto`) | choice over departments by mission | the executive (the CEO shapes it) |

The invariant: the model can make the organization more careful on its own; it lets work skip a
step only where the kernel's deterministic floor allows it too. Its key lives in a 0600 file the
kernel reads and never reaches a run's environment.

## 11. Isolation: the trust boundary

The kernel checks run tokens, separation of duties and grades inside the `holon` process. If that
process runs as the agent's own Unix user, an agent with a shell can read the Chair key or write
`work.db` and walk around all of it. Isolation puts a Unix user between them:

| Part | Runs as | Does |
|---|---|---|
| runner (`python3 -m holon.execution.isolation runner`, a system service root installs once) | the agent user | starts harnesses, machine checks and script routines for the kernel, with only the run's environment; stops them when the kernel says the run is over |
| broker (`holon broker`, a user service) | the kernel's user | answers agents' `holon` calls (and `holon mcp`'s): authenticates each by run token or employee key, never the Chair key; agent commands only; ignores `--home`; reads no file the client did not send |
| run directories | the kernel's user, sticky, the agent granted by ACL | the agent reads its brief and writes `result.json`; it cannot replace what the kernel wrote |
| ACLs (`holon isolation apply`) | the kernel's user | the agent may traverse only the directories leading to its trees (`[scope]` roots, `[isolation] grants`, harness homes), the kernel distribution and harness binaries; every other entry on the way, and every future one, is denied to it |
| knowledge bases (`$HOLON_HOME/kb`) | the kernel's user, the agent granted **read** | the kernel is the KB's only writer (one verb, one commit, one seat), so the tree is a read and never a grant: an agent may read and diff the KB it is bound to, and may not write it |

Both services listen on abstract Unix sockets and check the caller's uid. Set up, once:

```sh
holon isolation plan          # writes the root script to $HOLON_HOME/isolation/setup.sh, durably
sudo sh $HOLON_HOME/isolation/setup.sh     # root: the agent user and its runner service
holon isolation apply --force                          # you: the ACLs (--force: it rewrites every one)
python3 install.py --systemd                           # writes and starts holon-broker
holon doctor                                           # the isolation checks
```

`holon doctor` checks the boundary from the agent's side: it asks the runner, as the agent user, who
it is, whether it can read any secret (the Chair key and the files beside it, the databases,
`holon.toml`, the dashboard's credentials), whether it can write its trees and read its binaries,
and whether the broker answers. Each failure is an error, so the heartbeat opens an incident.

Limits: agents share one Unix user, so one agent could read another's run directory (and its run
token); per-department users would close that. The model key a harness needs is readable by the
agent user by design.

## 12. Playbooks, refinement and objectives: the department's learning loop

Automations that run the same way forever drift away from what they are for. In Holon every job
carries a **playbook** that improves from its own evidence, and every set of jobs serves an
**objective** that steers it.

**Playbooks.** A playbook is a department skill of kind `playbook` (`holon/capabilities/playbooks.py`):
`SKILL.md` holds the objective and protocol (with a `version`), `rubric.md` the criteria, one `- `
line each. A routine gets one when it is approved (`routine-<slug>`, version 1 from its intent and
rubric); a task class gets one when its department has `class_after` verified results
(`class-<task_class>`, written by refinement work from those results). Plan, execute and verify
stages of work that follows a playbook get it in their context, the execute stage records the
version it ran under (`tasks.playbook_version`), and the rubric judge scores each result against the
playbook's rubric and its gold standard, storing one `judgments` row per criterion.

**Refinement** (`execution.service.Kernel._playbook_refinements`, on the heartbeat). For each playbook
in use, the evidence since its last refinement: runs, verifier failures and their notes, results the
judge passed that a verifier failed (the rubric misses what matters), and each criterion's mean
probability. Refinement is due on a verifier failure, a disagreement, a criterion averaging below
`pass_p` over `min_judged` results, or `refine_after` runs. It opens `Refine playbook: <name>` work
carrying that evidence; its plan writes `skill:<name>/SKILL.md` and `rubric.md` (so it is at least G2
and goes to its approver), its executor returns the new files, and when a verifier passes them the
kernel writes them, bumps the version and re-exports skills. The next runs are judged against the new
version, so `holon playbook show` tells whether the refinement helped. Both the protocol and the
rubric evolve: what "good" means sharpens as the terrain is understood.

**Objectives** (`holon objective add --key-result …`). An objective's planner is told to decompose
it into one-off work and routines: a child with a `cron` is a routine (`lane`, `command` for a
script, `rubric`). On approval the kernel creates the routines (`Kernel._routines_from_items`): the
decomposition's decider also approves each one whose grade is theirs; a job the classifier judges
deterministic becomes work to write its script. Everything the objective spawns carries its
`objective_id`, and the objective stays open (`waiting`). When its pool finishes a cycle (nothing of
its own in flight and something finished), or every `review_every_days` while anything moves, the
heartbeat opens a review: a retro on the objective whose context is its dashboard (`holon objective
show`: key results, work, routines with their playbook versions and criterion means, latest
results). Its actions become new work under the objective, or routine proposals; `objective_met`
closes it. The pool therefore improves at two levels: each job's playbook, and the set of jobs.

## 13. Smoke checks: is the infrastructure there

A smoke check is a command that proves something a department depends on works: a script imports, a
database answers, a model endpoint responds. Checks are declared once:

```toml
[[smoke.checks]]                         # everyone depends on it
name = "model gateway"
cmd = "curl -sf http://127.0.0.1:8000/v1/models -o /dev/null"
critical = true
reason = "every agent's model goes through it"

[smoke.dept.tools]                       # or kept with the department's code
file = "~/code/tools/smoke.toml"     # a TOML file of [[checks]]
```

`holon doctor` runs them when the last results are older than `every_min` (so the heartbeat does
too, every `doctor_every_min`), as the agent user under isolation, in the department's first scope
root. A failed critical check is a doctor error (an incident) and **blocks that department's
agents** (`Kernel.smoke_blocked`: their stages wait on the board) until it passes; a failed check for
everyone blocks every department except the incident department, which must be able to fix it. A
non-critical failure is a warning. `holon smoke` runs them now.

`holon lint` checks what the instance will run (`ref.command`, `ref.path`): every executable named
in `[checks]`, smoke checks, script routines and installed drivers must exist on the machine, and
absolute paths they name should; a department with its own reach or scripts but no smoke checks is
reported (`ref.smoke`).


## 14. Units and the bylaws layer

A **unit** is a thing with an identity the organization works through — a service its
departments consume or pool (a "foundation"), or a department's own declared bylaws. Units
are **declared, never registered**: one manifest per unit, `$HOLON_HOME/bylaws/units/<slug>.toml`
(charter, category, tier, `roots`, `pool_of`, `consumed`, `[[services]]` with entry points),
synced by `holon bylaws sync` (the only writer), checked by `holon lint`
(`bind.service`: every consumed `unit:service` is offered; `bind.pool`: every pooled unit
names a real department), and derived into `bylaws/registry.md` by `holon bylaws emit`.

**A surface** (`category = "surface"`) is a harness-level capability — the browser, the
channels an MCP mount, a tunnel — rather than a foundation service. A surface declares
`owner` (the department that keeps it true — platform-ops, C4) and per-service
**the contract** — `effect` (`read` | `write`: which posture may take the route),
`note` (when to prefer this route), `args` (the call shape), `output` (`toon` | `json` |
`text`) and `health` (a command probing the route's liveness). Lint requires `effect` and
`note` on every surface service. `pool_of = ["*"]` pools a surface **org-wide**: every
entitled brief, including department-less work (C-G4). `holon bylaws sync` publishes all of
this as read API v4 (`api_units` gains `owner`; `api_services` gains the contract; the new
`api_pool` view lists the pool edges — `"*"` among them — for a runtime to index, R-04).

**Pooling** (`pool_of = ["<dept>"]`) grants three things and nothing else: the unit's charter
and services render in that department's briefs (presence — at every stage that can use them:
plan, research, shape, execute, verify), its `roots` join the department's reach as **reads only**
(`read_roots` in `holon scope`, `brief.json` and `policy.json`; never the writable `roots`, so a
runtime that fences writes by `roots` refuses them, isolation grants read-only, and grading floors
a plan that writes into one at G3), and requests for it go through `msg request --service
<unit>:<service>` — validated against the registry (K-5), and never the pen: writing to a unit is
work for its own owner. When the department lists `[scope]` commands — or `act_commands`, whichever
fence exists — the pooled services' entry-point binaries join that fence; an empty list stays
ungoverned (never derived from pooling), and lint warns when a pooling department lists no
commands.

Nothing about a unit is implicit: no inherited scope, no hidden authority — the manifest is
the whole contract, the tables and registry are views of it.

**A sync never empties the layer.** The tables are a view of the manifests, and the manifests
are the source — so a manifest that is not in `$HOLON_HOME/bylaws/units/` is a capability the
kernel cannot see. `holon bylaws sync` therefore **refuses** (and changes nothing) when the
manifest directory yields no manifest while the tables hold units, or when a manifest exists at
the layer's former path (`genome/units/`). `holon lint` and `holon doctor` both report such a
manifest as `bylaws.orphan`, so the mistake is visible before any sync runs. Deleting a
capability is the Chair's explicit act, never a side effect of a path change.
