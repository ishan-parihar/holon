# Holon plugin for ZeroClaw

Makes ZeroClaw a body that Holon employees can run in. Holon stays harness-agnostic: this
directory is the *only* place that knows ZeroClaw exists.

| Piece | What it does |
|---|---|
| `driver.toml` | Runs each duty as its own agent alias: `zeroclaw agent -a holon-{duty} -m <brief>` |
| `config.snippet.toml` | Four agent aliases + two risk profiles (below), appended to `~/.zeroclaw/config.toml` between markers |
| `skill/holon-employee/` | A skill so any ZeroClaw session can work as a Holon employee in pull mode (`holon claim` / `holon submit`) |
| `install.py` | Idempotent installer with automatic config backup and TOML validation |
| `../systemd/holon-tick.*` | The heartbeat timer (harness-neutral; any timer works) |

## Safety model

| Alias | Risk profile | Can |
|---|---|---|
| `holon-plan`, `holon-review`, `holon-verify` | `holon-observer` | read files, read-only shell (`ls cat grep find git jq sqlite3 holon …`); **no write tools** |
| `holon-execute` | `holon-worker` | the approved plan's work, within `allowed_roots`; forbidden paths blocked |

Defense in depth: Holon also fails any plan or review run that changed its working
directory, verifies every execution with a different employee, and gates all work behind
graded approval. Known limits: `git`, `sqlite3` and shell redirection can still write on the
observer profile; the workdir hash catches writes *inside* the workdir only.

Observer runs can't write `result.json`, so they print the result JSON as their final message;
the driver uses `result = "stdout-json"` to pick it up (and still prefers `result.json` when present).

## Install

```sh
ln -sf ~/src/holon/bin/holon ~/.local/bin/holon        # lets agents run `holon` (allowlisted)
python3 adapters/zeroclaw/install.py --holon-home ~/holon --allow ~/.hermes/organism --dry-run
python3 adapters/zeroclaw/install.py --holon-home ~/holon --allow ~/.hermes/organism
holon hr hire ... --driver zeroclaw                     # employees now run on ZeroClaw
```
