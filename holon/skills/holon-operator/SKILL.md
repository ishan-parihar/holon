---
name: holon-operator
description: Manage the organization itself: add a system, retire one, refactor one in place, expand or condense it, heal what is stuck, and evolve what has outgrown itself. Use when the Chair asks for a new system, when a head or steward proposes a change to the organization's shape, or when you are changing the system the work runs in rather than the work.
kind: operation
roles: [head, steward]
version: 1
---

# Operating the organization

Every other skill runs **one department, from inside it**. This one runs **the organization**: what
it is made of, what connects the parts, and what happens when one outgrows its shape. A new agent
arrives able to do work and unable to change the system that does the work, so the operator's
reflexes have to be written down to survive them.

**`holon-system-design` designs and staffs one system; this one manages the organization of them.**
When you already know which department you are changing, use that skill. When the question is which
departments should exist, or how one that outgrew its profile changes shape, read on.

A department is a holon: a mission, a bylaws profile, staff, reach and checks. **Changing the
organization's shape means changing departments, and every one of those changes is the Chair's or
a pipeline's** — you propose with evidence, you do not reach around it.

## Protocol

1. **Measure before you touch anything.** `$holon status`, `$holon org`, `$holon doctor`, and
   `$holon metrics --dept <slug>`. A change proposed without its numbers is a preference.
2. **Name the seam.** Say which document owns the change: `docs/BYLAWS.md` for configuration,
   `docs/COMMANDS.md` for the command, `docs/ARCHITECTURE.md` for what the thing is. If you cannot
   name it, you do not understand it yet; go and read it.
3. **Propose with the blast radius.** Which departments are affected, what stops working if it is
   wrong, and **how it is reversed**. Reversal is the part people omit and the part that decides
   whether the change is safe.
4. **Land it as work.** `$holon task add --dept <slug> --intent "…" --acceptance "…"`, or a
   proposal to the Chair: `$holon msg send chair "Proposal: … — <evidence>" --as <your id>`.
   The organization changing shape is work like any other: planned, graded, approved, executed.
5. **Verify it landed, then watch it.** `$holon doctor` and the metric you said the change would
   move. If it did not move, propose reverting: an unmeasured change is indefensible later.

## The moves, and their exact seam

| Move | Perform it with | Approved by |
|---|---|---|
| **add** a system | `$holon dept create <slug> --name … --mission @m.md --profile <p>` (the `holon-system-design` skill is the full design protocol) | Chair |
| add a candidate from outside | `$holon venture import <path> --dept <slug> --apply` — every routine lands unapproved, since an import is not an approval | Chair |
| **remove** one | `$holon dept export <slug> --to <dir>` first, then `$holon task cancel` its live work | Chair |
| **refactor** one | `$holon dept profile <slug> <profile>`, or `$holon hr set-driver <emp> --driver <d>` | Chair |
| **expand** one | `$holon hr hire …`; a `[scope.dept.<slug>]` root, a `[smoke.dept.<slug>]` check, a task class in `holon.toml` | Chair |
| **condense** one | `$holon dept profile <slug> <older profile>`; `$holon hr reassign <emp> …`; `$holon gc --apply --force` (the Chair's call — it refuses without a recent backup) | Chair |
| **heal** it | the fix as a registered guard (`$holon guards`); `$holon incident waive <work id> --note "…"` to close without one | Chair |
| **evolve** it | the retro's action, as planned work; the `holon-steward` skill's evolution reference is the protocol | the approver its grade names |

`references/moves.md` says what each move owes — read it before you perform one.

## Principles

- Every change is measured, reversible and attributed. All three, or it is a guess with a stamp.
- The organization is the only thing that can change itself, and only through its own pipeline,
  under the same approvals as any other work (INTENT law 6).
- Nothing personal lands in this repository: hosts, paths, chat ids and endpoints belong to the
  instance's `holon.toml`, never to code, skills or docs (`AGENTS.md`).

## Never

- Change the organization's shape by a direct database write. One writer exists, and it is not you.
- Approve a change you proposed, or one whose blast radius you cannot state.
- Retire a system without exporting it, or an employee without naming a successor.
