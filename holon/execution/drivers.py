"""Drivers: how a harness is invoked. One TOML file per harness; the kernel knows none by name.

    name      = "my-harness"
    command   = "my-agent --prompt {prompt} --json"   # placeholders are shell-quoted
    cwd       = "{workdir}"            # default
    timeout_s = 1800
    result    = "file"                 # "file": agent writes {result_path}; "stdout-json": last JSON object on stdout
    read_only = "--plan-only"   # substituted as {read_only} for plan/verify/review runs, else ""
    duties    = ["plan", "execute", "verify", "review"]   # optional restriction
    [env]                              # optional extra environment
    FOO = "bar"

Placeholders: {brief_dir} {brief_md} {context_md} {result_path} {workdir} {run_id}
{task_id} {duty} {role} {read_only} {prompt} (= the BRIEF.md text) {holon} (= kernel command).
Search order: $HOLON_HOME/config/drivers/*.toml, then the examples shipped with the kernel.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from holon.core.paths import Home
from holon.execution.envelopes import extract_last_json
from holon.execution.briefs import ROLE_OF_KIND, kernel_env


class DriverError(RuntimeError):
    pass


@dataclass
class Driver:
    name: str
    command: str
    cwd: str = "{workdir}"
    timeout_s: int = 3600
    result: str = "file"
    read_only: str = ""
    duties: list[str] = field(default_factory=lambda: ["plan", "execute", "verify", "review"])
    env: dict[str, str] = field(default_factory=dict)
    source: str = ""

    @classmethod
    def from_toml(cls, text: str, source: str) -> "Driver":
        data = tomllib.loads(text)
        unknown = set(data) - {"name", "command", "cwd", "timeout_s", "result", "read_only", "duties", "env"}
        if unknown:
            raise DriverError(f"{source}: unknown keys {sorted(unknown)}")
        if not data.get("name") or not data.get("command"):
            raise DriverError(f"{source}: 'name' and 'command' are required")
        if data.get("result", "file") not in ("file", "stdout-json"):
            raise DriverError(f"{source}: result must be 'file' or 'stdout-json'")
        return cls(**data, source=source)


def available(home: Home) -> dict[str, Driver]:
    found: dict[str, Driver] = {}
    builtin = resources.files("holon.execution").joinpath("drivers")
    for f in sorted(p for p in builtin.iterdir() if p.name.endswith(".toml")):
        d = Driver.from_toml(f.read_text(), f"builtin:{f.name}")
        found[d.name] = d
    if home.drivers_dir.exists():
        for f in sorted(home.drivers_dir.glob("*.toml")):
            d = Driver.from_toml(f.read_text(), str(f))
            found[d.name] = d  # instance drivers override examples
    return found


def get(home: Home, name: str) -> Driver:
    drivers = available(home)
    if name not in drivers:
        raise DriverError(f"no driver '{name}' (have: {', '.join(sorted(drivers)) or 'none'})")
    return drivers[name]


def render(driver: Driver, brief: dict, kind: str) -> tuple[str, str]:
    role = ROLE_OF_KIND[kind]
    values = {
        "brief_dir": str(Path(brief["brief_md"]).parent), "brief_md": brief["brief_md"],
        "context_md": brief["context_md"], "result_path": brief["result_path"], "workdir": brief["workdir"],
        "run_id": brief["run_id"], "task_id": brief["task_id"], "role": role, "duty": kind, "holon": brief["holon"],
        "prompt": Path(brief["brief_md"]).read_text(),
    }
    quoted = {k: shlex.quote(v) for k, v in values.items()}
    quoted["holon"] = values["holon"]  # already a shell-ready command
    quoted["read_only"] = driver.read_only if kind in ("plan", "verify", "review") else ""
    try:
        cmd = driver.command.format(**quoted)
        cwd = driver.cwd.format(**values)
    except KeyError as e:
        raise DriverError(f"driver {driver.name}: unknown placeholder {e}") from None
    return cmd, cwd


def run(home: Home, driver: Driver, brief: dict, kind: str) -> dict:
    """Invoke the harness and return {'exit_code', 'envelope' | None, 'log_path', 'stdout_tail'}."""
    if kind not in driver.duties:
        raise DriverError(f"driver {driver.name} does not serve the '{kind}' duty")
    cmd, cwd = render(driver, brief, kind)
    if not Path(cwd).is_dir():
        cwd = str(Path(brief["brief_md"]).parent)
    env = {**os.environ, **kernel_env(home), "HOLON_RUN_ID": brief["run_id"], "HOLON_TASK_ID": brief["task_id"],
           "HOLON_DUTY": kind, "HOLON_ROLE": ROLE_OF_KIND[kind], "HOLON_BRIEF_DIR": str(Path(brief["brief_md"]).parent),
           "HOLON_RESULT_PATH": brief["result_path"], **driver.env}
    log_path = Path(brief["brief_md"]).parent / "driver.log"
    try:
        proc = subprocess.run(cmd, shell=True, cwd=cwd, env=env, capture_output=True, text=True,
                              timeout=driver.timeout_s)
        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        exit_code = 124
        stdout = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = f"driver timed out after {driver.timeout_s}s"
    log_path.write_text(f"$ {cmd}\n[cwd] {cwd}\n[exit] {exit_code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n")
    envelope = None
    result_path = Path(brief["result_path"])
    if result_path.exists():
        try:
            envelope = json.loads(result_path.read_text())
        except json.JSONDecodeError:
            envelope = None
    if envelope is None and driver.result == "stdout-json":
        envelope = extract_last_json(stdout)
        if isinstance(envelope, dict) and isinstance(envelope.get("result"), str):
            # harnesses that wrap the model reply in {"result": "..."}: look inside it
            envelope = extract_last_json(envelope["result"]) or envelope
    return {"exit_code": exit_code, "envelope": envelope, "log_path": str(log_path), "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-2000:]}
