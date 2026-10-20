# Holon adapter for jcode

Makes [jcode](https://github.com/1jehuang/jcode) (Rust, MIT) a body Holon employees run in: about
3 MB of RAM per extra headless session, a coding-focused toolset, stdio MCP. Implements the adapter
shape in `docs/CONTRACT.md` §7.

| Part | Here |
|---|---|
| Driver | `jcode run --json -C {workdir} --provider-profile holon … {briefing}`: one headless run per stage, the identity card and stage brief as the message |
| Isolation | its own `JCODE_HOME` (`$HOLON_HOME/adapters/jcode/home`): provider profile, sessions and config never mix with your own jcode |
| Model | a named OpenAI-compatible profile (`holon`); the key is read from a file and kept in jcode's private env file |
| Native tools | the Holon MCP server (`bin/holon mcp`, stdio, `shared: false`): every run spawns its own, inheriting the run token |
| Skills | `holon skill export` → `$JCODE_HOME/skills` (registered: verified skill changes refresh it) |
| Posture | observe stages run with `--disabled-tools write,edit,multiedit,patch,apply_patch,replace,bg,selfdev,swarm`; the shell stays, so the kernel's read-only guard stays on |
| Turned off | jcode's own memory (the organization's is Holon's), swarm (sub-agents outside the work graph), ambient mode (unapproved proactive work), self-updates, telemetry |
| Usage | `--trace` prints a `token_usage` line per model call; the kernel sums them (`usage_line`) and records them on the run |
| Conformance | `holon adapter test jcode` |

## Install

```sh
python3 adapters/jcode/install.py --holon-home ~/holon \
    --base-url <gateway-url>/v1 --model <model> --api-key-file ~/.config/holon/model.key
holon adapter test jcode
holon hr hire … --driver jcode
```

The driver pins the `jcode` launcher found on `PATH` (or `--jcode`) and the `JCODE_HOME`, so a
timer whose `PATH` differs still runs the same binary. Re-run the installer to change the model.

## What was verified on a real jcode (v0.93)

- `jcode run --json` prints `{"text": <reply>, "usage": {...}}`; the kernel reads the result
  envelope out of `text` and the token counts out of `usage`.
- jcode's base prompt is large (about 15k input tokens before the brief); budget for it.
- Conformance 5/5 (2026-10-13): plan 6.2 min, approve 23.3, execute 10.4, verify
  11.3. Plan and execute submitted through `holon_submit` and the dispatcher stopped the harness
  once their results were in; that is why those two runs carry no token usage. The driver now runs
  jcode with `--trace` and sums its per-call `token_usage` lines (`usage_line`), which survive the
  stop. jcode's `input` already includes cached tokens, so the `--json` summary is no longer used for
  tokens.
- An earlier attempt timed out: the planner read Holon's source for an hour to derive the grade
  floor. Plan contexts now state the floor and the approver.

## What broke after v0.93 (2026-10-20) — read this before trusting the adapter

**On jcode v0.99.0 this adapter scores 1/5 in conformance** (`holon adapter test jcode`: the
`driver` clause passes; `plan`, `approve`, `execute` and `verify` fail). Two independent causes,
both measured on a real jcode:

1. **The run token does not reach the MCP server.** On v0.93 an MCP server started with
   `shared: false` saw the run's environment — a variable set for the run reached the server —
   and that is what authenticated `holon_submit`. **On v0.99.0 it does not**: a probe server
   printing its environment received no `HOLON_RUN_TOKEN`, so the server authenticated as
   `{"principal": "chair"}` and every `holon_submit` was refused with *"no run: pass run_id, or
   call from a run (HOLON_RUN_TOKEN)"*. jcode's `mcp.json` `env` block does **not** work around
   it. The fix is to launch the server through a wrapper that injects the token, or to make the
   CLI path primary (the body already has `$HOLON` and the token in its own environment).
   Guarded by `test_a_jcode_run_token_reaches_the_mcp_server`.
2. **The body does not stop.** Given a working provider it produced a *correct* plan envelope and
   *did* call `holon_submit` — which was then refused for cause 1. With the MCP mount removed it
   explored for 15+ minutes (58 tool calls) and never reached a submit. A driver turn cap and a
   brief that names the exploration budget are the open fix.

**Nothing here is fixed in the adapter yet.** The claim this file used to make — that the MCP
server sees the run's environment — was true on v0.93 and is false on v0.99.0; it is kept here as
a versioned statement, not as a current guarantee. Until conformance passes again, treat this
adapter as **unverified**: `holon adapter test jcode` is the gate, and it must reach 5/5.
