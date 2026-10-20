#!/usr/bin/env python3
"""Install the Holon adapter into jcode (idempotent; everything lives in its own JCODE_HOME).

    python3 adapters/jcode/install.py --holon-home ~/holon --base-url <gateway-url>/v1 \\
        --model <model> --api-key-file ~/.config/holon/model.key [--dry-run]

1. creates JCODE_HOME=$HOLON_HOME/adapters/jcode/home, so Holon's employees never share the user's own
   jcode config, sessions or memory
2. adds the `holon` provider profile (an OpenAI-compatible endpoint; the key is read from the file and
   kept in jcode's private env file, never on a command line)
3. turns off what would fork the organization: jcode's own memory, swarm (sub-agents outside the work
   graph), ambient mode (unapproved proactive work) and self-updates
4. mounts the Holon MCP server (stdio, one process per run, so each run's token stays its own)
5. exports Holon's skills to $JCODE_HOME/skills (registered: verified skill changes refresh them)
6. installs the driver, pinned to this jcode binary and JCODE_HOME
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
import install_lib  # noqa: E402  (the shared installer seam: R3 of the 2026-10-20 bridge plan)

BEGIN, END = "# ── Holon: jcode features off for employees ──", "# ── end Holon ──"
BLOCK = f"""{BEGIN}
[features]
memory = false        # the organization's memory is Holon's
swarm = false         # sub-agents would work outside the work graph
check_updates = false # a harness upgrade is the Chair's change, not a side effect of a run

[ambient]
enabled = false       # no unapproved proactive work
{END}
"""


def strip_block(text: str) -> str:
    """The config without any copy of BLOCK: `jcode provider add` rewrites the file and can drop the BEGIN line."""
    return install_lib.strip_block(text, BEGIN, END, first_section="[features]")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holon-home", default=os.environ.get("HOLON_HOME", str(Path("~/holon").expanduser())))
    ap.add_argument("--jcode", help="the jcode binary (default: the first on PATH); its path is pinned in the driver")
    ap.add_argument("--base-url", required=True, help="OpenAI-compatible endpoint for Holon's employees")
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key-file", help="file holding the endpoint's key (omit for an unauthenticated endpoint)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    home = Path(a.holon_home).expanduser().resolve()
    jhome = home / "adapters" / "jcode" / "home"
    # R3: the pin is taken through the seam, which refuses a binary that cannot start —
    # a jcode that cannot run cannot serve a stage, and a driver that names it is worse
    # than no driver at all.
    try:
        jcode = install_lib.pin_binary("jcode", a.jcode, required=not a.dry_run, check=not a.dry_run)
    except install_lib.InstallError as e:
        print(f"jcode: {e}", file=sys.stderr)
        return 2
    driver = (HERE / "driver.toml").read_text().replace('command = "jcode run', f'command = "{jcode} run', 1)
    driver = driver.rstrip() + f'\nJCODE_HOME = "{jhome}"\n'
    tomllib.loads(driver)
    mcp = {"mcpServers": {"holon": {"command": str(REPO / "bin" / "holon"), "args": ["mcp"], "shared": False,
                                    "timeout_secs": 120}}}
    print(f"jcode: {jcode}\nJCODE_HOME: {jhome}\nprovider: holon -> {a.base_url} ({a.model})")
    if a.dry_run:
        print("dry run: no changes written\n--- driver ---\n" + driver + "--- mcp.json ---\n" + json.dumps(mcp, indent=2))
        return 0

    jhome.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "JCODE_HOME": str(jhome), "JCODE_NO_TELEMETRY": "1"}
    cfg = jhome / "config.toml"
    if cfg.exists():  # jcode refuses a file with the block twice (older installers left one)
        cfg.write_text(strip_block(cfg.read_text()))
    cmd = [jcode, "provider", "add", "holon", "--overwrite", "--base-url", a.base_url, "--model", a.model]  # a rerun updates it
    key = Path(a.api_key_file).expanduser().read_text().strip() if a.api_key_file else None
    cmd += ["--api-key-stdin"] if key else ["--no-api-key"]
    p = subprocess.run(cmd, input=key, capture_output=True, text=True, env=env)
    print("provider: " + ("set" if p.returncode == 0 else "FAILED: " + (p.stderr or p.stdout)[-300:]))
    if p.returncode != 0:
        return 1
    cfg.write_text(strip_block(cfg.read_text()).rstrip() + "\n\n" + BLOCK)
    tomllib.loads(cfg.read_text())
    (jhome / "mcp.json").write_text(json.dumps(mcp, indent=2))
    try:
        install_lib.export_skills(home, jhome / "skills")
    except install_lib.InstallError as e:
        print(f"skills: export FAILED — {e}", file=sys.stderr)
        return 1
    print("skills: exported")
    path = install_lib.write_driver(home, "jcode", driver)
    print(f"driver: {path}")
    print(f"verify: {install_lib.verify(home, jcode)}")
    print("next: `holon adapter test jcode` runs the conformance suite against this harness")
    return 0


if __name__ == "__main__":
    sys.exit(main())
