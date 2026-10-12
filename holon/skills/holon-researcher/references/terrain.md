# Terrain items

A terrain item is one reusable fact with its source. The research stage's `terrain` list becomes
department (or project) memory with provenance `terrain:<work id> <source>`, so the steward can
expire it when the system changes and planners can trust where it came from.

Good: "The deploy script reads `DEPLOY_ENV` from `/etc/app/env` (scripts/deploy.sh:14)."
Bad: "Deployment is somewhat complicated." (no fact, no source)

Use kind `constraint` only for a rule someone with authority stated, and `open_question` (in the
research result's `open_questions`) for unknowns. Outside a research stage, add terrain with
`$holon memory add --scope dept:<slug> --kind fact --provenance "terrain: <source>" --content "…"`.
