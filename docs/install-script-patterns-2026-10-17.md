# Install Script Patterns — Compressed Findings

Source files analyzed (8 files, ~3400 LOC total):
- `agentic-harness/parent-projects/zeroclaw/install.sh` (1412 lines, POSIX sh)
- `agentic-harness/parent-projects/zeroclaw/setup.bat` (444 lines, Windows batch)
- `agentic-harness/parent-projects/hermes-agent/setup-hermes.sh` (306 lines, bash)
- `agentic-harness/parent-projects/hermes-agent/setup-hermes.ps1` (156 lines, PowerShell)
- `agentic-harness/operant/scripts/install.sh` (99 lines, bash)
- `holon/install.py` (291 lines, Python)
- `holon/adapters/{zeroclaw,hermes,jcode,operant}/install.py` (108/88/246/234 lines)
- Supporting: `holon/holon/execution/drivers.py`, `holon/adapters/*/driver.toml`, `holon/adapters/zeroclaw/{config,scope}.snippet.toml`, `holon/holon/execution/drivers/generic-shell.toml`

---

## 1. Script Structure

### ZeroClaw pair (`install.sh` + `setup.bat`)
Uses **generated-section markers** for tool-maintained code:
```
# >>> generated:route-contract by `cargo generate installers` - do not edit <<<
```
Markers: `route-contract`, `feature-picker-helper`, `rust-bootstrap`, `prebuilt`, `source-dispatch-open`, `feature-picker`, `source-cargo-install`, `app-selection`, `source-dispatch-close`, `path-handoff`, `quickstart-handoff`. The generator (`cargo generate installers`) emits these; hand-written glue surrounds them.

