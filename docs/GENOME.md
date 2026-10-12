# Genome: roles, profiles, skills, autonomy

Every department (system) is the same holon, configured for its purpose. Four things define it.

## 1. Roles: the six standard functions

| Role | Function | Duty today | Skill |
|---|---|---|---|
| head | owns outcomes: shapes work, approves within authority, assigns, staffs | review | `holon-head` |
| researcher | maps the terrain into the department knowledge base | (research stage: G1) | `holon-researcher` |
| planner | turns shaped work + terrain into a graded plan or a decomposition | plan | `holon-planner` |
| executor | does the approved plan and documents it | execute | `holon-executor` |
| verifier | independently checks results against acceptance criteria | verify | `holon-verifier` |
| steward | retros, memory curation, playbook and genome evolution, healing | (retro stage: G1) | `holon-steward` |

Researcher and steward work today through the CLI their skills describe; they get their own
pipeline stages in the work-graph phase (G1, see docs/design/).

## 2. Profiles: which slots a department has

A slot is a set of roles one employee fills. Profiles ship in `holon/genome/`; an instance adds
or overrides them in `$HOLON_HOME/genome/<name>.toml`.

| Profile | Slots | For |
|---|---|---|
| `light` | lead (head+steward) · maker (planner+executor) · checker (researcher+verifier) | small or new systems: 3 employees |
| `standard` | one slot per role | sustained load, high-risk work |
| `executive` | ceo (head+planner) · strategy · audit · charter | the executive office (delegates execution) |

Holon checks every profile covers all six roles and that no slot both executes and verifies.

```
holon dept create <slug> --name "…" --mission @mission.md --profile light|standard|<file.toml>
holon hr hire --dept <slug> --slot <slot> --name … --title … --jd @jd.md --driver … --reason "…"
holon dept roles <slug>            # slots, holders, separation warnings
holon dept profile <slug> standard --reason "…"   # the Chair grows a department
```

Each hire is tailored to its system through the job description (skill `holon-system-design`
gives the format and the design protocol).

## 3. Skills: how employees do their jobs

The standard library (`holon/skills/`): `holon-employee` (the protocol every spawn loads first),
one skill per role, and `holon-system-design`, `holon-collaboration`, `holon-memory`. Role skills
hold the duty briefs (`briefs/<duty>.md`) the kernel renders into each run's BRIEF.md.

Skills resolve per file through three layers, first match wins:
`$HOLON_HOME/skills/@<dept>/<skill>/` → `$HOLON_HOME/skills/<skill>/` → `holon/skills/<skill>/`.
A steward tailors one file for one department without forking the rest, and the change reaches
that department's next brief.

```
holon skill list [--as <emp>] [--role R] [--dept D]   # catalog (an employee's own skills with --as)
holon skill show|path <skill> [file] [--dept D]        # read / locate the resolved file
holon skill invoke <skill> [file] --as <emp>           # render for an employee; the use is recorded
holon skill fork <skill> [--file F] [--dept D]         # copy into the instance/department layer to tailor
holon skill new <name> --kind role|operation|protocol --description "…" [--dept D]
holon skill check [--dept D]                           # frontmatter, links, roles
holon skill export --to <dir> [--name S] [--dept D]    # for harnesses that load skills from disk
```

`SKILL.md` frontmatter: `name`, `description`, `kind` (protocol | role | operation), `roles`,
`version`. Links to other files are written as `` `references/x.md` `` or `` `briefs/x.md` `` and
checked.

## 4. Autonomy: how much a department decides without the Chair

Safe by default; fully autonomous only where trust has been established and the Chair says so.

```toml
[autonomy]                  # holon.toml
mode = "supervised"         # supervised | delegated | autonomous
verify_sample_rate = 0.2    # share of trusted G0 results still verified

[autonomy.dept.it]          # one department, once it has earned it
mode = "autonomous"
```

| Key | supervised (default) | delegated | autonomous |
|---|---|---|---|
| `widen_approver`: who approves widening anyone's authority | chair | ceo | auto |
| `auto_promote_cap`: highest grade authority can be earned to | G2 | G3 | G4 |
| `ceo_initial_max`: the CEO's authority at hire | G1 | G2 | G2 |
| `promote_after`: clean verified successes per promotion | 10 | 10 | 10 |
| `audit_sample_permille`: employee approvals re-judged by the Chair | 100 | 100 | 50 |
| `verify_sample_rate`: trusted G0 results still verified | 0.2 | 0.2 | 0.2 |
| `trusted_after`: consecutive verified passes before an executor is trusted | 10 | 10 | 10 |

Invariants no mode relaxes:
- narrowing authority applies at once; widening goes to `widen_approver` as a proposal in that
  approver's queue (applied by the kernel on approval), and nobody approves their own widening;
- work closed without verification stays unverified and never counts toward trust or authority;
- the objective grade floors (risk, external effects, sensitive paths, novelty) always apply;
- the Chair can always approve, reject, regrade, cancel, and change this policy.

`holon charter autonomy` shows the effective policy per department.
