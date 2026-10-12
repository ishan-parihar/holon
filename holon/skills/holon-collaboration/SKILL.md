---
name: holon-collaboration
description: Talk to colleagues, ask other departments for work, and escalate inside a Holon organization. Use whenever you need information or work from someone else instead of guessing or doing another department's job.
kind: operation
roles: [head, researcher, planner, executor, verifier, steward]
version: 1
---

# Working with colleagues

| Need | Command |
|---|---|
| Ask a colleague | `$holon msg send emp:<id> "<question>" --as <your id> [--task <task_id>]` |
| Tell your department | `$holon msg send dept:<slug> "<note>" --as <your id>` |
| Ask another department for work | `$holon msg request <dept> "<title>" --intent "<outcome>" --acceptance "<check>" --as <your id>` |
| Read your messages | `$holon msg inbox --as <your id>` |
| Who does what | `$holon org`, `$holon dept roles <slug>` |

- A request becomes a task in that department's inbox: their head shapes it, their planner
  plans it. You do not plan or do it for them.
- Escalate to your head (then the CEO) when a decision is above your authority; to the Chair
  only through a plan's `needs_input` question.
- Put the task id on every message about a task, so it appears in that task's context.
