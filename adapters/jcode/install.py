#!/usr/bin/env python3
"""Install the Holon adapter into jcode (idempotent; everything lives in its own JCODE_HOME).

    python3 adapters/jcode/install.py --holon-home ~/holon --base-url http://127.0.0.1:20129/v1 \\
        --model free-stack --api-key-file ~/.config/holon/model.key [--dry-run]

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
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
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
    found = a.jcode or shutil.which("jcode")
    if not found and not a.dry_run:
        print("no jcode on PATH; pass --jcode", file=sys.stderr)
        return 2
    jcode = os.path.abspath(os.path.expanduser(found)) if found else "jcode"  # the launcher, not a build dir
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
    cmd = [jcode, "provider", "add", "holon", "--base-url", a.base_url, "--model", a.model]
    key = Path(a.api_key_file).expanduser().read_text().strip() if a.api_key_file else None
    cmd += ["--api-key-stdin"] if key else ["--no-api-key"]
    p = subprocess.run(cmd, input=key, capture_output=True, text=True, env=env)
    print("provider: " + ("added" if p.returncode == 0 else "FAILED: " + (p.stderr or p.stdout)[-300:]))
    if p.returncode != 0:
        return 1
    cfg = jhome / "config.toml"
    text = cfg.read_text() if cfg.exists() else ""
    if BEGIN in text:
        text = text[:text.index(BEGIN)].rstrip() + "\n" + text[text.index(END) + len(END):].lstrip("\n")
    cfg.write_text(text.rstrip() + "\n\n" + BLOCK)
    tomllib.loads(cfg.read_text())
    (jhome / "mcp.json").write_text(json.dumps(mcp, indent=2))
    p = subprocess.run([sys.executable, "-m", "holon", "--home", str(home), "skill", "export", "--to",
                        str(jhome / "skills")], capture_output=True, text=True, cwd=str(REPO))
    print("skills: " + ("exported" if p.returncode == 0 else "export FAILED: " + (p.stdout + p.stderr)[-300:]))
    if p.returncode != 0:
        return 1
    drivers = home / "config" / "drivers"
    drivers.mkdir(parents=True, exist_ok=True)
    (drivers / "jcode.toml").write_text(driver)
    print(f"driver: {drivers / 'jcode.toml'}")
    p = subprocess.run([jcode, "--version"], capture_output=True, text=True, env=env)
    print(f"verify: {(p.stdout or p.stderr).strip()}")
    print("next: `holon adapter test jcode` runs the conformance suite against this harness")
    return 0 if p.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
