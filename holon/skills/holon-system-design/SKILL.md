---
name: holon-system-design
description: Design and staff a new Holon department (a system) for a stated purpose, or reshape an existing one. Use when the Chair or the CEO asks for a new system, when a department outgrows its profile, or when a head proposes a split.
kind: operation
roles: [head, steward]
version: 1
---

# Designing a system

A department is a holon: a mission, a genome profile, role slots, and employees tailored to
fill them. Design it from its requirements, not from a template alone.

## Protocol

1. **Requirements.** Write down: the mission (one sentence), the outcomes it owns, the task
   classes it will handle, the systems and paths it works in, its risks (external effects,
   irreversible actions, sensitive data), and which departments it serves or depends on.
2. **Choose a profile.**
   - `light` (3 slots: lead, maker, checker): new or small systems, low volume, low risk.
   - `standard` (6 slots, one per role): sustained load, high-risk work, or several projects.
   - A custom profile (`$HOLON_HOME/genome/<name>.toml`) only when neither fits; it must still
     cover all six roles. `references/profiles.md` explains the format.
3. **Create it:** `$holon dept create <slug> --name "<name>" --mission @mission.md --profile <profile>`
4. **Tailor each slot's job description** to the requirements: `references/job-descriptions.md`.
5. **Hire** into each slot: `$holon hr hire --dept <slug> --slot <slot> …` (the head first, with `--head`).
6. **Check:** `$holon dept roles <slug>` shows every slot filled and no separation warnings.
7. **Set its autonomy** (Chair only): new departments run under the organization's default
   (supervised). Trust is earned per department in `holon.toml` under `[autonomy.dept.<slug>]`.
8. **Tailor skills** where the department's work differs from the standard playbooks:
   `$holon skill fork <skill> --dept <slug>`.
