# Holon adapter for Operant

Makes Operant (Rust) a body that Holon employees run in. Holon stays harness-agnostic: this
directory is the only place that knows operant exists. It implements the adapter shape in
`docs/CONTRACT.md` §7.

| Part | Here |
|---|---|
| Driver | `driver.toml`: `operant run -c …/adapters/operant/{posture}.toml --max-iterations 40 --no-stream -q {briefing}` (identity card + stage brief) |
| Identity | carried in the query (`{briefing}`), so it reaches the system prompt whatever operant does with workspace identity files |
| Skills | `holon skill export` → `$HOLON_HOME/adapters/operant/home/skills`, set as `[skills] root_dir` |
| Native tools | the Holon MCP server (`bin/holon mcp`, stdio) in both posture configs — unless the pinned binary registers the `holon_*` native tools (probed at install): those builds get no MCP mount, the native tools are the organization's read/submit surface. Authenticated by the run token operant's child inherits |
| Posture map | `observe.toml`: write/mutation/notification tools disabled (the kernel's workdir fingerprint is the read-only check). `act.toml`: `command_allowlist = ["*"]` so an approved plan never stalls on a prompt an unattended harness cannot answer |
| Conformance | `holon adapter test operant` |

## Cast import (H9, phase G)

Operant's nine seeded seats map onto Holon departments, roles and kernel mechanisms
(ORG-LAYER-DISPOSITION.md §5 in operant's docs), and their cron jobs re-propose as routines
through the classifier:

```sh
python3 adapters/operant/import-cast.py               # dry run: the mapping, seat by seat
python3 adapters/operant/import-cast.py --apply       # hire the seat employees, propose the crons
# --dept-map seat=slug overrides where a seat lands (the apply refuses a department the
#   instance lacks); --stagger N spaces same-minute daily proposals N minutes apart (H-11);
#   an agent cadence ≥ 12×/day is flagged for review before you approve it.
```

premiere and chief-of-staff become executive routines with `--deliver`; governor's governance
digest lands in executive; identity-warden's audit lands in `it`; compass and chief-of-staff
are hired as executive employees (the seat's charter is the job description). crew-chief,
hrmaster, dispatcher and dp-the-program are already kernel mechanisms — the importer lists
them and proposes nothing. The command lives here, not in the kernel CLI, because the kernel
names no harness (law 2). Operant's databases are opened read-only; schedules drop their
leading seconds field.

## Isolation

Everything operant writes — config, sessions, memory, approval allowlist, `.env` — lives under
`HERMES_HOME=$HOLON_HOME/adapters/operant/home` (operant honors `HERMES_HOME`), so Holon's
employees never share the user's own operant state, and the Chair key never reaches a run. The
endpoint key is read from `--api-key-file` into that home's `.env` (mode 0600), never a command
line. The posture configs also pin operant's own org surfaces off (`[genome]` lockdown,
`[context_injection]` and `[socialization]` disabled, `[kernel]` — the continual-harness lane —
off) and keep `[memory]` on inside the isolated home: the body's own working memory, while the
organization's memory is Holon's (boundary S1/S3). The `cron`/`kanban` TOOLS are disabled through
`tools.disabled_tools` (they are tools, not config sections). Operant's only memory engine today
is memory-wire — there is no `[memory] provider` pin, and a personal memory provider in a Holon
run is the runtime's own isolation work (operant's R-08); until it lands, this README claims no
provider-level isolation.

Operant enforces `policy.json` per tool call (roots, commands, budget, deny — and `read_roots`
for the pooled units a run may only read): a call outside the policy is refused and reported in
`refusals`. `command_allowlist = ["*"]` in act.toml remains: the policy is the fence, and the
allowlist only keeps an unattended approved plan from stalling on a permission prompt no one can
answer.

## Result hand-in

`--no-stream` makes the non-TUI run print the final model reply as its last stdout; the brief
tells the agent to end its reply with the result envelope JSON, and `extract_last_json` picks it
up (`result = "stdout-json"`). An agent that prefers the tool route calls `holon_submit` on the
mounted MCP server instead — either path satisfies the contract.

## Install

```sh
python3 adapters/operant/install.py --holon-home ~/holon --base-url <gateway-url>/v1 \
    --model <model> --api-key-file ~/.config/holon/model.key
holon adapter test operant          # conformance: a few real runs in a scratch organization
holon hr hire … --driver operant     # employees now run on Operant
```

The installer pins the absolute path of the `operant` it found (`--operant PATH` to override)
in the driver and the MCP server block, and refuses to continue when no `operant` is on PATH.
Re-run without changes after tailoring skills, to re-export them.
