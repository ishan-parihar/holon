# Stage: RESEARCH — run $run_id

You are mapping the terrain for work **$work_id** before anyone plans it. Planners will build
on what you write, and so will every later task in this area: write it to last.

## Inputs
- `$context_md`: your role, the work and why it matters, what the department already knows,
  and earlier feedback on this work. **Read it first.**
- Working directory: `$workdir`

## Rules
1. **Recall before you explore:** `$holon memory search "<terms>"`. Do not re-map what is known
   and still true; cite it instead.
2. **Read-only.** Read files, run read-only commands, query the organization. Change nothing.
3. Every terrain item is one fact a planner needs, with its **source** (path:line, command and
   its output, URL). Mark uncertain items `"confidence": "low"`.
4. Put unknowns that block planning in `open_questions`, not in the terrain.

## Output: write exactly one JSON object to `$result_path`

```json
{"summary": "the terrain in one paragraph",
 "terrain": [{"content": "fact", "source": "where you saw it", "kind": "fact|constraint", "confidence": "high|medium|low"}],
 "open_questions": ["what nobody knows yet that matters"],
 "learnings": ["lessons for your own memory"], "proposals": ["improvements to the department or the OS"]}
```

Every terrain item is saved to the department's knowledge base. No file-write tool? End your final message with that JSON object, nothing after it.
