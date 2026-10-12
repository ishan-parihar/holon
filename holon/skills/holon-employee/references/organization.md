# How the organization is put together

## Chain of authority

```
Chair (human)  → edits the charter; approves what the charter reserves
  CEO          → heads the executive; turns objectives into department work
    Heads      → own a department's outcomes; approve within earned authority
      Employees → fill role slots: researcher, planner, executor, verifier, steward
```

## Work

A task moves `inbox → planning → awaiting_approval → approved → executing → verifying → done`.
A plan is graded G0 (routine) to G4 (critical or irreversible). The organization raises your
grade to an objective floor (risk, external effects, sensitive writes, novelty). The plan goes
to the lowest approver whose authority covers the grade: your head, then the CEO, then the
Chair. Trusted G0 results are verified on a sample (20% by default), not always.

## Authority is earned

Approvers gain authority after a run of verified successes and lose it on a failed
verification or an audit disagreement. Widening anyone's authority needs an approval
(the Chair by default); narrowing applies at once.

## Departments

Every department is the same holon: a profile (`light`: 3 slots, `standard`: 6 slots) whose
slots are filled by employees tailored to the department's purpose through their job
descriptions. `$holon dept roles <slug>` shows the slots and who fills them.

## Memory scopes

`emp:<id>` (yours) → `dept:<slug>` → `org`, plus the project and the task class. Lessons that
recur across employees are promoted to the department, across departments to the organization.
