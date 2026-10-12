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
python3 adapters/hermes/install.py --holon-home ~/holon
holon adapter test hermes
holon hr hire … --driver hermes
```
