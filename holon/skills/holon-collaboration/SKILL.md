---
name: holon-collaboration
description: Talk to colleagues, ask other departments for work, and escalate inside a Holon organization. Use when you need information or work from someone else instead of guessing, or doing another department's job.
kind: operation
roles: [head, researcher, planner, executor, verifier, steward]
version: 2
---

# Working with colleagues

An organization is smarter than any one employee only if its employees ask each other. Asking
costs a message; guessing costs a failed verification.

## Protocol

| Need | Command |
|---|---|
| Ask a colleague | `$holon msg send emp:<id> "<question>" --task <work id>` |
| Tell your department | `$holon msg send dept:<slug> "<note>"` |
| Ask another department for work | `$holon msg request <dept> "<title>" --intent "<outcome>" --acceptance "<check>"` |
| Read your messages | `$holon msg inbox` |
| Find who does what | `$holon org`, `$holon dept roles <slug>` |

Your run token or employee key identifies you; the Chair adds `--as <emp id>`.

## Principles

- A request becomes work in the other department: its head shapes it and its planner plans it.
  You do not plan or do it for them; your own work can wait for it.
- Put the work id on every message about work, so it appears in that work's context.
- Escalate to your head, then the CEO, when a decision is above your authority; reach the Chair
  through a plan's `needs_input` question.
