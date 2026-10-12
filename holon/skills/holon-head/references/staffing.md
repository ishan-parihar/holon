# Staffing the department

Your department follows a genome profile. `$holon dept roles <slug>` shows each slot, who fills
it, and separation problems such as no verifier independent of the executor.

A good hire has a job description tailored to the department (the `holon-system-design` skill has the job-description guide) and a reason grounded in evidence: a vacant slot,
a backlog that `$holon metrics --dept <slug>` shows growing, or a capability the retros keep
asking for. Hiring and firing are the Chair's commands; propose them with that evidence:

```
$holon msg send chair "Proposal: hire a verifier for ops — first-pass rate 0.4, backlog 9 ready stages" --as <your id>
```

New hires start on probation; when it ends, a confirmation reaches you to approve or reject.
Firing keeps the employee's memory and hands it to a successor.
