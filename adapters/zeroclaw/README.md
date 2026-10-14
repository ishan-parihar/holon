# Holon adapter for ZeroClaw

Makes ZeroClaw a body that Holon employees run in. Holon stays harness-agnostic: this directory
is the only place that knows ZeroClaw exists. It implements the adapter shape in
`docs/CONTRACT.md` §7.

| Part | Here |
|---|---|
| Driver | `driver.toml`: `zeroclaw agent -a holon-{scope}-{posture} -m {briefing}` (identity card + stage brief) |
| Identity | carried in every message (`{briefing}`), so it works whatever ZeroClaw does with workspace identity files |
| Skills | `holon skill export` → `$HOLON_HOME/adapters/zeroclaw/skills`, loaded as the `holon` skill bundle |
| Native tools | the Holon MCP server (`bin/holon mcp`, stdio) in the `holon` MCP bundle of both aliases: `holon_whoami`, `holon_submit`, `holon_memory_search`, … authenticated by the run token ZeroClaw passes to it |
| Chair notifications | `--notify-channel telegram --notify-recipient <chat id>` points Holon's `[notify]` hook at `zeroclaw channel send` |
| Posture map | `holon-<scope>-observe` (no write tools, read-only shell) for every stage but execute; `holon-<scope>-act` for execute |
| Department scope | one alias pair and risk-profile pair per scope from `holon scope` (`org`, plus each `[scope.dept.<slug>]`): the department's roots and commands are added to its own sandbox only |
| Conformance | `holon adapter test zeroclaw` |

## Why these native surfaces, and not a WASM plugin

ZeroClaw plugins are sandboxed WebAssembly components, loaded only by binaries built with the
`plugins-wasm` feature (release binaries are not), and a sandboxed component cannot reach Holon's
kernel or its databases anyway. What a stock ZeroClaw offers natively, and this adapter uses:
skill bundles (`[skill_bundles.holon]`), MCP servers scoped per agent (`[mcp_bundles.holon]`), agent
aliases with risk and runtime profiles, and channel sends. ZeroClaw cron jobs run as agent turns
(each one a model call), so Holon's heartbeat stays a plain timer (`adapters/systemd`).

## Safety model

- **Observe stages** run on `holon-<scope>-observer`: write, upload, download and skill-writing tools are
  excluded, and the shell allows only commands that cannot write (no `git`, no `sqlite3`, no
  interpreters) plus the commands `[scope]` grants the department. Unless the instance sets
  `trust_harness_read_only`, the kernel also fingerprints the working directory and fails a run
  that changed it.
- **Execute** runs on `holon-<scope>-worker`, within the allowed roots. Its shell includes `bash`,
  `sh` and interpreters, so its command list is not a fence: what bounds execution is the approved
  plan (graded against the department's reach) and the roots.
- **Roots.** Agents reach only the run directories, the `--allow` paths and their department's
  `[scope]` roots. The instance's
  databases, `holon.toml`, drivers, skills and genome, and `~/.config` (where the Chair key lives),
  are forbidden paths. Agents change the organization only through `holon` with their run token.

## Install

```sh
python3 adapters/zeroclaw/install.py --holon-home ~/holon --allow ~/src/repo --dry-run
python3 adapters/zeroclaw/install.py --holon-home ~/holon --allow ~/src/repo --model <model> \
    --notify-channel telegram --notify-recipient <your chat id>
holon adapter test zeroclaw          # conformance: a few real runs in a scratch organization
holon hr hire … --driver zeroclaw     # employees now run on ZeroClaw
```

`--config-dir DIR` writes the block into a separate ZeroClaw config instead of `~/.zeroclaw`.
The installer pins the absolute path of the `zeroclaw` it verified (`--zeroclaw PATH`, default
the first on `PATH`) in the driver and the notify command, and refuses a binary without
`agent -a`: a systemd timer's `PATH` differs from your shell's and can find an older install
(found on the first real heartbeat: `~/.local/bin` held 0.7.3, `~/.cargo/bin` 0.8.5).
Re-run without `--force` after tailoring skills, to re-export them. Re-run **with** `--force` after
changing `[scope]` in `holon.toml` (or when upgrading from an older adapter): the driver carries the
scope fingerprint it was built for, and `holon doctor` reports `scope.drift` until you do. Pass
the same `--allow`, `--model` and notify flags as the first install.
