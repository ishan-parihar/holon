#!/usr/bin/env python3
"""Install Holon from this checkout onto a machine, cold or warm. Safe to run again.

    python3 install.py                                   # SQLite instance at ~/holon, Chair key, `holon` on PATH
    python3 install.py --notion-parent-page <page id> --notion-token-file ~/.config/holon/notion.token
        [--notion-webhook-hostname holon-notion.example.com]  # webhooks through a Cloudflare tunnel
    python3 install.py --systemd [--heartbeat]           # user units: Notion sync; --heartbeat also dispatches work

1. checks the dependencies: Python (3.11+) and SQLite (3.35+). Everything else is optional and
   checked only when used: a harness (each adapter installer checks its own), `cloudflared`
   (only for Notion webhooks), a systemd user session (only for the units);
2. `holon init`: creates the instance (holon.toml, work.db and memory.db at the latest migration)
   and the Chair key (~/.config/holon/keys/<instance>.chair, 0600); an existing instance is migrated;
3. puts a `holon` launcher in --bin-dir;
4. with --notion-*: creates the four Notion databases under the page (adapters/notion) and pushes
   the organization; an instance already mirrored gets `check --repair` and a push instead;
5. with --notion-webhook-hostname: a dedicated Cloudflare tunnel (`holon-notion`) routes
   https://<hostname>/notion/… to the sync's webhook receiver; needs `cloudflared` logged in
   (`cloudflared tunnel login`). Without it, the sync polls Notion every minute instead;
6. with --systemd: writes the user units (holon-tick; holon-notion and its tunnel when set up) and
   enables the Notion sync and tunnel, then checks the webhook URL answers. The heartbeat dispatches
   work to employees, so it starts only with --heartbeat;
7. runs `holon doctor`, then prints the onboarding runbook: the adapter, hires and approvals that
   turn a bare instance into a working organization (README.md §Usage walks the same steps).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import tomllib
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NOTION = ROOT / "adapters" / "notion" / "holon_notion.py"
TUNNEL, PORT = "holon-notion", 8787


def run(cmd: list[str], env: dict, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    print("  $ " + " ".join(cmd), flush=True)
    out = subprocess.run(cmd, env=env, text=True, capture_output=True)
    for line in (out.stderr if quiet else out.stdout + out.stderr).strip().splitlines():
        print("    " + line)
    if check and out.returncode:
        raise SystemExit(f"failed ({out.returncode}): {' '.join(cmd)}")
    return out


def holon(env: dict, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run([sys.executable, "-m", "holon", *args], env, check)


def unit(name: str, home: Path, extra_env: dict) -> str:
    text = (ROOT / ("adapters/notion" if name == "holon-notion.service" else "adapters/systemd") / name).read_text()
    text = text.replace("Environment=HOLON_HOME=%h/holon", f"Environment=HOLON_HOME={home}")
    text = text.replace("<holon-checkout>", str(ROOT)).replace("/usr/bin/env holon ", f"{ROOT}/bin/holon ")
    env_lines = "".join(f"Environment={k}={v}\n" for k, v in extra_env.items())
    return text.replace("[Service]\n", "[Service]\n" + env_lines, 1)


def tunnel(cloudflared: str, hostname: str, cf_dir: Path, env: dict) -> Path:
    """The dedicated tunnel, its DNS route and its config; returns the config path. A tunnel of its own:
    a hostname added to a tunnel whose connectors run different configs 404s on some requests."""
    def tunnels() -> list:
        listed = run([cloudflared, "tunnel", "list", "--output", "json"], env, quiet=True).stdout
        return json.loads(listed or "[]") or []

    found = next((t for t in tunnels() if t.get("name") == TUNNEL), None)
    if found is None:
        run([cloudflared, "tunnel", "create", TUNNEL], env)
        found = next(t for t in tunnels() if t.get("name") == TUNNEL)
    creds = cf_dir / f"{found['id']}.json"
    if not creds.exists():
        raise SystemExit(f"tunnel {TUNNEL} exists but {creds} is not on this machine: copy it here, "
                         f"or `cloudflared tunnel delete {TUNNEL}` and run again")
    out = run([cloudflared, "tunnel", "route", "dns", TUNNEL, hostname], env, check=False)
    if out.returncode:
        raise SystemExit(f"could not route {hostname} to {TUNNEL}; if the name belongs to another tunnel, "
                         "pick another hostname or move the record in Cloudflare")
    config = cf_dir / f"{TUNNEL}.yml"
    config.write_text(f"""# written by holon install.py: Notion webhooks -> the holon-notion sync
tunnel: {found['id']}
credentials-file: {creds}
# IPv4 to the edge, like the sync itself: a host with a dead IPv6 route otherwise stalls every reconnect
edge-ip-version: "4"

