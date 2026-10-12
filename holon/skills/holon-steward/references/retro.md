# Retro

1. Gather: `$holon task show <task_id>` (plans, review notes, result, verdict, events).
2. Find the cause, not the symptom: was the terrain missing, the plan wrong, the approval too
   lenient, the execution sloppy, or the acceptance criteria unclear?
3. Act on the cause:
   - a fact or rule the department should have known → `$holon memory add --scope dept:<slug> …`
   - a playbook that led people wrong → `references/evolution.md`
   - a capability gap → tell the head (staffing)
4. Record the retro itself as a `lesson` in `dept:<slug>` with the task id as provenance.
