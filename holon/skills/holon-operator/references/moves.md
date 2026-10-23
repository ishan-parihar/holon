# What each move owes

The skill's table says which command performs a move. This says what has to be true for the move to
be safe. A move performed without these is a preference with a command on it.

## add

- **A mission only it owns.** Two departments with the same sentence are one department with a
  naming problem; merge rather than split.
- **Its reach before its staff.** `[scope.dept.<slug>]` roots, its checks, its secrets' **paths**
  (never values). A department hired before its reach is a person with no access.
- **Its outcome measure.** What `$holon metrics --dept <slug>` shows when it is working. If that
  cannot be stated, the boundary is wrong.
- **A candidate from outside lands unapproved.** An import is not an approval: `$holon venture
  import` puts every routine in the unapproved set, and the department's work does not consult them
  until the Chair or a pipeline approves.

## remove

- **Export first.** `$holon dept export <slug> --to <dir>` takes the profile, the people, the
  reach, the checks, the secret paths, the routines and the knowledge bases. Retiring a system
  without this loses everything the organization learned inside it.
- **Its employees retire with successors named**, so memory passes to the successor rather than
  dying with the department.
- **Its live work is cancelled with reasons** naming what replaced it. Work left claimable after
  its department is gone is work nobody can ever close.

## refactor

- **Change the person, not the body.** A harness is a body, not an identity (INTENT law 2).
  `$holon hr set-driver <emp> --driver <d>` rebinds an employee and keeps their record, their
  authority and their memory. Removing and re-hiring to change a harness loses all three, so it is
  never the answer.
- **A profile change is checked against the employee already in the department**, not against a
  fresh hire: a narrower profile can strand a specialist or leave a slot nobody in reach fills, and
  `$holon dept roles <slug>` after the change is the confirmation.

## expand and condense

- **Both on evidence.** Expand on a slot `$holon dept roles <slug>` shows vacant, a backlog the
  metrics show growing, a capability retros keep asking for. Condense on a profile heavier than the
  work needs, a slot nobody claims, a scope root nothing reads.
- **A department that stopped moving is a candidate to pause**, not to delete; its memory is the
  organization's only record of what it learned there.
- **`$holon gc --apply` collects what retention has already expired.** It collects only what
  policy already decided, but it is *not* unapprovable: without a backup newer than
  `[retention] backup_max_age_days` it is refused with `not allowed: no backup newer than 7 days`,
  and `--force` is the Chair's call to collect anyway. Run `holon backup` first, or name the force
  deliberately.

## heal

- **A fixed defect with nothing that re-checks it is a defect that comes back.** The kernel runs
  registered guards on every heartbeat and reopens the incident when one fails.
- **Closing an incident without a guard is a decision**: `$holon incident waive <work id> --note
  "…"` records why. The note is what a future reader weighs against the defect returning.
- **The guard names a `[checks.class]` entry the Chair authored**, never a command the fixer typed.
  A guard that runs an arbitrary command is a path from an employee's result to the kernel's user.

## evolve

- **The narrowest layer that fixes it.** One department's fork for a local fix; a proposal upward
  for one every department needs; the approver its autonomy names for a shared bylaws profile.
  Skills resolve department → organization → standard library, so the wrong layer puts one
  department's reflex in everyone's way.
- **A playbook change lands as a version, through verification.** The refinement runs as work, its
  executor returns the changed files as `skill_changes`, and the change is held until verified —
  an edit that goes straight to the playbook skips both the version and the gate.
- **The loop is closed by evidence, not by intent.** A retro that only describes changes nothing:
  its action is planned work, and the next retro reads what that work produced.
