# holon

A harness-agnostic agentic operating system kernel. A human feeds tasks in; agents plan
them; the human approves plans; agents execute and verify; results come back with
attached documentation, and every run feeds a project memory that the next run reads.

- **Zero dependencies.** Python ≥ 3.11 stdlib + `sqlite3`. Nothing to `pip install`.
- **Harness-agnostic.** The kernel never names, imports, or reads the state of any agent
  harness. A harness joins by a 10-line driver file (push mode) or by calling two commands
  (pull mode). Enforced by tests.
- **One pipeline, one audit trail.** Every transition is checked against a state machine
  and recorded.

## The pipeline

```
inbox → planning → awaiting_approval ──(you)──→ approved → executing → verifying → done
          │   └→ needs_input ─(you answer)─→ inbox      │                 └→ failed → retry | replan
          │                                              └→ stale plan (findings changed) → inbox
          └→ decompose ─(you approve)─→ waiting_on_subtasks → done when all sub-tasks close
  rejected plans return to inbox; the replan sees your review note
```

## Quick start

```sh
export HOLON_HOME=~/holon
bin/holon init
bin/holon task add "Summarize the Q3 logs" --intent "One-page summary of errors by service" \
      --acceptance "summary.md exists with one section per service" --workdir ~/logs
bin/holon agent add --role planner  --driver hermes --id planner-1
bin/holon agent add --role executor --driver hermes --id executor-1
bin/holon agent add --role verifier --driver hermes --id verifier-1
bin/holon schedule add --kind planner  --cron "*/10 * * * *" --agent planner-1
bin/holon schedule add --kind executor --cron "*/5 * * * *"  --agent executor-1
bin/holon schedule add --kind verifier --cron "*/5 * * * *"  --agent verifier-1
# from any timer (systemd, cron, launchd, a harness scheduler):
bin/holon tick
# your side:
bin/holon plan queue                 # plans waiting for you
bin/holon plan show <plan_id>
bin/holon plan approve <plan_id> [--note "..."] | bin/holon plan reject <plan_id> --note "what to change"
bin/holon task show <task_id>        # result + attached documentation
```

## Two ways a harness participates

| Mode | How | Use when |
|---|---|---|
| **push** | `holon tick` / `holon dispatch` claims work, writes a brief, runs the agent's driver | scheduled autonomous agents |
| **pull** | an agent running anywhere: `holon claim --role executor` → read `BRIEF.md` → write `result.json` → `holon submit <run_id>` | interactive sessions, harnesses you don't want to wrap |

See [docs/CONTRACT.md](docs/CONTRACT.md) for briefs, result envelopes, and drivers.

## Instance layout (`$HOLON_HOME`)

```
holon.toml                         instance config
config/drivers/*.toml                 harness drivers (override the shipped examples)
execution/runtime/work.db             tasks, plans, runs, results, agents, schedules, events, messages
execution/runtime/runs/<run_id>/      briefs and results
capabilities/memory/memory.db         project memory: items, episodes, checkpoints
capabilities/strategy/trajectory.db   intent hierarchy (read-only to the kernel; goal lineage in context)
capabilities/logbook/logbook.db       human evidence (read-only to the kernel)
```

The distribution (this repo) holds code only; all state lives in the instance.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```
