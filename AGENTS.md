# AGENTS.md — holon (the distribution)

This is the kernel source, not an instance: no state lives here, and instances live at
`$HOLON_HOME`. If you were handed a Holon brief, read its `BRIEF.md` instead. This file is for
agents changing the kernel itself: where things are, and the laws every change must keep.

## Map

| Path | Owns |
|---|---|
| `holon/core/vocab.py` | every enumeration (roles, stages, statuses, kinds); nothing else may define one |
| `holon/core/contract.py` | the result contract per stage; validators and `schemas/` are generated from it |
| `holon/core/{db,ids,paths}.py` | SQLite, migrations with checksums, the hash-chained audit trail, ids, instance paths |
| `holon/execution/work.py` | the work graph; **the only writer of tasks, task_needs, outputs, runs** |
| `holon/execution/org.py` | departments, employees, authority, evidence-based reviews |
| `holon/execution/genome.py`, `holon/genome/*.toml` | role → stage mapping, profiles, slots |
| `holon/execution/autonomy.py` | the autonomy policy |
| `holon/execution/service.py` | the kernel: genome rules, claims, ingestion, sampling, triggers, metrics |
| `holon/execution/security.py` | the Chair key, employee keys, run tokens |
| `holon/execution/{briefs,drivers,grading,schedules,staleness,cron}.py` | the harness contract and scheduling |
| `holon/capabilities/{memory,context,skills}.py` | memory (**only writer of memory.db**), context bundles, the skills library |
| `holon/skills/` | the standard skills library; role skills hold the stage briefs |
| `holon/integrity/{lint,doctor,conformance}.py` | the rubric, live-state checks, adapter conformance |
| `holon/cli.py` | the command registry and its actor classes |
| `adapters/<harness>/` | everything harness-specific: driver, installer, identity and skills sync |
| `docs/` | INTENT, GENOME, CONTRACT; `docs/design/` holds dated design records |

## Laws (enforced by `holon lint`, `holon doctor` and the tests)

1. **Stdlib only** under `holon/`.
2. **No harness named under `holon/`.** Harness specifics live in `adapters/`.
3. **One writer per database.** Other capabilities' databases are opened read-only.
4. **Status changes go through `Work._set`**, which logs a hash-chained event.
5. **Enumerations come from `vocab.py`; result shapes from `contract.py`.** Copies fail lint.
6. **Schema changes are new migration files.** An edited applied migration fails lint.
7. **Every org term binds to a mechanism** in the INTENT and GENOME tables.
8. **Every command declares its actor class** in the registry.
9. **Tests first** for any change to the graph, the contract, security or drivers.

## Verify a change

```sh
python3 -m unittest discover -s tests -t .
python3 -m holon --home "$(mktemp -d)" lint      # zero errors before you commit
```
