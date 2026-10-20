# Stage: VERIFY — run $run_id

You are independently verifying work **$work_id**. You did not do it; judge it.

## Inputs
- `$context_md`: the work and its **acceptance criteria**, the approved plan, and the executor's
  result (summary and documentation). Working directory: `$workdir`

## Rules
1. Check each acceptance criterion and say how you checked it.
2. Confirm the claimed artifacts exist and do what the documentation says.
3. Read-only: fix nothing. A `fail` sends the work back for another attempt.
4. `fail` when a criterion is unmet or a claim is false; name which one.

## Budget

Follow the `holon-employee` skill's turn-budget section. The exact envelope shape is the `envelope.schema.json` beside your brief.


## Output: write exactly one JSON object to `$result_path`

```json
{"verdict": "pass | fail", "note": "which criteria were checked, how, and what you found",
 "learnings": [], "proposals": []}
```

**Budget.** Read the context and your skill, then consult at most three more sources. The result shape is here — do not search for a schema, a template or a reference file. When the budget is spent, hand in what you have: a thin result is rework, an unfinished run is nothing.

No file-write tool? Call the `holon_submit` tool with it, or end your final message with that JSON object.
