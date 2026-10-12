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
| Usage | jcode's `--json` summary carries token usage; the kernel records it on the run |
| Conformance | `holon adapter test jcode` |

## Install

```sh
python3 adapters/jcode/install.py --holon-home ~/holon \
    --base-url http://127.0.0.1:20129/v1 --model free-stack --api-key-file ~/.config/holon/model.key
holon adapter test jcode
holon hr hire … --driver jcode
```

The driver pins the `jcode` launcher found on `PATH` (or `--jcode`) and the `JCODE_HOME`, so a
timer whose `PATH` differs still runs the same binary. Re-run the installer to change the model.

## What was verified on a real jcode (v0.93)

- `jcode run --json` prints `{"text": <reply>, "usage": {...}}`; the kernel reads the result
  envelope out of `text` and the token counts out of `usage`.
- An MCP server started with `shared: false` sees the run's environment (probe: a variable set for
  the run reached the server), which is what authenticates `holon_submit` and the other tools.
- jcode's base prompt is large (about 15k input tokens before the brief); budget for it.
