# Holon

**An agentic operating system that runs as an organization.** Persistent AI employees work in
departments under heads, led by a CEO, governed by a charter and supervised by you, the Chair.
Every piece of work leaves the organization smarter: lessons accumulate in employees, recur
across colleagues, and are promoted into department and company knowledge that every later brief
carries. Authority to approve is earned from verified results and lost on failures.

[![CI](https://github.com/ishan-parihar/holon/actions/workflows/ci.yml/badge.svg)](https://github.com/ishan-parihar/holon/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
![Dependencies](https://img.shields.io/badge/dependencies-0-success)

## Why an organization, not a task runner

Most agent systems hire anonymous contractors that do a job and vanish; nothing adds up. Holon
employees keep an identity, roles, a manager, a memory and a record, so the organization
compounds: departments get measurably better at their jobs, and the feedback loops between
employees, departments and the company keep evolving how the work itself is done
([docs/INTENT.md](docs/INTENT.md)).

- **Persistent employees, portable bodies.** Identity and memory belong to the organization, so
  an employee can change harness and keep its career.
- **Checked, not trusted.** Every CLI call is authenticated, every result is judged against a
  rubric, every machine-checkable claim is actually checked, and the audit trail is
  hash-chained.
- **Harness-agnostic.** The kernel names no harness (`holon lint` enforces it); bodies plug in
  through adapters.
- **Zero dependencies.** Python ≥ 3.11 standard library and SQLite ≥ 3.35.
- **Self-healing.** Leases, retries and incidents: a stage that keeps failing opens work for the
  IT department; `holon doctor` checks live state on every heartbeat.

## Installation

Requirements: Linux with Python ≥ 3.11 and SQLite ≥ 3.35. Nothing else.

```sh
git clone https://github.com/ishan-parihar/holon && cd holon
python3 install.py        # instance at ~/holon (SQLite + Chair key, 0600), `holon` on PATH
```

`install.py` checks the dependencies, creates or migrates the instance, puts a `holon` launcher
in `--bin-dir` (default `~/bin`), and ends with `holon doctor` and an onboarding runbook: which
adapter to install, whom to hire, how to run and approve your first task. `--systemd
--heartbeat` installs the 2-minute `holon tick` timer; `--notion-*` wires the Notion dashboard
(see below). A pip install also works: `pip install .` then `HOLON_HOME=~/holon holon init`.

## Usage

Give the employees a body — any OpenAI-compatible model endpoint — then hire. With the jcode
adapter (any of the four below works the same way):

```sh
python3 adapters/jcode/install.py --holon-home ~/holon \
    --base-url http://localhost:8000/v1 --model <model> --api-key-file ~/.config/holon/model.key
holon adapter test jcode          # conformance: a few real runs in a scratch organization

export HOLON_HOME=~/holon
holon hr hire --dept executive --slot ceo --name Ada --title CEO \
     --driver jcode --jd @jd/ceo.md --reason "founding CEO" --status active
holon dept create it --name "IT" --mission "Keep hosts, tools and credentials healthy" --profile light
holon hr hire --dept it --slot lead --name Bo --title "Head of IT" \
     --driver jcode --jd @jd/head-of-it.md --reason "founding head"
holon task add "Free disk space on the file server" --intent "Get / under 75% used" --dept it
holon schedule add --kind wake --cron "*/10 * * * *" --as <employee-id>
holon tick
```

Work then flows through the pipeline, and you steer it:

```sh
holon status                      # what needs you
holon plan approve <id> --note "…"     # or: holon plan reject <id> --note "what to change"
holon task tree <id>              # the holarchy under a piece of work
holon metrics --dept it           # the feedback loops' sensors
holon lint && holon doctor        # integrity: artifacts and live state
holon help                        # every command, and who may run it
```

```text
work ─► research? ─► shape? ─► plan ─► approve ─► execute ─► verify ─► done
             (genome rules decide)      (graded G0–G4, routed to the lowest approver whose
                                         earned authority covers it: head → CEO → Chair)
```

## Harnesses (the employees' bodies)

| Adapter | Harness | Notes |
|---|---|---|
| `adapters/zeroclaw` | ZeroClaw | sandboxed agent aliases per scope; Holon as native MCP tools |
| `adapters/hermes` | Hermes | one-shot sessions; see its README for read-only limits |
| `adapters/jcode` | [jcode](https://github.com/1jehuang/jcode) | about 3 MB per headless session |
| `adapters/operant` | Operant | Rust harness; isolated state home per Holon instance |
| `adapters/claude-code` | Claude Code | driver only; not yet through conformance |

Anything else: copy `holon/execution/drivers/generic-shell.toml` into your instance's
`config/drivers/` and point it at whatever runs one agent turn — or use **pull mode**: any agent
runtime claims work with `holon claim` and hands in results with `holon submit` or the MCP
server (`holon mcp`, run-token authenticated). The full contract for harness authors is
[docs/CONTRACT.md](docs/CONTRACT.md).

## How it works

- **Lanes.** Work runs cheapest-first: a script the kernel runs, one fast session for routine
  work, the full pipeline when it is needed. Each routine has a rubric, and its last verified
  run is its gold standard, shown to the next executor and its judge.
- **Learning loops.** Every stage's learnings consolidate into employee memory; failures and
  metrics open department retros whose actions become work; playbooks (a job's protocol and
  rubric as a versioned skill) refine themselves from evidence after verified runs.
- **Security.** Agents run as their own OS user and reach the organization only through a broker;
  run tokens authenticate every call; the Chair key never reaches a harness; observe stages are
  read-only, enforced by driver posture and fingerprinted workdirs.
- **Notion mirror (optional).** `adapters/notion` mirrors departments, employees, work and runs
  live, and takes your decisions, notes, answers and new work back
  ([adapters/notion/README.md](adapters/notion/README.md)).

State lives in the instance, never in this repo:

```text
$HOLON_HOME/
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

Docs: [INTENT](docs/INTENT.md) · [GENOME](docs/GENOME.md) (departments, roles, every config key)
· [CONTRACT](docs/CONTRACT.md) (for harness authors) · [the skills library](holon/skills) ·
[adapter READMEs](adapters/), which record what each real harness does.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The gates: `python3 -m unittest discover -s tests -t .`
and `python3 -m holon --home "$(mktemp -d)" lint --strict` (Python 3.11 and 3.13 in CI). A
defect found by a real run gets a guard test and a regression-ledger row in the same commit; this
repository stays free of instance and operator specifics (AGENTS.md).

## License

[MIT](LICENSE) © 2026 Ishan Parihar
