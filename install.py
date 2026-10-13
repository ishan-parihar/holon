#!/usr/bin/env python3
"""Install Holon from this checkout onto a machine, cold or warm. Safe to run again.

    python3 install.py                                   # SQLite instance at ~/holon, Chair key, `holon` on PATH
    python3 install.py --notion-parent-page <page id> --notion-token-file ~/.config/holon/notion.token
    python3 install.py --systemd [--heartbeat]           # user units: Notion sync; --heartbeat also dispatches work

1. checks Python (3.11+) and SQLite (3.35+);
2. `holon init`: creates the instance (holon.toml, work.db and memory.db at the latest migration)
   and the Chair key (~/.config/holon/keys/<instance>.chair, 0600); an existing instance is migrated;
3. puts a `holon` launcher in --bin-dir;
4. with --notion-*: creates the four Notion databases under the page (adapters/notion) and pushes
   the organization; an instance already mirrored gets `check --repair` and a push instead;
5. with --systemd: writes the user units (holon-tick, and holon-notion when Notion is set up) and
   enables the Notion sync. The heartbeat dispatches work to employees, so it starts only with
   --heartbeat;
6. runs `holon doctor` and prints what is left to do (hiring, a harness adapter).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NOTION = ROOT / "adapters" / "notion" / "holon_notion.py"


def run(cmd: list[str], env: dict, check: bool = True) -> subprocess.CompletedProcess:
    print("  $ " + " ".join(cmd), flush=True)
    out = subprocess.run(cmd, env=env, text=True, capture_output=True)
    for line in (out.stdout + out.stderr).strip().splitlines():
        print("    " + line)
    if check and out.returncode:
        raise SystemExit(f"failed ({out.returncode}): {' '.join(cmd)}")
    return out


def holon(env: dict, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run([sys.executable, "-m", "holon", *args], env, check)


def unit(name: str, home: Path, extra_env: dict) -> str:
    text = (ROOT / ("adapters/notion" if name == "holon-notion.service" else "adapters/systemd") / name).read_text()
    text = text.replace("Environment=HOLON_HOME=%h/holon", f"Environment=HOLON_HOME={home}")
    text = text.replace("%h/src/holon", str(ROOT)).replace("/usr/bin/env holon tick", f"{ROOT}/bin/holon tick")
    env_lines = "".join(f"Environment={k}={v}\n" for k, v in extra_env.items())
    return text.replace("[Service]\n", "[Service]\n" + env_lines, 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=os.environ.get("HOLON_HOME", "~/holon"), help="the instance directory")
    ap.add_argument("--bin-dir", default="~/bin", help="where the `holon` launcher goes")
    ap.add_argument("--notion-parent-page", help="a Notion page shared with the integration")
    ap.add_argument("--notion-token-file", help="file holding the integration token (0600)")
    ap.add_argument("--notion-chair-user", action="append", default=[], help="Notion user id(s) of the Chair")
    ap.add_argument("--notion-ipv4", action="store_true", help="this host's IPv6 route is dead")
    ap.add_argument("--notion-api-base", help=argparse.SUPPRESS)  # tests
    ap.add_argument("--systemd", action="store_true", help="write the user units; enable the Notion sync")
    ap.add_argument("--heartbeat", action="store_true", help="also enable holon-tick (starts dispatching work)")
    ap.add_argument("--systemd-dir", default="~/.config/systemd/user")
    ap.add_argument("--no-start", action="store_true", help="write the units without enabling them")
    a = ap.parse_args(argv)
    home, bin_dir = Path(a.home).expanduser().resolve(), Path(a.bin_dir).expanduser()

    print("1. prerequisites")
    if sys.version_info < (3, 11):
        raise SystemExit(f"Python 3.11+ is required (this is {sys.version.split()[0]})")
    if sqlite3.sqlite_version_info < (3, 35):
        raise SystemExit(f"SQLite 3.35+ is required (this Python has {sqlite3.sqlite_version})")
    print(f"  python {sys.version.split()[0]}, sqlite {sqlite3.sqlite_version}")

    env = {**os.environ, "HOLON_HOME": str(home), "PYTHONPATH": str(ROOT)}
    if a.notion_ipv4:
        env["HOLON_NOTION_IPV4"] = "1"
    print(f"2. instance at {home}")
    home.mkdir(parents=True, exist_ok=True)
    holon(env, "init")

    print(f"3. launcher in {bin_dir}")
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / "holon"
    if launcher.is_symlink() or not launcher.exists():
        launcher.unlink(missing_ok=True)
        launcher.symlink_to(ROOT / "bin" / "holon")
        print(f"  {launcher} -> {ROOT / 'bin' / 'holon'}")
    else:
        print(f"  {launcher} exists and is not a link; left alone")

    notion_cfg = home / "adapters" / "notion" / "notion.json"
    if a.notion_parent_page or notion_cfg.exists():
        print("4. Notion mirror")
        base = [sys.executable, str(NOTION), "--holon-home", str(home)]
        set_up = notion_cfg.exists() and json.loads(notion_cfg.read_text()).get("data_sources")
        if set_up:
            run(base + ["check", "--repair"], env)
            run(base + ["push"], env)
        else:
            if not a.notion_token_file and not os.environ.get("NOTION_API_TOKEN"):
                raise SystemExit("--notion-token-file (or NOTION_API_TOKEN) is required to set Notion up")
            cmd = base + ["setup", "--parent-page", a.notion_parent_page]
            cmd += ["--token-file", str(Path(a.notion_token_file).expanduser())] if a.notion_token_file else []
            cmd += [x for u in a.notion_chair_user for x in ("--chair-user", u)]
            cmd += ["--api-base", a.notion_api_base] if a.notion_api_base else []
            run(cmd, env)
        run(base + ["check"], env)
    else:
        print("4. Notion mirror: skipped (no --notion-parent-page)")

    if a.systemd:
        print("5. systemd user units")
        dest = Path(a.systemd_dir).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        names = ["holon-tick.service", "holon-tick.timer"] + (["holon-notion.service"] if notion_cfg.exists() else [])
        for name in names:
            extra = {"HOLON_NOTION_IPV4": "1"} if name == "holon-notion.service" and a.notion_ipv4 else {}
            (dest / name).write_text(unit(name, home, extra))
            print(f"  wrote {dest / name}")
        if not a.no_start:
            start = (["holon-tick.timer"] if a.heartbeat else []) + [n for n in names if n == "holon-notion.service"]
            run(["systemctl", "--user", "daemon-reload"], env)
            if start:
                run(["systemctl", "--user", "enable", "--now", *start], env)
            if not a.heartbeat:
                print("  heartbeat written, not started: systemctl --user enable --now holon-tick.timer")
    else:
        print("5. systemd: skipped (--systemd writes the units)")

    print("6. doctor")
    holon(env, "doctor", check=False)
    print(f"""
Installed. Next:
  export HOLON_HOME={home}
  holon dept create <slug> --name … --mission … --profile light      # docs/GENOME.md
  python3 {ROOT}/adapters/<zeroclaw|jcode|hermes>/install.py --holon-home {home} …   # a harness
  holon hr hire --dept <slug> --slot lead --driver <harness> …        # employees
  holon status""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
