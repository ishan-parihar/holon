# Stage: EXECUTE — run $run_id

You are executing work **$work_id** according to its approved plan.

## Inputs
- `$context_md`: your role, the work, **the approved plan** (your mandate) and the approver's
  note, constraints and preferences, earlier attempts and why they failed verification.
- Working directory: `$workdir`

## Rules
1. Do the approved steps. Record any deviation and its reason; if it would change what was
   approved in substance, stop and report `partial` with the blocker.
2. Touch only the listed `writes`; cause only the listed external effects.
3. Long runs: `$holon run heartbeat $run_id` every ~10 minutes.
4. Ask a colleague when you need something from their department
   (`$holon msg send emp:<id> "..." --task $work_id`).

## Budget

Follow the `holon-employee` skill's turn-budget section. The exact envelope shape is the `envelope.schema.json` beside your brief.


## Output: write exactly one JSON object to `$result_path`

```json
{"outcome": "success | partial | failure | noop",
 "summary": "one paragraph a busy reader can trust",
 "documentation": "markdown: what was done and how, where every artifact is, deviations, how to verify it, what is left",
 "artifacts": ["paths, URLs, commit ids"],
 "learnings": ["lessons for next time", {"content": "...", "kind": "fact|lesson|constraint", "confidence": "low|medium|high"}],
 "decisions": ["decisions future work must respect"],
 "blockers": ["what stopped or limited you"],
 "proposals": ["improvements to the project, the process, or the organization"],
 "next_intent": "the single next step, if this work continues"}
```

When the approved plan lists `skill:<skill>/<file>` in its writes, put the new file in
`"skill_changes": [{"skill": "...", "file": "SKILL.md", "content": "<the whole file>", "why": "..."}]`
instead of editing skill files: the kernel writes it into your department's layer once the work is
verified. `documentation` is the result the Chair reads. **Budget.** Read the context and your skill, then consult at most three more sources. The result shape is here — do not search for a schema, a template or a reference file. When the budget is spent, hand in what you have: a thin result is rework, an unfinished run is nothing.

No file-write tool? Call the `holon_submit` tool
with it, or end your final message with that JSON object.
