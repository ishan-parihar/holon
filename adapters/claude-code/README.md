# Holon adapter for Claude Code

A driver for Claude Code's headless mode (`claude -p`). Observe stages run in plan mode, which
cannot edit; execute runs with `acceptEdits`. Copy `driver.toml` to
`$HOLON_HOME/config/drivers/claude-code.toml`, export the skills where Claude Code loads them
(`holon skill export --to ~/.claude/skills`), then run `holon adapter test claude-code`.
