# AGENTS.md — holon (the distribution)

You are in the **kernel source**, not an instance. No state lives here; instances live at
`$HOLON_HOME`. If you were handed a brief, read its `BRIEF.md` instead. This
file is for agents changing the kernel itself.

## Map (strata = directories)

| Path | Stratum | Owns |
|---|---|---|
| `holon/core/` | — | instance paths (`HOLON_HOME`), ids, sqlite + migrations |
| `holon/execution/work.py` | operations/work | the state machine; **only writer of task/plan/run/result rows** |
| `holon/execution/org.py` | operations/hr | departments, employees, authority, evidence-based reviews |
| `holon/execution/grading.py` | operations/work | G0–G4 grade floors |
| `holon/execution/schedules.py` | operations/work | schedules and recurring plans |
| `holon/execution/service.py` | execution/runtime | claim → brief → driver → ingest; `tick` |
| `holon/execution/{briefs,drivers,envelopes,findings,cron}.py` | execution/runtime | the harness contract |
| `holon/execution/templates/` | execution/runtime | role instructions (planner/executor/verifier) |
| `holon/capabilities/memory.py` | capabilities/memory | **only writer of memory.db** |
| `holon/capabilities/context.py` | capabilities/memory | context bundles (reads trajectory/logbook read-only) |
| `holon/migrations/<db>/` | — | forward-only SQL; `.fts.` files are optional (FTS5) |
| `holon/governance/` | governance | reserved: law (genome.db), observatory (P5) |
| `schemas/`, `docs/CONTRACT.md` | — | the published harness contract |

## Laws (enforced by `tests/test_core.py::Agnosticism` and the state machine)

1. **Stdlib only** under `holon/`. No third-party imports, ever.
2. **No harness named in kernel code.** Harness specifics go in driver TOML files only.
3. **One writer per database.** work.db ← `holon.execution.{work,org,schedules}`; memory.db ← `holon.capabilities.memory`.
   Other capabilities' databases are opened read-only.
4. **Every task transition goes through `TRANSITIONS`** and writes an event. No direct status updates
   elsewhere (the only exception, `instantiate_recurring`, writes its own event).
7. **Every org term binds to a mechanism** (docs/INTENT.md). No hollow roles.
5. **Schema changes are new migration files.** Never edit an applied migration.
6. **Tests first** for any change to the state machine, envelopes, or drivers.

## Verify a change

```sh
python3 -m unittest discover -s tests -t .
ruff check holon tests --select F,E9,B --line-length 120
```
