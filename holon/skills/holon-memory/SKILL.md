---
name: holon-memory
description: Recall and contribute to a Holon organization's institutional memory and knowledge base. Use before exploring anything (someone may already know it) and whenever you learn something the next person should not have to rediscover.
kind: operation
roles: [head, researcher, planner, executor, verifier, steward]
version: 1
---

# Institutional memory

## Recall first

`$holon memory search "<terms>"` searches every scope; add `--scope dept:<slug>`,
`--scope emp:<your id>`, a project ref, or `class:<task class>` to narrow it.
`$holon memory context <task_id> --as <your id>` previews exactly what your brief will contain.

## Contribute

Most of your contribution is automatic: the `learnings` and `decisions` in your result
envelope are consolidated into memory (lessons to you, decisions to the project or
department), deduplicated, and promoted when colleagues learn the same.

Add directly only for knowledge that does not come from a run:

```
$holon memory add --scope <scope> --kind fact|decision|constraint|preference|lesson|open_question \
  --content "…" --provenance "<source>" --confidence low|medium|high
```

| Kind | Use for |
|---|---|
| fact | something true about the world (terrain) |
| decision | a choice made by someone with authority, which future work must respect |
| constraint | a rule that must not be broken |
| preference | how the Chair or a head wants things done |
| lesson | what went wrong or right and what to do next time |
| open_question | an unknown that blocks or risks work |
