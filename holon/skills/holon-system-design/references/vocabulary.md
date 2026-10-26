# The design vocabulary, and whose reference carries it

Designing one department touches mechanisms that live in other skills' references. This index
maps each topic to the file that owns it, so a designer reads the authority rather than a
summary. Measured 2026-10-26: the library carries 13 reference files across 7 skills; the eight
topics the 2026-10-16 capability-layer audit named as missing here were covered elsewhere and
unlinked, so this index links them and the guard `test_bylaws_names_every_operation_skill_the_library_carries`
keeps the library's list honest.

| Topic | Where it lives | Read it when |
|---|---|---|
| Departments, roles, the six role vocabulary | `holon-employee/references/organization.md` | before slotting a department or hiring into it |
| Profiles and slots | this skill's `references/profiles.md` | choosing `light` / `standard` / custom |
| Job descriptions per slot | this skill's `references/job-descriptions.md` | writing a slot's JD |
| Shaping work (a plan, decomposition) | `holon-head/references/shaping.md` | the department's incoming work needs shaping |
| Staffing (who claims what, class gates) | `holon-head/references/staffing.md` | staffing or claiming work in a class |
| Review and grading | `holon-planner/references/grading.md` | a plan or result is graded |
| Terrain (what an agent reads before acting) | `holon-researcher/references/terrain.md` | the department works in an unknown system |
| Department review (the retro cadence) | `holon-head/references/department-review.md` | a department's quarterly/periodic review |
| Playbooks (a job's protocol and rubric) | `holon-steward/references/evolution.md`, `holon-steward/SKILL.md` | the department's jobs refine their playbooks |
| Curation (what becomes law) | `holon-steward/references/curation.md` | a learning should become binding |
| Healing (an unhealthy department) | `holon-steward/references/healing.md` | a department's rates breach a bound |
| Retro | `holon-steward/references/retro.md` | the department's retrospective |
| The organization of systems (add, remove, reshape) | `holon-operator/references/moves.md` | the question is which systems should exist |
| Reach (what an agent may touch) | `holon-employee/SKILL.md`, `holon-operator/SKILL.md`; `docs/BYLAWS.md` §4 | fencing a department, or debugging a refusal |
| Routines (recurring work) | `holon-operator/SKILL.md`; `docs/BYLAWS.md` §9 | a department owns a recurring job |
| Units and pooling (a shared capability) | `holon-operator/SKILL.md`; `docs/BYLAWS.md` §14 | several departments need one service |
| Autonomy (a department's earned latitude) | `docs/BYLAWS.md` §5 | raising or lowering a department's autonomy |

The commands that bind a design live in one place each: `$holon dept create`, `$holon dept profiles`,
`$holon dept roles`, `$holon hr hire`, `$holon skill fork`. Every one takes the Chair or a head as
its actor class; a designer proposes and the Chair lands it.
