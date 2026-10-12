# Holon adapter for ZeroClaw

Makes ZeroClaw a body that Holon employees run in. Holon stays harness-agnostic: this directory
is the only place that knows ZeroClaw exists. It implements the adapter shape in
`docs/CONTRACT.md` §7.

| Part | Here |
|---|---|
| Driver | `driver.toml`: `zeroclaw agent -a holon-{posture} -m {briefing}` (identity card + stage brief) |
| Identity | carried in every message (`{briefing}`), so it works whatever ZeroClaw does with workspace identity files |
| Skills | `holon skill export` → `$HOLON_HOME/adapters/zeroclaw/skills`, loaded as the `holon` skill bundle |
| Posture map | `holon-observe` (no write tools, read-only shell) for every stage but execute; `holon-act` for execute |
| Conformance | `holon adapter test zeroclaw` |

## Safety model

- **Observe stages** run on `holon-observer`: write, upload, download and skill-writing tools are
  excluded, and the shell allows only commands that cannot write (no `git`, no `sqlite3`, no
  interpreters). The kernel also fingerprints the working directory and fails a run that changed it.
- **Execute** runs on `holon-worker`, within the allowed roots.
- **Roots.** Agents reach only the run directories and the `--allow` paths. The instance's
  databases, `holon.toml`, drivers, skills and genome, and `~/.config` (where the Chair key lives),
  are forbidden paths. Agents change the organization only through `holon` with their run token.

## Install

```sh
python3 adapters/zeroclaw/install.py --holon-home ~/holon --allow ~/src/repo --dry-run
python3 adapters/zeroclaw/install.py --holon-home ~/holon --allow ~/src/repo [--model free-stack]
holon adapter test zeroclaw          # conformance: a few real runs in a scratch organization
holon hr hire … --driver zeroclaw     # employees now run on ZeroClaw
```

`--config-dir DIR` writes the block into a separate ZeroClaw config instead of `~/.zeroclaw`.
Upgrading from the pre-graph adapter (aliases `holon-plan`, `holon-execute`, …): re-run with
`--force`. Re-run without `--force` after tailoring skills, to re-export them.