**Phase order** (both sh + bat):
1. Argument parsing (flags → mode vars)
2. Source-of-truth parsing (Cargo.toml: version, MSRV, edition, features, apps via `feature_members()` awk parser)
3. Install-routing decision (prebuilt vs. source — forced to source when any flag touches features/apps/gateway)
4. Prebuilt path: GitHub API → SHA256SUMS → download → verify → extract → install
5. Source path: clone/checkout → rustup bootstrap → MSRV check → RAM heuristic → cargo install
6. Web dashboard build (cargo web build, only when gateway feature present)
7. App discovery + installation (apps/*/Cargo.toml, Tauri apps skipped)
8. PATH injection (shell-profile detection + marker-block idempotency)
9. Quickstart prompt (3-way interactive: CLI, browser daemon, skip)
10. Final summary with smart next-step hint

`setup.bat` mirrors with `:labels` instead of functions; `--dry-run`, `--uninstall`, presets (`--prebuilt`/`--minimal`/`--dist`/`--default`/`--all`).

### Hermes pair (`setup-hermes.sh` + `setup-hermes.psw`)
**Phase order**:
1. Stage pinned `uv` from `pm/lock.json` (sha256-verified, mirror fallback)
2. Delegate to `pm.cli install` (Python + tool store + uv.lock sync)
3. `.env` seeding from `.env.example` (chmod 600)
4. Launcher publication via `hermes_cli/_launchers.py` → `$HOME/.local/bin`
5. Shell-profile PATH injection (zsh/bash/fish detection)
6. Skills sync (`tools.skills_sync` → fallback `cp -rn`)

PowerShell version: uses `ConvertFrom-Json` for lock parsing, `[Console]::OutputEncoding = UTF8` for path decoding, `Get-FileHash -Algorithm SHA256`.

### Operant (`install.sh`)
Linear, 6 labeled sections: native dep preflight → build → runtime lib install → binary to /usr/local/bin → skills seeding → systemd service register. Best-effort gates (`|| echo "WARN"`) on skills and gateway.

### Holon (`install.py` + `adapters/*/install.py`)
**Main installer (install.py)**: 7 numbered phases — prereqs → `holon init` → launcher → Notion mirror → Cloudflare tunnel → systemd units → doctor → onboarding runbook. Each phase prints `echo "N. phase name"`.

**Each adapter installer** follows a 4-step internal docstring contract:
1. Export skills to the harness's native skills directory
2. Write driver to `$HOLON_HOME/config/drivers/<harness>.toml`
3. Write harness-specific config (posture configs, provider profiles, feature toggles)
4. Verify the harness binary runs

## 2. OS Support / Cross-Platform Strategies

| Strategy | ZeroClaw `install.sh` | ZeroClaw `setup.bat` | Hermes bash | Hermes PS1 | Operant | Holon |
|---|---|---|---|---|---|---|
| Shell | `#!/bin/sh` POSIX | `@echo off` batch | `#!/usr/bin/env bash` | PowerShell | `#!/bin/bash` | Python 3.11+ |
| Unix | ✅ (Alpine/Debian/macOS/Termux) | N/A | ✅ | N/A | ✅ (Linux) | ✅ (all) |
| Windows | Via MinGW detection | ✅ native batch | Via MINGW* check | ✅ native PS | ❌ | ✅ (all) |

**Platform detection techniques:**
- ZeroClaw `install.sh`: `detect_libc()` checks `/lib/ld-musl-*`, `ldd --version`, `/etc/os-release` for Alpine → musl/glibc target triple. `detect_target_triple()`: Darwin → `sysctl hw.optional.arm64` for Rosetta truth (an Intel Mac under Rosetta reports `x86_64` from `uname -m`, false). Android detected via `$TERMUX_VERSION` or `/data/data/com.termux`.
- Hermes bash: Windows arch via `reg.exe query HKLM\...\Environment /v PROCESSOR_ARCHITECTURE` (env var lies under emulation; registry is truth). Then `shutil.which` equivalent in bash.
- Hermes PS1: `(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment').PROCESSOR_ARCHITECTURE` → ARM64 vs x64.
- ZeroClaw Windows: `powershell -Command "[math]::Round((Get-PSDrive $env:SystemDrive).Free / 1GB)"` for disk space (native batch can't do float math).
- Shell profile detection: ZeroClaw `detect_shell_profile()` reads `$SHELL` basename → `.zshrc` / `.bashrc` / `.bash_profile` / fish config.fish. Hermes bash checks `$SHELL` for zsh/bash.
- Fish shell: `set -gx PATH` syntax vs. `export PATH`.

**Cross-platform path resolution:**
- ZeroClaw: `resolve_web_data_dir()` — Darwin: `~/Library/Application Support/`, Windows: `%LOCALAPPDATA%`, Linux: `$XDG_DATA_HOME/.local/share/`
- ZeroClaw BAT: uses `%USERPROFILE%` and `%LOCALAPPDATA%` consistently with the sh install.sh paths
- Holon: Python `pathlib.Path.expanduser()` + `os.pathsep` for PATH splitting (platform-agnostic)

## 3. Package Manager Detection / Tool Bootstrapping

**ZeroClaw**: No package manager. Pre-built → GitHub releases API (`curl -fsSL https://api.github.com/repos/.../releases/latest | grep '"tag_name"'`). Source → bootstraps Rust via `rustup` if `command -v rustc` fails:
```sh
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
  --no-modify-path --default-toolchain stable
. "$CARGO_HOME/env"
```
MSRV check reads `rust-version` from Cargo.toml's `[workspace.package]`, compares via `version_gte()`.

**Hermes**: No package manager. Stages **pinned** uv from `pm/lock.json`:
```python
# lock.json has one member per line; awk follows object names + braces, not indentation
pin()  # reads url, sha256, version from the lockfile before Python is available
```
Downloads from origin URL; on curl failure (specific codes: 5,6,7,18,22,28,52,55,56), falls back to sha256-keyed mirror URL. Verifies sha256 before extracting. **No "latest" — only the pinned version.**

**Operant**: No package manager. Assumes Rust toolchain present. No bootstrap.

**Holon**: No package manager. Main installer: checks `sys.version_info >= (3, 11)` and `sqlite3.sqlite_version_info >= (3, 35)`. Adapter installers detect harness binaries via `shutil.which()`:
```python
# zeroclaw adapter: validates capability, not just presence
found = a.zeroclaw or shutil.which("zeroclaw")
rc, out = sh([zc, "agent", "--help"])
if "--agent" not in out and " -a" not in out:
    print(f"{zc} does not support `agent -a` (too old?)")
```
```python
# operant adapter: probes for native tools capability
def carries_native_tools(operant: str) -> bool:
    p = subprocess.run([operant, "tools", "list"],
        env={**os.environ, "HOLON_RUN_DIR": "/probe"}, timeout=60)
    return "holon_" in (p.stdout or "")
```

## 4. Prerequisite Checks

| Check | ZeroClaw | Hermes | Operant | Holon |
|---|---|---|---|---|
| Runtime version | Rust MSRV from Cargo.toml | uv sha256 from lock | N/A (Rust assumed) | Python 3.11+, SQLite 3.35+ |
| RAM | Linux `/proc/meminfo` < 12GiB → thin LTO | N/A | `wmic os get FreePhysicalMemory` (warn < 2GiB) | N/A |
| Disk space | N/A | N/A | PowerShell: `Get-PSDrive` free GB | N/A |
| CPU arch | `uname -m` + Rosetta detection + libc | `uname -m` + registry truth | `PROCESSOR_ARCHITECTURE` | N/A |
| 32-bit ARM | Hardblock with helpful error | N/A | N/A | N/A |
| Binary presence | `command -v rustc/cargo` | uv pin + sha256 | `where cargo/git/node` | `shutil.which(<harness>)` |
| Binary capability | N/A | N/A | N/A | `zeroclaw agent --help` supports `-a`; operant probes native tools |
| Checksum | SHA256SUMS from GitHub | sha256 from lockfile | N/A | N/A |
| Optional tools | git (source build) | N/A | cmake, pkg-config (warn if missing) | cloudflared, systemd (only when used) |

**ZeroClaw 32-bit ARM guard** (`install.sh` line 1008):
```sh
case "$(uname -m)" in
armv7l | armv6l | armhf)
  die "32-bit ARM detected — the default feature 'observability-prometheus'
requires 64-bit atomics and will not compile on this architecture."
```

## 5. Error Handling

All projects use a **"fail fast on real errors, degrade gracefully on non-critical"** model:

**Fatal patterns:**
- ZeroClaw: `die()` → colored ✗ + `exit 1`. SHA256 mismatch is fatal (`die "Checksum mismatch"`). Invalid Cargo feature → `die "Unknown feature '$1'"`. MSRV too old → `die`.
- Hermes: sha256 mismatch → `exit 1`. Missing `pm/lock.json` → `exit 1`. Failed download from all sources → `exit 1`.
- Holon: `SystemExit(returncode)` with codes: 0=success, 1=failure, 2=missing prerequisite. Invalid TOML before write → restore backup + return 1.
- All use `set -e` (or `set -eu`/`set -euo pipefail`).

**Non-fatal / best-effort patterns:**
- Operant: `bash "$(dirname "$0")/install-skills.sh" || echo "WARN: skill seeding failed (non-fatal)"`
- Operant gateway: `target/release/operant gateway install --force || echo "WARN: gateway service install failed (non-fatal)"`
- ZeroClaw: prebuilt → source fallback chain at **5 distinct failure points** — platform unsupported, API resolution failed, SHA256SUMS fetch failed, asset not in manifest, download failed. Each `return 1` triggers source build.
- ZeroClaw PATH injection: `>>"$PROFILE" 2>/dev/null` with fallback to `print_path_help()` if unwritable.

**Idempotency:**
- ZeroClaw: PATH marker blocks (`# >>> zeroclaw >>>` ... `# <<< zeroclaw <<<`) — stripped on uninstall via `sed '/marker/,/marker/d'`
- Holon: `strip_block()` removes old Holon blocks before re-injecting; `strip_block` for jcode handles `[features]` block that `jcode provider add` may rewrite

**Validation before write:**
- Holon: every adapter calls `tomllib.loads()` on generated TOML before writing it. If invalid → `toml.TOMLDecodeError` → backup restored.
- ZeroClaw: parses result back after feature selection: `CARGO_FLAGS="$CARGO_FLAGS --features $USER_FEATURES"` (validated via `validate_feature()`)
- Hermes: `--trust-recorded` flag for runtime-only installs (skips re-checking uv hash)

## 6. Env-Var / Config Handling

**Key security pattern (Holon):** API keys NEVER on command line.
```
--api-key-file ~/.config/holon/model.key   # path, not value
```
Then per-harness:
- **hermes**: `OPENAI_API_KEY=$(cat <file>)` as command prefix — read at runtime
- **jcode**: key piped via `--api-key-stdin` (`subprocess.run(..., input=key)`)
- **operant**: key written into posture config (`.toml`) with `chmod 0o600`
- **zeroclaw**: no key in adapter — uses ZeroClaw's own provider config

**Dry-run redaction** (holon operant adapter):
```python
shown = shown.replace(f'api_key = "{key}"', 'api_key = "<redacted>"')
# dry run never prints keys
```

**Config isolation (Holon):** Each adapter creates its own home:
```
$HOLON_HOME/adapters/jcode/home     → JCODE_HOME (not ~/.config/jcode)
$HOLON_HOME/adapters/operant/home   → HERMES_HOME (not ~/.operant)
```
Operant: `env = {**os.environ, "JCODE_HOME": str(jhome), "JCODE_NO_TELEMETRY": "1"}` — isolated config, sessions, memory, approval allowlist.

**Environment file handling:**
- ZeroClaw: `QUICKSTART_COMMAND="zeroclaw quickstart"` as a generated route-contract variable
- Hermes: `chmod 600 .env` after creation from `.env.example` (matches `scripts/install.sh` pattern)
- ZeroClaw `setup.bat`: ANSI color codes via `set "GREEN=[32m"` (Windows 10+ Terminal)

**PATH injection with idempotency:**
```sh
# ZeroClaw install.sh (generated:path-handoff)
if [ -f "$PROFILE" ] && grep -q "$CARGO_HOME/bin" "$PROFILE"; then
  PATH_ALREADY_SET=true
fi
# Marker-block injection:
printf '\n# >>> zeroclaw >>>\n'
printf '%s\n' "$EXPORT_LINE"
printf '# <<< zeroclaw <<<\n'
```

**Shell-profile detection:**
```sh
# ZeroClaw: reads $SHELL to pick the profile file
shell_name=$(basename "${SHELL:-/bin/bash}")
case "$shell_name" in
  zsh) echo "$HOME/.zshrc" ;;
  fish) echo "$HOME/.config/fish/config.fish" ;;
  *) echo "$HOME/.bashrc" ;;
```

## 7. Service / Daemon Registration

| Installer | Service System | Registered Units | Key Detail |
|---|---|---|---|
| ZeroClaw `install.sh` | `zeroclaw service install` (built-in) | daemon service | Uninstall calls `$bin service stop` + `service uninstall` |
| ZeroClaw `setup.bat` | N/A | N/A | No service install |
| Hermes sh/ps1 | N/A | N/A | `source ./activate` is the "on" switch |
| Operant `install.sh` | `systemctl --user` | `operant-gateway.service` | Unit file lives in the binary (`operant gateway install`); `--force`; guarded by `systemctl --user show boot.target` |
| Holon `install.py --sstemd` | `systemctl --user` | `holon-tick.{service,timer}`, `holon-notion.service`, `cloudflared-holon-notion.service`, optional `holon-broker.service` | `KillMode=process` on tick (not `control-group`) — prevents killing child dispatches |
| Holon `--heartbeat` | `systemctl --user` | `holon-tick.timer` | Enabled, starts dispatching work |

**Operant service guard**: `systemctl --user show boot.target` must succeed before writing units. Fallback: "Start the gateway manually with: operant gateway run"

**Holon systemd unit template** (written by `unit()` function):
```python
# Reads unit file from adapters/systemd/<name>, injects HOLON_HOME, checkout path, extra env
text = text.replace("Environment=HOLON_HOME=%h/holon", f"Environment=HOLON_HOME={home}")
text = text.replace("<holon-checkout>", str(ROOT))
```

**Holon KillMode fix** (from `systemd/holon-tick.service`):
```ini
# KillMode=control-group (default) kills everything in the unit's cgroup when a
# oneshot finishes, killing those dispatches before they run. Only kill the tick itself.
KillMode=process
```

## 8. Harness Detection (how each detects which harness the user runs)

**ZeroClaw / Hermes / Operant install scripts**: These ARE the harness installers themselves. They detect Rust/Python/system tools, not "which harness" — because they ARE the harness.

**Holon adapters**: Each adapter installer detects its target harness via `shutil.which()` plus a capability probe:

| Adapter | Detection | Capability validation | Failure mode |
|---|---|---|---|
| zeroclaw | `shutil.which("zeroclaw")` or `--zeroclaw` | `zeroclaw agent --help` must support `-a` (alias mode) | Refuses binary: "does not support `agent -a` (too old?)" |
| hermes | `shutil.which("hermes")` | `hermes --version` must run | Returns 2: "hermes is not on PATH" |
| jcode | `shutil.which("jcode")` or `--jcode` | `jcode --version` with `JCODE_HOME` set | Returns 2: "no jcode on PATH; pass --jcode" |
| operant | `shutil.which("operant")` or `--operant` + `HOLON_RUN_DIR=probe` env var | `operant tools list` must show `holon_` native tools (or not — both are valid) | Returns 2: "no operant on PATH; pass --operant" |

**Key insight**: Holon itself never names a harness. The kernel (`holon/init.py` line 1-2):
```python
"""holon kernel: a harness-agnostic agentic operating system.
The kernel is Python stdlib + sqlite3 only. It never imports, spawns by name,
or reads the state of any agent harness; harnesses plug in as driver files."""
```
AGENTS.md enforces: `holon lint` fails if "a harness is named in the kernel" (`test_no_harness_named_in_kernel`).

**Driver resolution** (`drivers.py`):
```python
# Search order: instance drivers override built-in examples
def available(home: Home) -> dict[str, Driver]:
    found = {}
    builtin = resources.files("holon.execution").joinpath("drivers")  # generic-shell.toml
    for f in sorted(builtin.iterdir()): ...
    if home.drivers_dir.exists():
        for f in sorted(home.drivers_dir.glob("*.toml")): ...  # overrides
    return found
```

## 9. Holon's Adapter Pattern (the N-harness auto-configuration pattern)

This is the central reusable pattern: **one kernel project auto-configures into N different harnesses** via per-harness adapter directories.

### Directory Layout
```
holon/
├── install.py                          # kernel installer (instance + deps + services)
├── holon/                              # kernel source (pathlib, never hands-free of harness)
├── execution/
│   ├── drivers.py                      # Driver dataclass + discovery (search order)
│   └── drivers/generic-shell.toml      # template for ANY harness
├── adapters/
│   ├── zeroclaw/                       # ZeroClaw adapter
│   ├── hermes/                         # Hermes adapter
│   ├── jcode/                          # jcode adapter
│   ├── operant/                        # Operant adapter
│   ├── claude-code/                    # Claude Code adapter (driver-only)
│   └── notion/                         # Notion mirror (NOT a harness)
│   ├── systemd/                        # systemd unit templates
│   └── notion/
│       ├── holon_notion.py             # Notion sync daemon
│       └── holon-notion.service        # unit template
└── adapters/<harness>/                 # THE PATTERN: one dir per harness
    ├── install.py                      # idempotent installer (shutil.which + validation)
    ├── driver.toml                     # driver template (TOML with placeholders)
    ├── README.md                       # what the harness does + verified facts
    ├── driver.toml                     # pinned at install time
    └── [posture configs]               # operant only: observe.toml, act.toml
```

### The Driver TOML Contract
Every harness driver is a TOML file with this schema (from `drivers.py`):
```toml
name      = "my-harness"
command   = "my-agent --prompt {briefing} --json"
cwd       = "{workdir}"                    # default
timeout_s = 3600
result    = "file" | "stdout-json"
read_only = "--plan-only"                  # substituted as {read_only} for observe stages
act_args  = "--yes"                        # substituted as {act_args} for execute stage
read_only_enforced = false                  # harness denies writes on observe?
stages    = ["research", "shape", ...]     # optional restriction
usage_line = 'in=(?P<tokens_in>[0-9]+) out=(?P<tokens_out>+)'  # optional: usage regex
build_cmd = "my-agent --version"           # optional: harness build identification
scope_fingerprint = ""                     # stamp from [scope] at install
[env]                                      # optional extra environment
FOO = "bar"
```

**Placeholder alphabet** (substituted at dispatch time by the kernel):
```
{brief_dir} {brief_md} {context_md} {identity_md} {result_path}
{workdir} {run_id} {task_id} {stage} {role} {posture} (observe|act)
{employee_id} {employee_slug} {read_only} {act_args}
{briefing} (= identity card + BRIEF.md, for harnesses taking one message)
{holon} (= kernel command, resolved at runtime)
{scope} (= department slug or "org")
{spawn} (= one-line spawn message pointing at BRIEF.md)
```

### Posture Mapping (the key abstraction)
The driver defines `read_only` and `act_args` as template strings. At dispatch:
- **Observe stages** (research, shape, plan, approve, verify, retro): `{read_only}` → the observe-stage args, `{act_args}` → ""
- **Execute stage**: `{read_only}` → "", `{act_args}` → the execute-stage args

Examples:
```toml
# hermes: observe = restricted tools, no --yolo; execute adds code_execution + --yolo
read_only = "-t file,terminal,skills,web,clarify"
act_args = "-t file,terminal,skills,web,clarify,code_execution --yolo"

# jcode: observe = disabled editing tools; execute = disabled selfdev+swarm
read_only = "--disabled-tools write,edit,multiedit,patch,apply_patch,replace,bg,selfdev,swarm"
act_args = "--disabled-tools selfdev,swarm"

# operant: no inline flags — uses separate config files selected by {posture}
command = "{operant} run -c \"{ohome}/{posture}.toml\" --max-iterations 40 --no-stream -q {briefing}"
read_only = ""
act_args = ""  # act.toml has command_allowlist = ["*"]
```

### The Adapter Installer Lifecycle (every install.py follows this shape)

**Step 1 — Detect the harness** (`shutil.which` + capability probe):
```python
found = a.zeroclaw or shutil.which("zeroclaw")
if found:
    zc = str(Path(found).expanduser().resolve())  # ABSOLUTE PATH — pinned for systemd
    rc, out = sh([zc, "agent", "--help"])
    if "--agent" not in out and " -a" not in out:
        return 2  # too old
elif a.dry_run:
    zc = "zeroclaw"  # fake it for dry-run
else:
    return 2  # "no zeroclaw on PATH"
```

**Step 2 — Export skills** (via the kernel, never copied manually):
```python
p = subprocess.run(
    [sys.executable, "-m", "holon", "--home", str(home), "skill", "export", "--to", str(skills_dir)],
    capture_output=True, text=True, cwd=str(REPO)
)
```
Skills are rendered from holon's layered skill system (base + dept fork) to harness-native format. "Registered: verified skill changes refresh them" — re-running the installer re-exports.

**Step 3 — Write harness-specific config** (driver templates + harness-native config):
- **zeroclaw**: Inject marked block into `~/.zeroclaw/config.toml` between `# ── Holon (agentic OS) employees` and `# ── end Holon ──`. Uses `config.snippet.toml` and `scope.snippet.toml` templates with `@SCOPE@`, `@MODEL_PROVIDER@`, `@SKILLS_DIR@`, `@HOLON_BIN@` placeholders. Creates per-scope agent aliases (`holon-<scope>-observe`, `holon-<scope>-act`) with risk profiles and allowed/forbidden paths.
- **hermes**: Render `driver.toml` via template substitution. Driver writes config to `$HOLON_HOME/config/drivers/` only — hermes' own config untouched. Model + endpoint pinned in driver env.
- **jcode**: Creates isolated `JCODE_HOME`, runs `jcode provider add holon --overwrite`, injects `[features]` block between markers (disable memory/swarm/ambient/updates), writes `mcp.json`, exports skills.
- **operant**: Writes two posture configs (`observe.toml`, `act.toml`) into isolated `HERMES_HOME`. Both set `[genome] unrestricted_default = "lockdown"`, `[context_injection] enabled = false`, `[socialization] enabled = false`, `[tools.kernel] enabled = false`. Execute posture gets `command_allowlist = ["*"]`. Probes binary for native tools to decide MCP mount.

**Step 4 — Pin binary path in driver** (critical for systemd timers with different PATH):
```python
# zeroclaw: pins absolute path in both driver and notify command
driver = (HERE / "driver.toml").read_text()
driver = driver.replace('command = "zeroclaw agent', f'command = "{zc} agent', 1)
# jcode: same pattern
driver = driver.rstrip() + f'\nJCODE_HOME = "{jhome}"\n'
```

**Step 5 — Validate before write** (TOML round-trip):
```python
tomllib.loads(text)  # never install an invalid driver/config
```

**Step 6 — Verify**:
```python
# Run the harness's version command to confirm it works
p = subprocess.run([operant, "--version"], capture_output=True, text=True,
    env={**os.environ, "HERMES_HOME": str(ohome)})
print(f"verify: {(p.stdout or p.stderr).strip()}")
```

### Skill Isolation Patterns
Each adapter exports skills to a **different directory** to prevent cross-harness contamination:
| Harness | Skills dir | Rationale |
|---|---|---|
| ZeroClaw | `$HOLON_HOME/adapters/zeroclaw/skills` | Referenced by `[skill_bundles.holon]` directory |
| Hermes | `<hermes_home>/skills/holon/` | As a Hermes skill **category** (`-s holon-employee` preloads protocol) |
| jcode | `$JCODE_HOME/skills` | Registered with jcode's skill system |
| Operant | `$HERMES_HOME/skills` | operant reads `[skills] root_dir` |

**Path rendering for cross-instance portability** (from `skills.py` export):
```python
# `$holon` is rendered as `${HOLON:-holon}`, the command each run exports,
# never one instance's path — so exported skills serve every instance
```

### MCP Mounting (per-run, token-authenticated)
```toml
# zeroclaw config.snippet.toml
[[mcp.servers]]
name = "holon"
transport = "stdio"
command = "@HOLON_BIN@"
args = ["mcp"]
tool_timeout_secs = 120
```
```toml
# jcode mcp.json
{"mcpServers": {"holon": {"command": "<holon>", "args": ["mcp"], "shared": false, "timeout_secs": 120}}}
```
**Key**: `shared: false` — one MCP process per run, inherits the run token (not a long-lived shared server). Operant installer probes whether the binary registers native `holon_*` tools; if so, skips the MCP mount entirely.

### Scope-Based Sandboxing (zeroclaw-specific, but pattern is portable)
The zeroclaw adapter creates **per-department-scope** agent aliases with distinct risk profiles:
```toml
# scope.snippet.toml — rendered once per scope from `holon scope`
[risk_profiles.holon-@SCOPE@-observer]
level = "full"
block_high_risk_commands = true
excluded_tools = ["file_write", "file_edit", ...]
forbidden_paths = [@FORBIDDEN_PATHS@]   # /etc, /root, ~/.ssh, ~/.config, etc.
allowed_commands = [@OBSERVER_COMMANDS@]

[risk_profiles.holon-@SCOPE@-worker]
excluded_tools = ["skill_manage"]       # workers can write, but not manage skills
allowed_commands = [@WORKER_COMMANDS@]  # broader: git, npm, cargo, python, etc.
```
The `scope_fingerprint` is stamped into the driver at install time; `holon doctor` reports `scope.drift` if `[scope]` changes.

## 10. Star / Donate / CTA Final Steps

**None found.** Zero out of eight install scripts contain any star/donate/sponsor/buy-me-a-coffee/ko-fi/open-collective CTAs. Instead, they uniformly conclude with:
- **Functional next-step commands** (e.g., ZeroClaw: "Run `zeroclaw quickstart`", "Run `zerocode` to launch the TUI")
- **Documentation links** (ZeroClaw: "Documentation: https://github.com/zeroclaw-labs/zeroclaw"; Holon: onboarding runbook referencing README §Usage)
- **Alternative install methods** (ZeroClaw setup.bat: "Alternative install via Scoop")

This is a deliberate design choice: "perfect install scripts" end with actionable next steps, not monetization prompts.

## 11. Reusable Patterns for "Perfect Install Scripts"

**P1. Generated-section markers**: `# >>> generated:xxx by <generator> - do not edit <<<` / `end generated:xxx`. Lets a code generator own parts of the script while hand-written glue surrounds it. ZeroClaw uses 10+ such markers.

**P2. Source-of-truth config**: Read version, features, apps from the project's own manifest (Cargo.toml, pm/lock.json), never hardcode. ZeroClaw's `feature_members()` awk parser reads `[features]` arrays; Hermes reads `pm/lock.json` with object-name-aware awk.

**P3. Pre-built → source fallback chain**: Try the fast path (pre-built binary with checksum), fail through to the robust path (source build) at every distinct failure point. ZeroClaw has 5 fallback gates.

**P4. sha256-verified binary staging with mirror fallback**: Download the exact pinned version; verify sha256; on network failure (specific curl codes), try a sha256-keyed mirror URL. Hermes stages uv this way.

**P5. Capability probing, not just presence**: Don't just check `which binary` — run a capability probe. zeroclaw adapter checks `zeroclaw agent --help` supports `-a`; operant adapter probes `operant tools list` with `HOLON_RUN_DIR=/probe` for native `holon_*` tools.

**P6. Absolute path pinning for systemd compatibility**: Resolve `shutil.which()` to `Path.resolve()` and pin the absolute path in driver files. Comment: "a systemd timer's PATH can find an older install."

**P7. TOML validation before write**: `tomllib.loads()` every generated TOML before writing. Never writes invalid config; backup + restore on failure.

**P8. Marked-block config injection**: Begin/end markers in the harness's native config file (`# ── Holon ──` / `# ── end Holon ──`). Enables clean idempotent re-injection and uninstall. jcode's `strip_block()` handles blocks that may have been rewritten by the harness itself.

**P9. Dry-run with key redaction**: `--dry-run` shows what would happen; all secret values redacted via regex substitution, never printed. Operant: `shown.replace(f'api_key = "{key}"', 'api_key = "<redacted>"')`.

**P10. Posture mapping via driver template args**: `read_only` / `act_args` placeholders substituted at dispatch time. The driver defines observe-stage and execute-stage argument sets; the kernel picks based on `{posture}`. ZeroClaw's observe alias uses `excluded_tools`; jcode uses `--disabled-tools`; operant uses separate TOML config files.

**P11. Isolated harness home per instance**: `JCODE_HOME=$HOLON_HOME/adapters/jcode/home`, `HERMES_HOME=$HOLON_HOME/adapters/operant/home`. Never writes to the user's own `~/.config/<harness>` — prevents cross-instance and cross-employee contamination.

**P12. Terminal-aware output + TTY gating**: `[ -t 1 ]` for color; `[ -t 0 ]` for interactive prompts. Non-TTY runs (`curl | bash`) silently use defaults. Fish shell gets `set -gx PATH` instead of `export PATH`.

**P13. Shell-profile-aware PATH injection**: Detect profile file from `$SHELL`, inject with marker blocks for idempotent uninstall, warn if not on PATH. ZeroClaw: `.zshrc`/`.bashrc`/`.bash_profile`/fish. Hermes: `.local/bin` injection with grep-based idempotency check.

**P14. Best-effort service registration with graceful degradation**: Check for systemd (`systemctl --user show boot.target`), skip with a helpful message if absent. Operant: "Start the gateway manually with: operant gateway run".

**P15. KillMode=process on systemd oneshots**: Use `KillMode=process` not `control-group` to avoid killing child dispatches spawned during the tick.

**P16. Cross-platform arch detection with emulation truth**: Windows: read registry `PROCESSOR_ARCHITECTURE` (not the env var, which lies under emulation). macOS: `sysctl hw.optional.arm64` to override Rosetta-misreported `uname -m`.

**P17. Low-memory build heuristics**: Read `/proc/meminfo` MemTotal; if < 12 GiB, set `CARGO_PROFILE_RELEASE_LTO=thin` (respects user override). Comment documents the specific OOM hazard.

**P18. Runtime library shipping**: Copy shared libraries into a per-user lib dir (`~/.local/lib/operant`) that the binary's rpath points to. Documents the `readelf -d | grep NEEDED` evidence that the dependency is hard (not soft).

**P19. Skill sync with fallback**: Try the dedicated sync script (`tools.skills_sync` / `holon skill export`); if it fails, fall back to `cp -rn` (best-effort, never abort install).

**P19. Interactive feature picker for TTY, silent defaults for pipe**: ZeroClaw's `interactive_feature_picker()` only runs `[ -t 0 ]`; non-interactive runs default to pre-built silently. Picker pre-checks defaults (expanded transitively from Cargo.toml `default`), prompts to stderr, returns via globals.

## 12. Test Isolation Patterns (from holon tests)

**Temporary home per test** (`helpers.py` `KernelCase`):
```python
self.tmp = Path(tempfile.mkdtemp(prefix="holon-test-"))
self.home = Home(self.tmp / "home")
```

**Chair key isolation** (critical security): test-wide temp key file set via env, plus per-test:
```python
TEST_KEY_FILE = Path(tempfile.mkdtemp(prefix="holon-test-keys-")) / "chair.key"
os.environ["HOLON_CHAIR_KEY_FILE"] = str(TEST_KEY_FILE)
ENV_KEYS = ("HOLON_CHAIR_KEY_FILE", "HOLON_CHAIR_KEY", "HOLON_KEY", "HOLON_RUN_TOKEN")
for k in ENV_KEYS: os.environ.pop(k, None)
```

**Harness mocking** (`tests/fake_agent.py`): A `#!/usr/bin/env python3` script written to disk, made executable, referenced as a driver command. Asserts the security contract at startup:
```python
assert os.environ.get("HOLON_RUN_TOKEN"), "push mode must export the run token"
assert os.environ["HOLON_POSTURE"] in ("observe", "act")
assert "HOLON_CHAIR_KEY" not in os.environ, "the Chair key must never reach an agent"
```

**Installer idempotency tests**: Rerun the installer, assert "a rerun changes nothing" — no new databases, no duplicate tunnels, no duplicate config blocks.

**Dry-run security test**: `self.assertNotIn("sk-secret", p.stdout, "a dry run must not print keys")`

**Capability regression test**: An old binary without required flags is refused: `self.assertEqual(run(old).returncode, 2, "an old zeroclaw without agent -a is refused")`

**Launcher ownership test**: Installer refuses to repoint a launcher belonging to another checkout (exit 1, "belongs to another checkout"). `--repoint` is the explicit override.

## 13. Security Patterns

| Pattern | Where | Code |
|---|---|---|
| Chair key never reaches harness | `fake_agent.py` runtime assertion | `assert "HOLON_CHAIR_KEY" not in os.environ` |
| API keys read from files, not CLI args | All holon adapters | `--api-key-file ~/.config/holon/model.key` |
| Config files with secrets chmod 0600 | holon integron/adapters | `f.chmod(0o600)` on posture configs |
| Dry-run redacts all secrets | holon operant install.py | `shown.replace(f'api_key = "{key}"', 'api_key = "<redacted>"')` |
| Isolated harness home per instance | holon all adapters | `JCODE_HOME=$HOLON_HOME/adapters/jcode/home` |
| Forbidden paths in sandbox config | holon zeroclaw adapter | `SYSTEM_FORBIDDEN = ["/etc", "/root", "/usr", "~/.config", ...]` |
| TOML validation before write | holon all adapters | `tomllib.loads(text)` before `write_text()` |
| Backup before config mutation | holon zeroclaw adapter | `cfg_path.with_name(f"{cfg_path.name}.bak.holon.{int(time.time())}")` |
| Test key isolation | holon test helpers | `TEST_KEY_FILE` in temp dir, not real config |
