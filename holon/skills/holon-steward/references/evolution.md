# Evolving skills, playbooks and the genome

Skills resolve in layers: department (`@<slug>`) → organization → standard library. Change the
narrowest layer that fixes the problem, so other departments are not affected.

1. The signal: counts and stage ids from retros and `$holon metrics --dept <slug>`.
2. The change: which skill file, which line, why. Name it in a retro action, so it becomes work:
   planned, graded, approved and executed like any other. The executor tailors the file in the
   department's layer (the Chair forks it there with `$holon skill fork <skill> --dept <slug>`)
   and runs `$holon skill check`.
3. The check: watch the next runs that use it; if the metric did not move, propose a revert.

Changes that widen authority, or alter a genome profile used by every department, go to the
approver the autonomy policy names: the Chair by default.
