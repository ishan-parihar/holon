# Holon

**An agentic operating system that runs as an organization.** Persistent AI employees work
in departments under heads of department (HODs), led by a CEO, governed by a charter, and
supervised by you, the Chair. Every task leaves the organization smarter: lessons accumulate
in employees, recur across colleagues, and are promoted into department and company
knowledge that every future brief carries. Authority to approve work is earned from
verified results and lost on incidents, so the organization takes on more as it proves
itself. See [docs/INTENT.md](docs/INTENT.md).

- **Zero dependencies.** Python ≥ 3.11 stdlib + `sqlite3`.
- **Harness-agnostic.** Employees run on any agent harness (ZeroClaw, Claude Code, operant,
  hermes…) through a small driver file, or by calling `holon claim` / `holon submit`. The
  kernel never names a harness (enforced by tests). An employee's identity and memory
  belong to Holon, so they can change harness and keep their career.

## How work flows

```
Chair drafts a task ─► CEO triages / splits it into department tasks ─► HOD plans or splits to employees
   ─► plan graded G0–G4 (max of planner's grade and an objective floor)
   ─► routed to the lowest approver whose earned authority covers the grade (HOD → CEO → Chair)
   ─► employee executes ─► a colleague (never the executor) verifies ─► done, documentation attached
   ─► lessons → employee memory → department → organization; approver's authority updated from the outcome
```

Task states: `inbox → planning → awaiting_approval → approved → executing → verifying → done`,
plus `needs_input`, `waiting_on_subtasks`, `failed`, `cancelled`. Stale plans (the paths they
write changed) are replanned; dead runs are recovered by `holon tick`.

## Quick start

```sh
export HOLON_HOME=~/holon
bin/holon init
bin/holon hr hire --dept executive --slot ceo --name Ada --title CEO \
     --driver zeroclaw --jd @jd/ceo.md --reason "founding CEO" --status active
bin/holon dept create it --name "IT" --mission "Keep hosts, tools and credentials healthy" --profile light
bin/holon hr hire --dept it --slot lead --name Bo --title "Head of IT" \
     --driver zeroclaw --jd @jd/head-of-it.md --reason "founding HOD"
bin/holon task add "Free disk space on the VPS" --intent "Get / under 75% used" --dept it
bin/holon schedule add --kind plan    --cron "*/10 * * * *" --as <hod-id>
bin/holon schedule add --kind review  --cron "*/10 * * * *" --as <ceo-id>
bin/holon tick                         # from any timer: systemd, cron, a harness scheduler

bin/holon status                       # what needs you
bin/holon plan queue                   # plans routed to you (G2+ until authority is earned)
bin/holon plan approve <plan> --note "…" | bin/holon plan reject <plan> --note "what to change"
bin/holon audit samples                # spot-check approvals your CEO/HODs made
bin/holon org                          # the org chart
bin/holon dept roles it                # the department's role slots and who fills them
bin/holon skill list --as <emp-id>     # the skills an employee works with; `skill invoke` renders one
bin/holon charter autonomy             # how much each department may decide without you
```

## Instance layout (`$HOLON_HOME`)

```
holon.toml                            config: autonomy, grading floors, memory promotion, context budget
skills/[@<dept>/]<skill>/             skills tailored for the organization or one department (override holon/skills)
genome/<profile>.toml                 instance genome profiles (override holon/genome)
config/drivers/*.toml                 harness drivers
execution/runtime/work.db             departments, employees, authority, tasks, plans, runs, results, messages
execution/runtime/runs/<run>/         BRIEF.md, brief.json, context.md, result.json, driver.log
capabilities/memory/memory.db         institutional memory (emp / dept / org / project / class scopes)
capabilities/strategy/trajectory.db   goals tree (read-only to the kernel)
capabilities/logbook/logbook.db       the Chair's logbook (read-only to the kernel)
```

Docs: [INTENT](docs/INTENT.md) · [GENOME](docs/GENOME.md) (roles, profiles, skills, autonomy) · [CONTRACT](docs/CONTRACT.md) (for harness authors) · [NAMING](docs/NAMING.md)

## Tests

```sh
python3 -m unittest discover -s tests -t .
```
