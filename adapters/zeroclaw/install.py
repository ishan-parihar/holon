#!/usr/bin/env python3
"""Install the Holon adapter into ZeroClaw (idempotent, reversible).

    python3 adapters/zeroclaw/install.py --holon-home ~/holon --allow ~/src/repo [--model free-stack] [--dry-run]

1. exports Holon's skills library to $HOLON_HOME/adapters/zeroclaw/skills (`holon skill export`),
   which ZeroClaw loads as the `holon` skill bundle; re-run after tailoring skills
2. backs up the ZeroClaw config, writes the Holon block (two posture aliases, two risk profiles, a
   runtime profile, the skill bundle) between markers, re-parses it, and restores the backup if invalid
3. installs the driver to $HOLON_HOME/config/drivers/zeroclaw.toml
4. checks that ZeroClaw sees both aliases

Agents may read and write only the run directories and the --allow paths; the instance's
databases, configuration, skills and genome are forbidden paths, and ~/.config (the Chair key) too.
Uninstall: restore the printed backup (or delete the marked block) and remove the driver file.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BEGIN = "# ── Holon (agentic OS) employees"
END = "# ── end Holon ──"
ALIASES = ("holon-observe", "holon-act")
SYSTEM_FORBIDDEN = ["/etc", "/root", "/usr", "/sbin", "/boot", "/dev", "/sys", "/var/run", "~/.ssh", "~/.gnupg",
                    "~/.aws", "~/.config", "/proc/kcore"]


def sh(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=cwd)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def strip_block(text: str) -> str:
    if BEGIN not in text:
        return text
    start = text.index(BEGIN)
    end = text.index(END, start) + len(END)
    return text[:start].rstrip() + "\n" + text[end:].lstrip("\n")


def toml_list(items: list[str]) -> str:
    return ", ".join(f'"{x}"' for x in items)


def set_notify(home: Path, config_dir: Path | None, channel: str, recipient: str, force: bool) -> str:
    """Point Holon's [notify] hook at `zeroclaw channel send` (holon.toml; kept unless --force)."""
    toml = home / "holon.toml"
    text = toml.read_text() if toml.exists() else ""
    cd = f"--config-dir {config_dir} " if config_dir else ""
    line = f'command = "zeroclaw {cd}channel send {{message}} --channel-id {channel} --recipient {recipient}"'
    if "[notify]" in text and not force:
        return f"{toml} already has [notify] (use --force to replace it)"
    if "[notify]" in text:
        start = text.index("[notify]")
        end = text.find("\n[", start + 1)
        text = text[:start] + text[end + 1:] if end != -1 else text[:start]
    toml.write_text(text.rstrip() + f"\n\n[notify]\n{line}\n")
    tomllib.loads(toml.read_text())
    return f"the Chair is told through {channel} ({toml})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holon-home", default=str(Path("~/holon").expanduser()))
    ap.add_argument("--config-dir", help="a separate ZeroClaw config directory for Holon (default: ~/.zeroclaw)")
    ap.add_argument("--model-provider", help="default: the provider of [agents.assistant] (or the first agent)")
    ap.add_argument("--model", help="give Holon its own provider entry, cloned from --model-provider, pinned to this "
                                    "model (e.g. an aroute combo such as free-stack)")
    ap.add_argument("--fallback-model", action="append", default=[], help="fallback models for --model (repeat)")
    ap.add_argument("--allow", action="append", default=[], help="a directory employees work in (repeat)")
    ap.add_argument("--notify-channel", help="ZeroClaw channel that tells the Chair what needs them (e.g. telegram)")
    ap.add_argument("--notify-recipient", help="the Chair's id on that channel (e.g. a Telegram chat id)")
    ap.add_argument("--force", action="store_true", help="replace an existing Holon block")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    home = Path(a.holon_home).expanduser().resolve()
    config_dir = Path(a.config_dir).expanduser() if a.config_dir else Path("~/.zeroclaw").expanduser()
    cfg_path = config_dir / "config.toml"
    if not cfg_path.exists():
        print(f"no ZeroClaw config at {cfg_path}", file=sys.stderr)
        return 2
    text = cfg_path.read_text()
    cfg = tomllib.loads(text)
    agents = cfg.get("agents", {})
    provider = a.model_provider or (agents.get("assistant") or next(iter(agents.values()), {})).get("model_provider")
    if not provider:
        print("could not infer a model provider; pass --model-provider", file=sys.stderr)
        return 2

    runs = home / "execution" / "runtime" / "runs"
    roots = [str(runs)] + [str(Path(p).expanduser().resolve()) for p in a.allow]
    instance_state = [str(home / p) for p in ("execution/runtime/work.db", "capabilities", "holon.toml", "config",
                                             "skills", "genome", "adapters")]
    skills_dir = home / "adapters" / "zeroclaw" / "skills"
    provider_block = ""
    if a.model:
        kind, _, alias = provider.partition(".")
        base = cfg.get("providers", {}).get("models", {}).get(kind, {}).get(alias)
        if not base:
            print(f"no provider {provider} to clone for --model", file=sys.stderr)
            return 2
        lines = [f"[providers.models.{kind}.holon]", f'model = "{a.model}"',
                 f"fallback_models = [{toml_list(a.fallback_model)}]"]
        for key in ("uri", "api_key", "timeout_secs", "context_window"):
            if key in base:
                v = base[key]
                lines.append(f'{key} = "{v}"' if isinstance(v, str) else f"{key} = {v}")
        provider_block = "\n".join(lines) + "\n\n"
        provider = f"{kind}.holon"
    block = (HERE / "config.snippet.toml").read_text() \
        .replace("@PROVIDER_BLOCK@", provider_block).replace("@MODEL_PROVIDER@", provider) \
        .replace("@ALLOWED_ROOTS@", toml_list(roots)) \
        .replace("@FORBIDDEN_PATHS@", toml_list(SYSTEM_FORBIDDEN + instance_state)) \
        .replace("@SKILLS_DIR@", str(skills_dir)).replace("@HOLON_BIN@", str(REPO / "bin" / "holon"))

    legacy = [x for x in ("holon-plan", "holon-execute", "holon-verify", "holon-review") if x in agents]
    present = BEGIN in text
    if present and not a.force:
        print("config: a Holon block is already present (use --force to replace it"
              + (f"; it is the pre-graph version with aliases {legacy}" if legacy else "") + ")")
        new_text = None
    else:
        new_text = strip_block(text).rstrip() + "\n" + block
        tomllib.loads(new_text)  # never write invalid TOML
    print(f"config: {cfg_path} provider={provider}\n  allowed_roots={roots}\n  skills={skills_dir}")
    if a.dry_run:
        shown = re.sub(r'^((?:api_key|token|secret)\s*=\s*)".*"$', r'\1"<redacted>"', block, flags=re.M)
        print("dry run: no changes written\n" + (shown if new_text else ""))
        return 0

    holon = [sys.executable, "-m", "holon", "--home", str(home)]
    rc, out = sh(holon + ["skill", "export", "--to", str(skills_dir)], cwd=str(REPO))
    print(f"skills: {'exported' if rc == 0 else 'export FAILED — ' + out[-300:]}")
    if rc != 0:
        return 1
    if new_text is not None:
        backup = cfg_path.with_name(f"{cfg_path.name}.bak.holon.{int(time.time())}")
        shutil.copy2(cfg_path, backup)
        cfg_path.write_text(new_text)
        try:
            tomllib.loads(cfg_path.read_text())
        except tomllib.TOMLDecodeError:
            shutil.copy2(backup, cfg_path)
            print("config: the write produced invalid TOML; backup restored", file=sys.stderr)
            return 1
        print(f"config: Holon block written (backup: {backup})")

    if a.notify_channel and a.notify_recipient:
        print("notify: " + set_notify(home, config_dir if a.config_dir else None, a.notify_channel,
                                      a.notify_recipient, a.force))

    drivers_dir = home / "config" / "drivers"
    drivers_dir.mkdir(parents=True, exist_ok=True)
    driver = (HERE / "driver.toml").read_text()
    if a.config_dir:
        driver = driver.replace("zeroclaw agent", f"zeroclaw --config-dir {config_dir} agent")
    (drivers_dir / "zeroclaw.toml").write_text(driver)
    print(f"driver: {drivers_dir / 'zeroclaw.toml'}")

    base = ["zeroclaw"] + (["--config-dir", str(config_dir)] if a.config_dir else [])
    rc, out = sh(base + ["agents", "list"])
    missing = [x for x in ALIASES if x not in out]
    print("verify: " + ("both holon aliases are visible to zeroclaw" if rc == 0 and not missing
                        else f"zeroclaw agents list rc={rc}, missing={missing}: {out[-300:]}"))
    print("next: `holon adapter test zeroclaw` runs the conformance suite against this harness")
    return 0 if rc == 0 and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
