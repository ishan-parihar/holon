# Holon adapter for Claude Code

A driver for Claude Code's headless mode (`claude -p`). It implements the adapter shape in
`docs/CONTRACT.md` §7.

| Part | Here |
|---|---|
| Driver | `driver.toml`: `claude -p {prompt} --append-system-prompt-file {identity_md} --add-dir {brief_dir} …` |
| Identity | the identity card is appended to Claude Code's system prompt |
| Skills | optional: `holon skill export --to ~/.claude/skills` (the brief already names the role skill) |
| Posture map | observe stages: `--permission-mode plan` (no edits); execute: `acceptEdits` plus Bash |
| Conformance | `holon adapter test claude-code` |

Claude Code confines its tools to the working directory, so the run directory is added with
`--add-dir`; without it the agent can read neither its context nor write its result.

Install: copy `driver.toml` to `$HOLON_HOME/config/drivers/claude-code.toml`, then run
`holon adapter test claude-code`.
