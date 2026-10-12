# Shaping incoming work

Turn a raw request into a task a planner can plan and a verifier can judge.

1. Read it and the requester's context: `$holon task show <task_id>`.
2. Check it belongs to your department's mission. If not, ask the owning department to take it
   (`$holon msg request <dept> "<title>" --intent "…"`) and tell the requester.
3. Make sure it has: a one-line outcome, **acceptance criteria** a verifier can check, a task
   class (reuse an existing one so the record accumulates), a risk level, and a working directory.
4. If research is needed first (unknown terrain, novel class, high risk), assign the research
   to your researcher before planning.
5. Assign the planner (and, if you know it, the executor) through the task's assignee.
