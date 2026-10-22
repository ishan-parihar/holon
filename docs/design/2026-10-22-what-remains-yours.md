---
status: superseded
date: 2026-10-22
description: "Three Chair actions measured on the live instance: isolation (two steps), the heartbeat timer, and the notify contract's dropped_marker. Superseded the same day by 2026-10-22-the-readiness-fixes.md: its heartbeat claim was falsified (the gateway's clock organ already runs the tick; the timer is a redundant second clock, now enabled anyway), and its isolation procedure was wrong in the ordering — `isolation apply` does not create the agent user (the root script does, and the broker unit must be written before agents can reach the organization). The Chair executed two of its three actions before the correction landed."
tags: [chair-actions, superseded]
supersedes: none
---

# What remains yours, and why each one matters (SUPERSEDED)

This record is superseded by `2026-10-22-the-readiness-fixes.md`. Do not follow its
isolation procedure or its heartbeat claim; the newer record carries the corrected
procedure, measured against `isolation.py`'s own printed steps, and the falsified
heartbeat diagnosis with what was actually done. Kept for history per the archive law:
never edit a record past its status line.
