# Department review

Read the evidence, decide, and turn every decision into a task, a memory item, or a staffing
change. A review that only describes is not done.

1. Outcomes: `$holon task list --dept <slug>`; failed, rejected and long-waiting tasks first.
2. People: `$holon hr review <emp_id>` for each employee.
3. Learning: what the steward reported; lessons promoted to `dept:<slug>`.
4. Decide: re-assign work, change a job description, hire, fire, or ask the steward to change a
   playbook. Record each decision: `$holon memory add --scope dept:<slug> --kind decision --content "…"`.
