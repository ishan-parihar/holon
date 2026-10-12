# Curating memory and the knowledge base

- Search the department scope for duplicates and contradictions:
  `$holon memory search "<topic>" --scope dept:<slug>`. Retire the stale one
  (`$holon memory retire <id>`), keep the better-sourced one.
- Expire terrain whose source changed (provenance starts with `terrain:`).
- Promote lessons that more than one employee learned: `$holon memory promote <id> --to dept:<slug>`;
  ask the Charter steward to promote ones other departments learned too (`--to org`).
- Pin the few constraints every run must see: `$holon memory pin <id>`.
