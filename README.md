# Holon

**An agentic operating system that runs as an organization.** Persistent AI employees work in
departments under heads, led by a CEO, governed by a charter, and supervised by you, the Chair.
Every piece of work leaves the organization smarter: lessons accumulate in employees, recur
across colleagues and are promoted into department and company knowledge that every later brief
carries. Authority to approve is earned from verified results and lost on failures, so the
organization takes on more as it proves itself. Read [docs/INTENT.md](docs/INTENT.md) first.

- **Zero dependencies.** Python ≥ 3.11 standard library and SQLite ≥ 3.35.
- **Harness-agnostic.** Employees run on any agent harness through a driver file and an adapter
  (ZeroClaw and Hermes ship in `adapters/`), or by calling `holon claim` and `holon submit`.
  The kernel names no harness, and `holon lint` enforces that.
- **Checked, not trusted.** Every CLI call is authenticated; `holon lint` holds the artifacts to a
  rubric and `holon doctor` checks the live state.

## How work flows

```
work ─► research? ─► shape? ─► plan ─► approve ─► execute ─► verify ─► done
            (genome rules decide)      (graded G0–G4, routed to the lowest approver whose
                                        earned authority covers it: head → CEO → Chair)
a plan may split work into child work in other departments; rework appends execute#2 / plan#2;
failures and repeated rejections queue a retro whose actions become new work
```

## Quick start

```sh
export HOLON_HOME=~/holon
bin/holon init                                   # creates the instance and your Chair key
bin/holon hr hire --dept executive --slot ceo --name Ada --title CEO \
     --driver zeroclaw --jd @jd/ceo.md --reason "founding CEO" --status active
bin/holon dept create it --name "IT" --mission "Keep hosts, tools and credentials healthy" --profile light
bin/holon hr hire --dept it --slot lead --name Bo --title "Head of IT" \
     --driver zeroclaw --jd @jd/head-of-it.md --reason "founding head"
bin/holon task add "Free disk space on the VPS" --intent "Get / under 75% used" --dept it
bin/holon schedule add --kind wake --cron "*/10 * * * *" --as <employee-id>
bin/holon tick                                   # from any timer

bin/holon status                                 # what needs you
bin/holon plan queue                             # plans routed to you
bin/holon plan approve <id> --note "…"           # or: holon plan reject <id> --note "what to change"
bin/holon task tree <id>                         # the holarchy under a piece of work
bin/holon metrics --dept it                      # the feedback loops' sensors
bin/holon lint && bin/holon doctor               # integrity: artifacts and live state
bin/holon help                                   # every command and who may run it
```

## Instance layout (`$HOLON_HOME`)

```
holon.toml                            configuration (docs/GENOME.md §6)
skills/[@<dept>/]<skill>/             skills tailored for the organization or one department
genome/<profile>.toml                 instance genome profiles
config/drivers/*.toml                 harness drivers (adapters install them)
execution/runtime/work.db             organization, work graph, runs, outputs, messages, audit trail
execution/runtime/runs/<run>/         BRIEF.md, brief.json, identity.md, context.md, result.json, driver.log
capabilities/memory/memory.db         institutional memory
capabilities/strategy/trajectory.db   goals tree (read-only to the kernel)
capabilities/logbook/logbook.db       the Chair's logbook (read-only to the kernel)
```

Docs: [INTENT](docs/INTENT.md) · [GENOME](docs/GENOME.md) · [CONTRACT](docs/CONTRACT.md) (for harness
authors) · [design records](docs/design/).

## Tests

```sh
python3 -m unittest discover -s tests -t .
```