ingress:
  - hostname: {hostname}
    path: ^/notion/
    service: http://localhost:{PORT}
  - service: http_status:404
""")
    print(f"  wrote {config}")
    return config


def reachable(url: str, tries: int = 12) -> bool:
    """The receiver answers an unsigned POST with 401; Cloudflare's own 404/5xx means not yet. A new
    DNS name can take a minute to resolve here (a resolver may have cached its absence)."""
    gai = socket.getaddrinfo  # IPv4 first, as the Notion adapter does: hosts with a dead IPv6 route hang
    socket.getaddrinfo = lambda *x, **kw: sorted(gai(*x, **kw), key=lambda r: r[0] != socket.AF_INET)
    for _ in range(tries):
        try:  # Cloudflare's browser integrity check refuses Python's default User-Agent (error 1010)
            urllib.request.urlopen(urllib.request.Request(url, data=b"{}", method="POST",
                                                          headers={"User-Agent": "holon-install/1"}), timeout=10)
        except urllib.error.HTTPError as e:
            e.close()
            if e.code == 401:
                return True
        except OSError:
            pass
        time.sleep(5)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=os.environ.get("HOLON_HOME", "~/holon"), help="the instance directory")
    ap.add_argument("--bin-dir", help="where the `holon` launcher goes (default: the first of "
                    "~/.local/bin or ~/bin that is on PATH)")
    ap.add_argument("--repoint", action="store_true", help="replace a launcher that belongs to "
                    "another checkout (the installer stops and names both otherwise)")
    ap.add_argument("--notion-parent-page", help="a Notion page shared with the integration")
    ap.add_argument("--notion-token-file", help="file holding the integration token (0600)")
    ap.add_argument("--notion-chair-user", action="append", default=[], help="Notion user id(s) of the Chair")
    ap.add_argument("--notion-webhook-hostname", help="serve Notion webhooks on https://<this>/notion/…")
    ap.add_argument("--cloudflared", default=shutil.which("cloudflared") or "cloudflared")
    ap.add_argument("--cloudflared-dir", default="~/.cloudflared")
    ap.add_argument("--notion-api-base", help=argparse.SUPPRESS)  # tests
    ap.add_argument("--systemd", action="store_true", help="write the user units; enable the Notion sync")
    ap.add_argument("--heartbeat", action="store_true", help="also enable holon-tick (starts dispatching work)")
    ap.add_argument("--no-notion", action="store_true", help="skip the Notion check --repair and push on an "
                    "instance that is already mirrored (the mirror keeps its configuration)")
    ap.add_argument("--systemd-dir", default="~/.config/systemd/user")
    ap.add_argument("--no-start", action="store_true", help="write the units without enabling them")
    a = ap.parse_args(argv)
    home, bin_dir = Path(a.home).expanduser().resolve(), Path(a.bin_dir or "").expanduser() if a.bin_dir else \
        next((d for d in (Path("~/.local/bin").expanduser(), Path("~/bin").expanduser())
             if d.is_dir() and str(d) in os.environ.get("PATH", "")), Path("~/bin").expanduser())
    path_dirs = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if str(bin_dir) not in path_dirs:
        print(f"  ⚠ {bin_dir} is not on PATH: shells will not find `holon` (add it, or pass --bin-dir)")

    print("1. prerequisites")
    if sys.version_info < (3, 11):
        raise SystemExit(f"Python 3.11+ is required (this is {sys.version.split()[0]})")
    if sqlite3.sqlite_version_info < (3, 35):
        raise SystemExit(f"SQLite 3.35+ is required (this Python has {sqlite3.sqlite_version})")
    print(f"  python {sys.version.split()[0]}, sqlite {sqlite3.sqlite_version}")

    env = {**os.environ, "HOLON_HOME": str(home), "PYTHONPATH": str(ROOT)}
    print(f"2. instance at {home}")
    home.mkdir(parents=True, exist_ok=True)
    holon(env, "init")

    print(f"3. launcher in {bin_dir}")
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / "holon"
    if launcher.is_symlink():
        target = Path(os.path.realpath(launcher))
        if ROOT not in target.parents and not a.repoint:
            raise SystemExit(f"{launcher} belongs to another checkout:\n  {launcher} -> {target}\n"
                             f"  this installer: {ROOT}\n"
                             "  pass --repoint to make this checkout the installation, or install "
                             "that checkout instead")
    if launcher.is_symlink() or not launcher.exists():
        launcher.unlink(missing_ok=True)
        launcher.symlink_to(ROOT / "bin" / "holon")
        print(f"  {launcher} -> {ROOT / 'bin' / 'holon'}")
    else:
        print(f"  {launcher} exists and is not a link; left alone")

    notion_cfg = home / "adapters" / "notion" / "notion.json"
    if a.notion_parent_page or (notion_cfg.exists() and not a.no_notion):
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
        webhook_path = run(base + ["webhook-path"], env).stdout.strip()
    else:
        print("4. Notion mirror: skipped (no --notion-parent-page)")

    tunnel_config, webhook_url = None, None
    if a.notion_webhook_hostname and notion_cfg.exists():
        print(f"5. webhook tunnel for {a.notion_webhook_hostname}")
        tunnel_config = tunnel(a.cloudflared, a.notion_webhook_hostname, Path(a.cloudflared_dir).expanduser(), env)
        webhook_url = f"https://{a.notion_webhook_hostname}{webhook_path}"
    else:
        print("5. webhook tunnel: skipped (no --notion-webhook-hostname; the sync polls Notion every minute)")

    if a.systemd:
        print("6. systemd user units")
        dest = Path(a.systemd_dir).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        isolated = bool(tomllib.loads((home / "holon.toml").read_text()).get("isolation", {}).get("agent_user")) \
            if (home / "holon.toml").exists() else False
        names = ["holon-tick.service", "holon-tick.timer"] + (["holon-notion.service"] if notion_cfg.exists() else []) \
            + (["holon-broker.service"] if isolated else [])
        for name in names:
            (dest / name).write_text(unit(name, home, {}))
            print(f"  wrote {dest / name}")
        if tunnel_config:
            (dest / f"cloudflared-{TUNNEL}.service").write_text(f"""[Unit]
