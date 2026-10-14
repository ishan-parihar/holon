# Holon adapter for Hermes Agent

Lets Holon employees run as one-shot Hermes sessions. It implements the adapter shape in
`docs/CONTRACT.md` §7; this directory is the only place that knows Hermes exists.

| Part | Here |
|---|---|
| Driver | `driver.toml`: `hermes chat -Q -q {briefing} -s holon-employee …` |
| Identity | the identity card leads every query (`{briefing}`) |
| Skills | `holon skill export` → `~/.hermes/skills/holon/` (a Hermes skill category); `-s holon-employee` preloads the protocol |
| Posture map | observe stages: `-t file,terminal,skills,web,clarify`, no `--yolo`; execute: adds `code_execution` and `--yolo` |
| Conformance | `holon adapter test hermes` |

## Limits

Hermes has no read-only mode: an observe-stage session can still write through its file and
terminal tools. Holon fingerprints the working directory and fails any observe run that changed
it, but writes elsewhere are not detected, and Hermes can read any path its user can. Run Hermes
employees on low-risk departments, or under a separate OS user without access to the Chair key.

## Install

```sh
python3 adapters/hermes/install.py --holon-home ~/holon --dry-run
python3 adapters/hermes/install.py --holon-home ~/holon \
    --model <model> --base-url <gateway-url>/v1 --api-key-file ~/.config/holon/model.key
holon adapter test hermes            # 5/5 on Hermes v0.19.0, about 2.5 minutes
holon hr hire … --driver hermes
```

`--model`, `--base-url` and `--api-key-file` pin Holon's employees to one model on an
OpenAI-compatible endpoint without touching Hermes' own configuration (Hermes then needs no
provider of its own). The key file is read at run time; keep it mode 0600.

## Lessons from real runs

- Current Hermes reads `CUSTOM_BASE_URL`, not `OPENAI_BASE_URL`, for a custom endpoint.
- Hermes switches its stream-stall detector off for local endpoints, so one stalled upstream
  stream hung a run for 15 minutes. The driver sets `HERMES_LOCAL_STREAM_STALE_TIMEOUT`,
  `HERMES_STREAM_STALE_TIMEOUT` and `HERMES_API_TIMEOUT` (`--stall-timeout`, default 300 s).
- Skills exported to Hermes serve every instance: `$holon` is rendered as `${HOLON:-holon}`, the
  command each run exports, never one instance's path.
