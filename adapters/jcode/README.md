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
`driver` clause passes; `plan`, `approve`, `execute` and `verify` fail).

**What was wrong with the *first* diagnosis, and why it matters (corrected 2026-10-20).**
An earlier version of this file claimed that on v0.99.0 the run token does not reach the MCP
server, that the server authenticates as `{"principal": "chair"}`, and that every
`holon_submit` is therefore refused. **That claim was false, and it was falsified by direct
measurement:** with a *real* minted run token, the *live* `mcp.json` shape
(`args: ["mcp"]`, no `--home`) and the driver's exact environment (`HOLON_HOME` +
`HOLON_RUN_TOKEN`), the real `holon mcp` server answered
`{"principal": "employee", "employee_id": "emp_…", "run_id": "run_…"}`. **The token does
reach the child on v0.99.0.**

The earlier probe had used a token that was never real, and the failure mode looks
identical either way: a missing *or* an unknown token falls back to the chair principal.
The lesson is general: *a fallback identity is not evidence of a missing credential.*

**What is actually true, and is worth a guard.** A harness that loses the token does not
fail loudly — it **silently becomes the chair**. The MCP seam is where this bites, because
the harness starts the server and is therefore the only thing that can lose the token.
Guarded by `test_the_run_token_reaches_a_stdio_mcp_server` (the token must reach the child)
and `test_a_harness_without_the_token_is_refused_not_promoted_to_chair` (the fail-open).

**The one real cause of the 1/5: the body does not stop.** Measured 2026-10-20 with the
gateway repointed to the live one, a real `plan` dispatch to a real jcode-driven employee
ran **10 min 21 s**, **19 assistant turns**, **36 tool calls** (33 `bash`, 2 `read`,
1 `skill_manage`) and **0 submits** — it was cut by the kernel's own timeout while still
verifying facts. The body *did* reason its way to the right answer ("No write/edit/
apply_patch tool → use `holon_submit`… I'll use mcp_call to holon_submit") and then kept
exploring. A second session died the same way, reading the kernel's own source for the
grading floor the brief forbids. The open fix is a **driver turn cap** and a **brief that
names the exploration budget**; neither is implemented.

**Nothing here is fixed in the adapter yet.** Until conformance passes again, treat this
adapter as **unverified**: `holon adapter test jcode` is the gate, and it must reach 5/5.
