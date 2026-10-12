# Healing

- `$holon status` and `$holon events --limit 50`: runs expiring their lease, contract breaches,
  repeated retries.
- A run that expired twice, or a driver that keeps failing, is an incident: ask the IT
  department to fix it (`$holon msg request it "…" --intent "…"`) with the run ids.
- Backlog older than the department's cadence: tell the head with the task ids; propose
  re-assignment or a hire.
