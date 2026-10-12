# The harness contract

Everything a harness author needs. The kernel guarantees this contract; any agent that
honors it can work in the organization, and nothing else is required.

## 1. The brief

For each run the kernel writes `$HOLON_HOME/execution/runtime/runs/<run_id>/`:

| File | Who writes | Content |
|---|---|---|
| `BRIEF.md` | kernel | role instructions: planner, executor, or verifier |
| `brief.json` | kernel | `run_id, task_id, kind, role, brief_md, context_md, result_path, workdir, holon, lease_until` |
| `context.md` | kernel | the compiled context bundle (task, plan, constraints, memory, goal lineage, logbook) |
| `result.json` | **agent** | the result envelope (§2) |
| `driver.log` | kernel | command, exit code, stdout/stderr (push mode) |

An agent needs only to read `BRIEF.md` (which points to everything else) and write
`result.json`. During a run it may call the kernel via the `holon` command given in
`brief.json` (also exported as `$HOLON`), e.g. `$HOLON memory search "..."` or `$HOLON run heartbeat <run_id>`.

Environment in push mode: `HOLON_HOME, OG, HOLON_RUN_ID, HOLON_TASK_ID, HOLON_ROLE, HOLON_BRIEF_DIR, HOLON_RESULT_PATH`.

## 2. Result envelopes

JSON Schemas: [`schemas/`](../schemas). Summary:

**planner** (`plan-result.schema.json`): one of
- `{"kind": "plan", "findings", "steps", "writes"?, "risks"?, "rollback"?, "estimate"?, "acceptance"?, "context_refs"?}`
- `{"kind": "decompose", "rationale", "children": [{"title", "intent", "acceptance"?, ...}, ≥2]}`
- `{"kind": "needs_input", "question"}`

**executor** (`execute-result.schema.json`):
`{"outcome": success|partial|failure|noop, "summary", "documentation", "artifacts"?, "learnings"?, "decisions"?, "blockers"?, "proposals"?, "next_intent"?}`

**verifier** (`verify-result.schema.json`): `{"verdict": pass|fail, "note"}`

### What happens to non-compliant output
- **planner / verifier:** the run fails and the task returns to the queue (no partial state).
- **executor:** the output is wrapped as a `partial` result with the stdout tail as its
  documentation and sent to verification, so an independent verifier decides, not the agent.
- **planner that modifies its working directory** (push mode, when `[planner] enforce_read_only`):
  the plan is discarded and the task returns to the inbox.
- **late result after the lease expired:** discarded; the work was already reassigned.

## 3. Drivers (push mode)

One TOML file per harness in `$HOLON_HOME/config/drivers/`:

```toml
name      = "my-harness"
command   = "my-agent --prompt-file {brief_md} --cwd {workdir}"
cwd       = "{workdir}"
timeout_s = 1800
result    = "file"          # or "stdout-json": take the last JSON object printed on stdout
read_only = "--plan-only"   # inserted at {read_only} for planner/verifier runs
roles     = ["planner", "executor", "verifier"]
[env]
SOME_VAR = "value"
```

Placeholders (shell-quoted): `{brief_dir} {brief_md} {context_md} {result_path} {workdir}
{run_id} {task_id} {role} {read_only} {prompt}` (the BRIEF.md text) and `{holon}`.
Double literal braces: `{{ }}`. Shipped examples: `hermes`, `claude-code`, `generic-shell`
(`holon drivers` lists them; instance files override by name).

## 4. Leases

Every run holds a lease (plan 30 min, execute 60 min, verify 20 min). Renew with
`holon run heartbeat <run_id>`. `holon tick` reclaims expired leases: planning returns to the
inbox; execution retries (up to `max_attempts`) or fails.

## 5. Staleness

The planner lists in `writes` the paths it will modify. The kernel fingerprints them at
plan submission and again when an executor claims the approved plan; if they changed, the
plan is superseded and the task is replanned. A retry after a partial execution therefore
usually replans; that is deliberate (the findings changed).
