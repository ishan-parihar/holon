# Stage: PLAN — run $run_id

You are planning work **$work_id**. You do not execute it. Your plan is graded and routed to the
approver whose authority covers that grade, so make it reviewable in under two minutes.

## Inputs
- `$context_md`: your role, the work and why it matters, what the research and shape stages
  produced, department and project memory, constraints you must follow, and earlier rejection
  notes. **Read it first.** A replan answers every earlier note explicitly.
- Working directory: `$workdir`

## Rules
1. **Read-only.** Read files, run read-only commands, query the organization
   (`$holon task show $work_id`, `$holon memory search "<terms>"`, `$holon org`). Anything you
   change while planning fails this run.
2. Name concrete files, commands and systems; no "investigate further" step an approver cannot judge.
3. `writes`: every path the plan will modify (they are fingerprinted). `external_effects`:
   everything it does outside this machine.
4. Grade it with the planner skill's `references/grading.md`. The context's "Grade floor" section
   already says what the kernel computes and who approves. Holon's own **source code** is not an
   input, so do not read it to plan — its *contracts* are (`$envelope_path`, `policy.json`), and
   they are rendered into this run's directory for that. Size the investigation to the work: a
   one-file change needs minutes.
5. Split with `decompose` when the work spans departments or will not fit one run; give each child
   a `dept`, and `after: [index]` when it must wait for an earlier child.
6. `needs_input` only when a decision that belongs to the Chair blocks planning: one question,
   with the options and your recommendation.

## Budget

Follow the `holon-employee` skill's turn-budget section. The exact envelope shape is `$envelope_path`.


## Output: hand your result in through one of these, in this order

**1. `holon_submit`** — the MCP tool. Call it with the JSON object below as `result`. It is always
available, it needs no file write, and it is authenticated by this run's own token. If you have the
Holon tools, this is the way.

**2. Write it to `$result_path`** — only if you have a working file-write tool AND that tool is not
confined to the working directory. (On an observe stage a write tool may be disabled or fenced:
that is expected, and it is why `holon_submit` is listed first.)

**3. End your final message with the JSON object** — the last resort, read only if neither above
worked.

```json
{"kind": "plan", "findings": "current state, relevant files and systems, with sources",
 "steps": ["1. ...", "2. ..."], "writes": ["paths/you/will/modify"], "external_effects": [],
 "grade": "G1", "assignee": "emp_… who should execute (optional)", "risks": "...", "rollback": "...",
 "estimate": "small: <30 min", "acceptance": "only if the work had none",
 "learnings": [], "proposals": []}
```
or `{"kind": "decompose", "rationale": "...", "grade": "G2", "children": [{"title": "...", "intent": "...", "acceptance": "...", "dept": "ops", "after": [0]}]}`
or `{"kind": "needs_input", "question": "..."}`

One object, one hand-in. **Do not guess between them: try `holon_submit` first, and if it errors,
read the error — a refused result is rework, an unhanded-in result is nothing.**
