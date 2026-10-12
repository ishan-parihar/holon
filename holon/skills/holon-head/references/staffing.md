# Staffing your department

Your department follows a genome profile. `$holon dept roles <slug>` shows each slot, who
fills it, and any separation problems (for example, no verifier independent of the executor).

## Hire

Tailor the job description to the department's purpose. A good JD names:
responsibilities, the task classes the employee owns, the tools and systems it works in,
what it must never do, and the KPIs it is judged on (verified results, first-pass rate, rework).

```
$holon hr hire --dept <slug> --slot <slot> --name <name> --title "<title>" \
  --jd @jd.md --driver <driver> --reason "<why this hire, with evidence>"
```

New hires start on probation. Hiring is a proposal the CEO (or the Chair) may decide on when
the charter requires it.

## Review and fire

`$holon hr review <emp_id>` gives counted evidence: runs, verified results, first-try plan
approvals, rework, cost. Judge on that, not on impressions. Firing keeps the employee's memory
and hands it to a successor: `$holon hr fire <emp_id> --reason "…" --successor <emp_id>`.
