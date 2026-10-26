---
name: holon-system-design
description: Design and staff a Holon department (a system) for a stated purpose, or reshape one that has outgrown its profile. Use when the Chair or the CEO asks for a new system, or when a head or steward proposes a split or a profile change.
kind: operation
roles: [head, steward]
version: 2
---

# Designing a system

A department is a holon: a mission, a bylaws profile whose slots cover the six roles, and
employees tailored to fill them. Design it from its requirements, not from a template alone;
the profile is the skeleton, the job descriptions and tailored skills are the muscle.

This designs one system. The `holon-operator` skill manages the organization of them: when the
question is which departments should exist, or how one that outgrew its profile is reshaped,
that skill is the seam. `references/vocabulary.md` indexes every mechanism a design touches and
names the skill or doc that owns it — read the authority, not a summary of it.

## Protocol

1. **Requirements.** The mission in one sentence; the outcomes it owns; its task classes; the
   systems and paths it works in; its risks (external effects, irreversible actions, sensitive
   data); the departments it serves or depends on.
2. **Profile.** `light` (3 slots: lead, maker, checker) for new or small systems; `standard`
   (6 slots) for sustained load or high-risk work; a custom profile only when neither fits
   (`references/profiles.md`). List them with `$holon dept profiles`.
3. **Create** (Chair): `$holon dept create <slug> --name "<name>" --mission @mission.md --profile <profile>`.
4. **Tailor each slot's job description** to the requirements: `references/job-descriptions.md`.
5. **Hire** (Chair), the head first: `$holon hr hire --dept <slug> --slot <slot> --name … --title … --jd @jd.md --driver <driver> --reason "…"`.
6. **Check**: `$holon dept roles <slug>` shows every slot filled and no separation warnings.
7. **Tailor skills** where the department's work differs from the standard playbooks
   (Chair: `$holon skill fork <skill> --dept <slug>`).
8. **Autonomy** stays at the organization's default until the department earns more; the
   Chair raises it in `holon.toml` under `[autonomy.dept.<slug>]`.

## Never

- Leave a role uncovered, or put executing and verifying in one slot: the bylaws rejects both.
