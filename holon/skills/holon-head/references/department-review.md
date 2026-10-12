# Department review

Read the evidence, decide, and turn every decision into work, memory or a staffing proposal.
A review that only describes changes nothing.

1. Outcomes: `$holon metrics --dept <slug>` (each sensor with its counter-metric) and
   `$holon task list --dept <slug> --status failed,needs_input`.
2. People: `$holon hr review <emp_id>` for each employee.
3. Learning: what the steward's retros produced, and lessons promoted to `dept:<slug>`.
4. Decide, and record each decision so future work respects it:
   `$holon memory add --scope dept:<slug> --kind decision --content "…" --as <your id>`.