Description=Cloudflare tunnel for Holon's Notion webhooks (written by holon install.py)
After=network-online.target holon-notion.service
Wants=network-online.target

[Service]
ExecStart={a.cloudflared} --no-autoupdate --config {tunnel_config} tunnel run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
""")
            names.append(f"cloudflared-{TUNNEL}.service")
            print(f"  wrote {dest / names[-1]}")
        if not a.no_start:
            start = (["holon-tick.timer"] if a.heartbeat else []) + [n for n in names if "notion" in n or "broker" in n]
            run(["systemctl", "--user", "daemon-reload"], env)
            if start:
                run(["systemctl", "--user", "enable", *start], env)
                run(["systemctl", "--user", "restart", *[n for n in start if n.endswith(".service")]], env)
                if "holon-tick.timer" in start:
                    run(["systemctl", "--user", "start", "holon-tick.timer"], env)
            if not a.heartbeat:
                print("  heartbeat written, not started: systemctl --user enable --now holon-tick.timer")
            if webhook_url:
                print(f"  webhook URL answers: {reachable(webhook_url)}")
    else:
        print("6. systemd: skipped (--systemd writes the units)")

    print("7. doctor")
    holon(env, "doctor", check=False)
    print(f"""
Onboarding runbook — instance ready at {home} (export HOLON_HOME={home}).

1. A body: install one harness adapter (all four are public; docs/CONTRACT.md §7 for any other)
     python3 {ROOT}/adapters/zeroclaw/install.py --holon-home {home} --allow <a work directory>
     python3 {ROOT}/adapters/jcode/install.py --holon-home {home} \\
         --base-url <model gateway>/v1 --model <model> --api-key-file <key file>
     python3 {ROOT}/adapters/hermes/install.py --holon-home {home} …    # same flags as jcode
     python3 {ROOT}/adapters/operant/install.py --holon-home {home} …   # same flags as jcode
     holon adapter test <name>          # conformance: a few real runs in a scratch organization
   Any OpenAI-compatible endpoint works as the model gateway; keys go in files, never arguments.
2. The organization (docs/BYLAWS.md has the full command reference):
     holon hr hire --dept executive --slot ceo --name <name> --title CEO --driver <harness> \\
         --jd @<file> --reason "founding CEO" --status active
     holon dept create <slug> --name "<Name>" --mission "<mission>" --profile light
     holon hr hire --dept <slug> --slot lead --name <name> --title "Head of <Name>" \\
         --driver <harness> --jd @<file> --reason "founding head"
3. First work:
     holon task add "<what must be true>" --intent "<why>" --dept <slug>
4. Run it: holon tick    (with --systemd: systemctl --user enable --now holon-tick.timer)
5. Steer it: holon status shows your queue; approve with holon plan approve <id> --note "…",
   or reject with what to change. holon metrics --dept <slug> is the feedback; holon lint &&
   holon doctor are the health checks. Every command: holon help
6. Optional hardening: holon isolation plan — agents as their own OS user through a broker
   (the doctor warns until it is done).""")
    if webhook_url:
        print(f"""
Notion webhooks: in the integration's Webhooks tab, subscribe
  {webhook_url}
to page and data source events, then paste the verification token holon-notion saved (field
webhook_token in {notion_cfg}) into Notion's Verify dialog. Resend is fine: the newest token
wins. Keep the URL private: its path is the secret.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
