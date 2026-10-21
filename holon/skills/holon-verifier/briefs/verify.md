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

A `proposals` item is one of: a plain string (one sentence), or an object with **both** a
`"title"` (a few words) and an `"intent"` (what the work would do). An object with only a
`"content"` key is **not** accepted — if you write one improvement, either
`"proposals": ["derive the summary and documentation from one source"]` or
`"proposals": [{"title": "one summary source", "intent": "derive the executor's summary and documentation from one source so they cannot drift"}]`.

Hand it in per the employee skill: `holon_submit`, or the JSON as your final message.
