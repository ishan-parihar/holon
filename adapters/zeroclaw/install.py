#!/usr/bin/env python3
"""Install the Holon plugin into ZeroClaw (idempotent, reversible).

    python3 adapters/zeroclaw/install.py --holon-home ~/holon --allow ~/.hermes/organism [--dry-run]

1. backs up ~/.zeroclaw/config.toml, appends the holon-* agents and risk profiles between
   markers (or replaces them with --force), re-parses the TOML, and restores the backup if invalid
2. installs the driver to $HOLON_HOME/config/drivers/zeroclaw.toml
3. exports the Holon skills library (`holon skill export`) and installs each skill with `zeroclaw skills install`
4. verifies the agent aliases are visible to `zeroclaw agents list`
Uninstall: restore the printed backup (or delete the marked block) and remove the driver file.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
BEGIN = "# ── Holon (agentic OS) employees"
END = "# ── end Holon ──"


def sh(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=cwd)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def strip_block(text: str) -> str:
    if BEGIN not in text:
        return text
    start = text.index(BEGIN)
    end = text.index(END, start) + len(END)
    return text[:start].rstrip() + "\n" + text[end:].lstrip("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holon-home", default=str(Path("~/holon").expanduser()))
    ap.add_argument("--zeroclaw-config", default=str(Path("~/.zeroclaw/config.toml").expanduser()))
    ap.add_argument("--model-provider", help="default: the provider of [agents.assistant] (or the first agent)")
    ap.add_argument("--model", help="give Holon its own provider entry, cloned from --model-provider, pinned to this "
                                    "model (e.g. an aroute combo such as free-stack)")
    ap.add_argument("--fallback-model", action="append", default=[], help="fallback models for --model (repeat)")
    ap.add_argument("--allow", action="append", default=[], help="extra directory employees may work in (repeat)")
    ap.add_argument("--force", action="store_true", help="replace an existing Holon block")
    ap.add_argument("--skip-skill", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    home = Path(a.holon_home).expanduser().resolve()
    cfg_path = Path(a.zeroclaw_config).expanduser()
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
    roots = [str(home)] + [str(Path(p).expanduser().resolve()) for p in a.allow]
    provider_block = ""
    if a.model:
        kind, _, alias = provider.partition(".")
        base = cfg.get("providers", {}).get("models", {}).get(kind, {}).get(alias)
        if not base:
            print(f"no provider {provider} to clone for --model", file=sys.stderr)
            return 2
        lines = [f"[providers.models.{kind}.holon]", f'model = "{a.model}"',
                 "fallback_models = [" + ", ".join(f'"{m}"' for m in a.fallback_model) + "]"]
        for key in ("uri", "api_key", "timeout_secs", "context_window"):
            if key in base:
                v = base[key]
                lines.append(f'{key} = "{v}"' if isinstance(v, str) else f"{key} = {v}")
        provider_block = "\n".join(lines) + "\n\n"
        provider = f"{kind}.holon"
    block = (HERE / "config.snippet.toml").read_text() \
        .replace("@PROVIDER_BLOCK@", provider_block) \
        .replace("@MODEL_PROVIDER@", provider) \
        .replace("@ALLOWED_ROOTS@", ", ".join(f'"{r}"' for r in roots))

    present = "holon-plan" in agents
    if present and not a.force:
        print("config: Holon block already present (use --force to replace)")
        new_text = None
    else:
        new_text = strip_block(text).rstrip() + "\n" + block
        tomllib.loads(new_text)  # never write invalid TOML
    print(f"config: provider={provider} allowed_roots={roots}")

    if a.dry_run:
        print("dry run: no changes written")
        print(block if new_text else "")
        return 0

    if new_text is not None:
        backup = cfg_path.with_name(f"{cfg_path.name}.bak.holon.{int(time.time())}")
        shutil.copy2(cfg_path, backup)
        cfg_path.write_text(new_text)
        try:
            tomllib.loads(cfg_path.read_text())
        except tomllib.TOMLDecodeError:
            shutil.copy2(backup, cfg_path)
            print("config: write produced invalid TOML; restored backup", file=sys.stderr)
            return 1
        print(f"config: Holon block written (backup: {backup})")

    drivers_dir = home / "config" / "drivers"
    drivers_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / "driver.toml", drivers_dir / "zeroclaw.toml")
    print(f"driver: {drivers_dir / 'zeroclaw.toml'}")

    if not a.skip_skill:
        # the skills library is exported from the kernel (resolved layers, rendered for this instance),
        # so ZeroClaw loads exactly what `holon skill show` serves to any other harness
        export = home / "exported-skills"
        rc, out = sh([sys.executable, "-m", "holon", "--home", str(home), "skill", "export", "--to", str(export)],
                     cwd=str(HERE.parents[1]))
        if rc != 0:
            print(f"skills: export failed — {out[-300:]}")
        for d in sorted(p for p in export.iterdir() if (p / "SKILL.md").exists()) if rc == 0 else []:
            rc2, out2 = sh(["zeroclaw", "skills", "install", str(d)])
            print(f"skill {d.name}: {'installed' if rc2 == 0 else 'NOT installed — ' + out2[-200:]}")

    rc, out = sh(["zeroclaw", "agents", "list"])
    missing = [x for x in ("holon-plan", "holon-execute", "holon-verify", "holon-review") if x not in out]
    print("verify: " + ("all holon-* agents visible to zeroclaw" if rc == 0 and not missing
                        else f"zeroclaw agents list rc={rc}, missing={missing}: {out[-300:]}"))
    return 0 if rc == 0 and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
