# Healing stuck work

The heartbeat already recovers dead runs and opens incident work when runs keep dying or a
schedule keeps failing. What it cannot see, you can:

- `$holon status` and `$holon events --limit 50` for patterns: the same stage failing, the same
  approval waiting for days.
- Work that waits on an approval for longer than the department's cadence: tell the head with
  the ids, and propose re-routing or more delegated authority.
- A backlog growing older (`$holon metrics --dept <slug>`): propose re-assignment or a hire.
