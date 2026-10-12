# Terrain items

One item per reusable fact, in the narrowest scope that fits (the project, else the
department). Provenance makes it checkable; the `terrain:` prefix makes it findable and lets
the steward expire it when the system changes.

```
$holon memory add --scope dept:<slug> --kind fact \
  --provenance "terrain:<task_id> <source: path:line | command | URL>" \
  --confidence high|medium|low \
  --content "<fact a planner needs, in one or two sentences>"
```

Use `--kind open_question` for unknowns that block planning, and `--kind constraint` only for
rules someone with authority stated.
