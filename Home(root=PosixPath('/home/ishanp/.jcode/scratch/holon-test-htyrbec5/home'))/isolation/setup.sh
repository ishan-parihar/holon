#!/bin/sh
# Holon isolation: the part only root can do, once (docs/BYLAWS.md §11). Creates the agent user and the
# runner service that starts agent processes as that user for this instance's kernel (uid 1000).
set -eu
id -u holon-agent-01m5sw3bx7kt2jsdgmj0pxryj0 >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/holon-agent-01m5sw3bx7kt2jsdgmj0pxryj0 --shell /usr/sbin/nologin holon-agent-01m5sw3bx7kt2jsdgmj0pxryj0
echo 'write this into holon.toml, then: holon isolation apply --force'
echo '[isolation]'
echo 'agent_user = "holon-agent-01m5sw3bx7kt2jsdgmj0pxryj0"'
echo 'runner = "@holon-runner-01m5sw3bx7kt2jsdgmj0pxryj0"'
cat > /etc/systemd/system/holon-runner-01m5sw3bx7kt2jsdgmj0pxryj0.service <<'UNIT'
[Unit]
Description=Holon runner: agent processes as holon-agent-01m5sw3bx7kt2jsdgmj0pxryj0 for ishanp's instance 01m5sw3bx7kt2jsdgmj0pxryj0
After=network-online.target

[Service]
User=holon-agent-01m5sw3bx7kt2jsdgmj0pxryj0
WorkingDirectory=/var/lib/holon-agent-01m5sw3bx7kt2jsdgmj0pxryj0
# No PYTHONPATH pinned to the planning checkout: the 2026-10-21 rule, applied here. The unit names
# no tree, so a moved or deleted worktree does not kill every agent process with nothing in the
# journal naming the cause.
# systemd's service PATH omits per-user bin dirs, where the `holon` launcher lives (the tick unit
# carries the same line for the same reason). Without it, `command -v holon` finds nothing below and
# the start shell prints `readlink: missing operand` -- and exits 0, so systemd records a healthy
# unit that never started a runner. Measured on 2026-10-23 under
# `env -i PATH=/usr/local/bin:/usr/bin:/bin`.
Environment=PATH=%h/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/sh -c 'H=$(dirname $(dirname $(readlink -f $(command -v holon)))) && PYTHONPATH="$H" exec /usr/bin/python3 -m holon.execution.isolation runner --address @holon-runner-01m5sw3bx7kt2jsdgmj0pxryj0 --allow-uid 1000'
Restart=always
RestartSec=10
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now holon-runner-01m5sw3bx7kt2jsdgmj0pxryj0.service
