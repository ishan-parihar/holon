#!/usr/bin/env python3
"""Install the Holon adapter into Hermes Agent (idempotent).

    python3 adapters/hermes/install.py --holon-home ~/holon [--hermes-home ~/.hermes] [--dry-run]

1. exports Holon's skills library into Hermes' skills directory, as the `holon` category
   (`<hermes home>/skills/holon/<skill>/`), so `-s holon-employee` preloads the protocol and every
   role skill is available; re-run after tailoring skills
2. installs the driver to $HOLON_HOME/config/drivers/hermes.toml
3. checks that `hermes` runs

Hermes reads any path its user can, so keep the Chair key away from it: run the Holon timer as a
different user, or keep the key off this machine and set HOLON_CHAIR_KEY only in your own shell.
Uninstall: delete <hermes home>/skills/holon and the driver file.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holon-home", default=str(Path("~/holon").expanduser()))
    ap.add_argument("--hermes-home", default=str(Path("~/.hermes").expanduser()))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    home = Path(a.holon_home).expanduser().resolve()
    skills_dir = Path(a.hermes_home).expanduser() / "skills" / "holon"
    drivers_dir = home / "config" / "drivers"
    print(f"skills: {skills_dir}\ndriver: {drivers_dir / 'hermes.toml'}")
    if a.dry_run:
        print("dry run: no changes written")
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
    shutil.copy2(HERE / "driver.toml", drivers_dir / "hermes.toml")
    p = subprocess.run(["hermes", "--version"], capture_output=True, text=True)
    print(f"verify: {(p.stdout or p.stderr).strip().splitlines()[0] if p.returncode == 0 else 'hermes --version failed'}")
    print("next: `holon adapter test hermes` runs the conformance suite against this harness")
    return 0 if p.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
