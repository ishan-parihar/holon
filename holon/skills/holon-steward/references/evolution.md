# Evolving skills, playbooks and the bylaws

Skills resolve in layers: department (`@<slug>`) → organization → standard library. Change the
narrowest layer that fixes the problem, so other departments are not affected.

1. The signal: counts and stage ids from retros and `$holon metrics --dept <slug>`.
2. The change: which skill file, which line, why. Name it in a retro action, so it becomes work:
   planned, graded, approved and executed like any other. The plan lists the file as a write,
   `skill:<skill>/<file>` (that floors it at G2); the executor returns the whole new file in
   `skill_changes`; once the work is verified the kernel writes it into the department's layer,
   rolls it back if `skill check` fails, and refreshes every harness that loads skills from disk.
3. The check: watch the next runs that use it; if the metric did not move, propose a revert.

Changes that widen authority, or alter a bylaws profile used by every department, go to the
approver the autonomy policy names: the Chair by default.
