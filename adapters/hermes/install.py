#!/usr/bin/env python3
"""Install the Holon adapter into Hermes Agent (idempotent).

    python3 adapters/hermes/install.py --holon-home ~/holon [--hermes-home ~/.hermes] [--dry-run]
        [--model free-stack --base-url http://127.0.0.1:20129/v1 --api-key-file ~/.config/holon/model.key]

1. exports Holon's skills library into Hermes' skills directory, as the `holon` category
   (`<hermes home>/skills/holon/<skill>/`), so `-s holon-employee` preloads the protocol and every
   role skill is available; re-run after tailoring skills
2. installs the driver to $HOLON_HOME/config/drivers/hermes.toml; with --model/--provider/--base-url
   the driver pins Holon's employees to that model and OpenAI-compatible endpoint without touching
   Hermes' own configuration. --api-key-file names a file read at run time, so no key is copied.
3. checks that `hermes` runs

Hermes reads any path its user can, so keep the Chair key away from it: run the Holon timer as a
different user, or keep the key off this machine and set HOLON_CHAIR_KEY only in your own shell.
Uninstall: delete <hermes home>/skills/holon and the driver file.
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def render_driver(a) -> str:
    text = (HERE / "driver.toml").read_text()
    provider = a.provider or ("custom" if a.base_url else None)
    flags = (f" -m {a.model}" if a.model else "") + (f" --provider {provider}" if provider else "")
    prefix = f"OPENAI_API_KEY=$(cat {shlex.quote(str(Path(a.api_key_file).expanduser()))}) " if a.api_key_file else ""
    text = text.replace('command = "hermes chat', f'command = "{prefix}hermes chat', 1).replace(
        "--max-turns 60", f"--max-turns 60{flags}", 1)
    if a.base_url:
        # Hermes disables its stream-stall detector for local endpoints unless told otherwise; a stalled
        # upstream stream would then hang the run until its lease expires
        text = text.rstrip() + (f'\n\n[env]\nCUSTOM_BASE_URL = "{a.base_url}"\n'
                                f'HERMES_LOCAL_STREAM_STALE_TIMEOUT = "{a.stall_timeout}"\n'
                                f'HERMES_STREAM_STALE_TIMEOUT = "{a.stall_timeout}"\n'
                                f'HERMES_API_TIMEOUT = "{a.stall_timeout * 3}"\n')
    tomllib.loads(text)  # never install an invalid driver
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holon-home", default=str(Path("~/holon").expanduser()))
    ap.add_argument("--hermes-home", default=str(Path("~/.hermes").expanduser()))
    ap.add_argument("--model", help="model for Holon's employees (hermes -m)")
    ap.add_argument("--provider", help="hermes --provider (default: auto)")
    ap.add_argument("--base-url", help="an OpenAI-compatible endpoint (Hermes' CUSTOM_BASE_URL; implies --provider custom)")
    ap.add_argument("--api-key-file", help="file holding that endpoint's key, read at run time (keep it 0600)")
    ap.add_argument("--stall-timeout", type=int, default=300,
                    help="seconds without streamed tokens before Hermes retries a model call (default 300)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    home = Path(a.holon_home).expanduser().resolve()
    skills_dir = Path(a.hermes_home).expanduser() / "skills" / "holon"
    drivers_dir = home / "config" / "drivers"
    print(f"skills: {skills_dir}\ndriver: {drivers_dir / 'hermes.toml'}")
    if a.dry_run:
        print("dry run: no changes written\n" + render_driver(a))
        return 0
    if not shutil.which("hermes"):
        print("hermes is not on PATH", file=sys.stderr)
        return 2
    p = subprocess.run([sys.executable, "-m", "holon", "--home", str(home), "skill", "export", "--to", str(skills_dir)],
                       capture_output=True, text=True, cwd=str(REPO))
    print(f"skills: {'exported' if p.returncode == 0 else 'export FAILED — ' + (p.stdout + p.stderr)[-300:]}")
    if p.returncode != 0:
        return 1
    drivers_dir.mkdir(parents=True, exist_ok=True)
    (drivers_dir / "hermes.toml").write_text(render_driver(a))
    p = subprocess.run(["hermes", "--version"], capture_output=True, text=True)
    print(f"verify: {(p.stdout or p.stderr).strip().splitlines()[0] if p.returncode == 0 else 'hermes --version failed'}")
    print("next: `holon adapter test hermes` runs the conformance suite against this harness")
    return 0 if p.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
