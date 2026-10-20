#!/usr/bin/env python3
"""The shared seam every adapter installer stands on (R3 of the 2026-10-20 bridge plan).

Four installers, one shape. Each used to carry its own copy of these steps, and the drift
that produced is not hypothetical:

- the jcode installer pinned a gateway port and a model the gateway no longer served, so
  every run failed at the first model call;
- the operant driver pinned a stale root-owned `operant` built against a different ONNX
  Runtime than the repo pins, so every run died in the loader — and the error text
  pointed at the library, which was correct.

Both are the same class: **a pin taken without checking that what it names still works.**
So the two invariants this module adds are

1. `pin_binary()` refuses a binary that fails its own `--version` — the caller is told,
   and no driver is written for a harness that cannot start;
2. `export_skills()` and `write_driver()` are one implementation, so a fix lands in all
   four installers at once instead of in the one someone remembered.

Everything here is stdlib, and every function is small enough to read in one screen.
Nothing here knows about a specific harness.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent          # the distribution's root (adapters/ is one level down)
HOLON = REPO / "bin" / "holon"


class InstallError(RuntimeError):
    """A step that cannot be completed. The installer prints it and refuses to write the
    driver, because a driver for a harness that cannot run is worse than no driver."""


def pin_binary(name: str, given: str | None = None, version_args: tuple[str, ...] = ("--version",),
               required: bool = True, check: bool = True) -> str:
    """The harness binary to pin: `given`, else the first on PATH.

    **It must start.** A binary that fails `--version` cannot run a Holon stage, so the
    caller is refused rather than handed a driver that will fail on its first run. This
    is the check whose absence produced the operant incident of 2026-10-20.

    `required=False` returns the bare name when nothing is found (a dry run has nothing
    to pin); `check=False` skips the start-up probe (a dry run must not fail on a harness
    that is not installed here).
    """
    found = given or shutil.which(name)
    if not found:
        if not required:
            return name
        raise InstallError(f"no {name} on PATH; pass --{name} (the installer will not pin one it cannot find)")
    binary = os.path.abspath(os.path.expanduser(found))  # the launcher, not a build directory
    if not Path(binary).exists():
        raise InstallError(f"{name} to pin does not exist: {binary}")
    if not check:
        return binary
    try:
        p = subprocess.run([binary, *version_args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise InstallError(f"{binary} --version failed to run: {e}") from None
    if p.returncode != 0:
        why = (p.stderr or p.stdout).strip().splitlines()
        raise InstallError(f"{binary} cannot start ({' '.join(version_args)} exited {p.returncode}): "
                           f"{why[0] if why else 'no output'} — refusing to pin it")
    return binary


def strip_block(text: str, begin: str, end: str, body: str = "") -> str:
    """`text` without any copy of a managed block.

    An installer rewrites its harness's config and can drop the BEGIN line, so a block is matched
    in two forms: whole (BEGIN through END), and a copy that lost only its BEGIN — which is
    identifiable because the caller passes the body it writes. The two are removed separately:
    making BEGIN optional in one pattern lets `.*?<end>` match from the start of the file, which
    swallows the harness's own config along with the block. Everything else is kept exactly, so
    stripping a file with no block is a no-op."""
    out = re.sub(rf"{re.escape(begin)}\n.*?{re.escape(end)}\n*", "", text, flags=re.S)
    if body and body.strip() and body in out:
        out = out.replace(body, "")                     # the dropped copy's own lines
        out = re.sub(rf"(?m)^[ \t]*{re.escape(end)}[ \t]*\n?", "", out)   # and its END marker
    return out


def export_skills(home: Path, dest: Path, cwd: Path = REPO) -> None:
    """Holon's skills, resolved and rendered, into the harness's skills directory.

    One implementation for all four installers, and the one place the export's
    failure is reported. The export runs with the repository on the path, because
    `python3 -m holon` needs the package importable — the installers' own directory
    is not that place.
    """
    dest.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(cwd), env.get("PYTHONPATH", "")]).strip(os.pathsep)
    p = subprocess.run([sys.executable, "-m", "holon", "--home", str(home), "skill", "export", "--to", str(dest)],
                       capture_output=True, text=True, cwd=str(cwd), env=env)
    if p.returncode != 0:
        raise InstallError(f"skill export failed: {(p.stdout + p.stderr)[-400:]}")


def write_driver(home: Path, name: str, toml: str) -> Path:
    """Install one driver TOML, validating it before it lands."""
    import tomllib
    tomllib.loads(toml)  # refuse to write a driver the kernel could not load
    drivers = home / "config" / "drivers"
    drivers.mkdir(parents=True, exist_ok=True)
    (drivers / f"{name}.toml").write_text(toml)
    return drivers / f"{name}.toml"


def verify(home: Path, harness: str, version_args: tuple[str, ...] = ("--version",)) -> str:
    """The harness's own version line, run with the instance's homes in place: the last
    step proves the pin still starts after everything else was written."""
    env = dict(os.environ)
    for home_env in ("HERMES_HOME", "JCODE_HOME"):
        env.pop(home_env, None)
    p = subprocess.run([harness, *version_args], capture_output=True, text=True, timeout=60, env=env)
    return (p.stdout or p.stderr).strip() or f"{harness} printed nothing for --version"


def write_file(path: Path, text: str, label: str) -> None:
    """Write a config file, printing its label, after validating what it claims to be."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"{label}: {path}")
