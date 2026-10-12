# Genome: roles, stages, profiles, skills, autonomy

The genome is what makes every department the same kind of holon while letting each one be
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

## 2. Profiles

A profile names a department's slots; a slot is the set of roles one employee fills. Profiles
ship in `holon/genome/` and an instance adds or overrides them in `$HOLON_HOME/genome/`.

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
(`holon-employee`), one skill per role with that role's stage briefs (`briefs/<stage>.md`), and
three operations (`holon-system-design`, `holon-collaboration`, `holon-memory`). Skills resolve per
file through three layers, first match wins: `$HOLON_HOME/skills/@<dept>/<skill>/`, then
`$HOLON_HOME/skills/<skill>/`, then the standard library, so tailoring one file for one department
leaves the rest untouched and reaches that department's next brief.

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
| `[planner]` | `enforce_read_only` | true | fail observe-stage runs that change the working directory |
| `[planner]` | `trust_harness_read_only` | false | skip that check for drivers declaring `read_only_enforced` |
| `[grading]` | `novelty_min_verified`, `default_proposed`, `sensitive_paths` | 3, 2, [] | grade floors (see the planner skill's grading reference) |
| `[autonomy]` | `mode` and the keys above; `[autonomy.dept.<slug>]` per department | supervised | §5 |
| `[pipeline]` | `research`, `shape`, `retro` | auto | §3 |
| `[memory]` | `promotion_threshold` | 2 | colleagues who must learn a lesson before it is promoted |
| `[healing]` | `incident_dept` | it | department that receives incident work (falls back to executive) |
| `[healing]` | `lease_expiries_for_incident` | 2 | expired runs of one stage before an incident opens |
| `[healing]` | `schedule_failures_for_incident` | 3 | consecutive schedule failures before an incident opens |
| `[healing]` | `run_failures_for_incident` | 3 | runs of one stage that ended without a valid result before an incident opens |
| `[cadence]` | `dept_retro_days` | 7 | how often a department retro may run over pending proposals |
| `[cadence]` | `dept_retro_min_proposals` | 1 | proposals needed before a department retro is due |
| `[cadence]` | `probation_days` | 14 | days before a new hire's confirmation goes to their head |
| `[budget]` | `max_running_per_dept` | 0 (no cap) | runs a department may have in flight; `[budget.dept.<slug>]` overrides |
| `[notify]` | `command` | "" (off) | run on each heartbeat that finds something new for the Chair; `{message}` is the text (an adapter fills this in, e.g. a chat CLI) |
| `[budget]` | `max_runs_per_dept_per_day` | 0 (no cap) | runs a department may start in 24 hours; its stages wait on the board beyond it |
| `[scope]` | `roots` | [] | paths every employee may work in beyond its work's workdir; `[scope.dept.<slug>]` adds a department's own (§8) |
| `[scope]` | `commands` | [] | extra shell commands every stage may use (a sandboxing harness grants them) |
| `[scope]` | `act_commands` | [] | extra commands for the execute stage only |

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

An installer that builds sandboxes records `scope_fingerprint` (`holon scope` prints it) in the
driver. `holon doctor` errors with `scope.drift` when `[scope]` changed after the install: re-run
the installer, or new scopes have no sandbox and narrowed ones keep their old reach.

