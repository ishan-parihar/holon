---
name: holon-memory
description: Recall from and contribute to a Holon organization's institutional memory and knowledge base. Use when you start exploring anything (someone may already know it), and when you learn something the next person should not have to rediscover.
kind: operation
roles: [head, researcher, planner, executor, verifier, steward]
version: 2
---

# Institutional memory

Memory is how the organization outlives every session. It is scoped from you outward: your own
(`emp:<id>`), your department's, the organization's, plus the project and the task class.

## Protocol

1. **Recall before you explore:** `$holon memory search "<terms>"`, narrowed with
   `--scope dept:<slug>`, `--scope emp:<your id>`, a project ref, or `class:<task class>`.
   `$holon memory context <stage id>` previews exactly what a stage's brief will contain.
2. **Contribute through your result.** `learnings` become your memory (or the project's, for
   facts); `decisions` bind future work; research `terrain` becomes the knowledge base. They are
   deduplicated, and lessons several colleagues learn are promoted to the department.
3. **Add directly** only knowledge that did not come from a run:
   `$holon memory add --scope dept:<slug> --kind fact --content "…" --provenance "<source>"`.
   Employees write their own and their department's scopes; the organization scope grows by promotion.

| Kind | For |
|---|---|
| fact | something true about the world (terrain) |
| decision | a choice made with authority that future work must respect |
| constraint | a rule that must not be broken |
| preference | how the Chair or a head wants things done |
| lesson | what went wrong or right, and what to do next time |
| open_question | an unknown that blocks or risks work |
