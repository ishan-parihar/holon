# Evolving playbooks, skills and the genome

Skills resolve in layers: department (`@<slug>`) → organization → standard library. Change the
narrowest layer that fixes the problem.

1. Fork the file you want to change into your department's layer:
   `$holon skill fork <skill> --dept <slug> --file <path>`
2. Propose the change as a task (so it is planned, graded and approved like any other work):
   `$holon msg request <your dept> "Change <skill>/<file>" --intent "<the signal, with counts and task ids; the change; how we will know it worked>"`
3. After approval, the executor edits the forked file and runs `$holon skill check --dept <slug>`.
4. Watch the next runs that use it. If the metric did not move, revert it.

Changes that widen anyone's authority, or alter a genome profile for every department, go to
the Chair (or the approver set by the autonomy policy).
