"""Drivers: how a harness is invoked. One TOML file per harness; the kernel knows none by name.

    name      = "my-harness"
    command   = "my-agent --prompt {prompt} --json"   # placeholders are shell-quoted
    cwd       = "{workdir}"            # default
    timeout_s = 1800
    result    = "file"                 # "file": agent writes {result_path}; "stdout-json": last JSON object on stdout
    read_only = "--plan-only"   # substituted as {read_only} for observe-posture stages, else ""
    act_args  = "--yes"         # substituted as {act_args} for the execute stage (act posture), else ""
    stages    = ["plan", "execute"]    # optional restriction (default: every stage)
    read_only_enforced = false   # true if the harness itself denies writes on observe stages (an
                                 # observe alias/profile); the kernel's workdir guard still runs unless
                                 # the instance sets [planner] trust_harness_read_only = true
    [env]                              # optional extra environment
    FOO = "bar"

Placeholders: {brief_dir} {brief_md} {context_md} {identity_md} {result_path} {workdir} {run_id}
{task_id} {stage} {role} {posture} (observe | act) {employee_id} {employee_slug} {read_only}
{prompt} (= the BRIEF.md text) {identity} (= the identity card) {briefing} (= identity card + BRIEF.md,
for harnesses that take one message) {spawn} (= a one-line spawn message pointing at BRIEF.md)
{act_args}
{holon} (= kernel command) {scope} (= the department's scope name: its slug if holon.toml has a
[scope.dept.<slug>] section, else "org"; adapters that sandbox build one sandbox per scope). `duty` is accepted as an alias of `stage`.
Search order: $HOLON_HOME/config/drivers/*.toml, then the examples shipped with the kernel.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from holon.core import vocab
from holon.core.paths import Home
from holon.execution.briefs import kernel_env

LEGACY_STAGE = {"review": "approve"}


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
    act_args: str = ""
    read_only_enforced: bool = False   # the harness itself denies writes on observe stages
    scope_fingerprint: str = ""        # set by an installer that built sandboxes from [scope]; doctor compares
    stages: list[str] = field(default_factory=lambda: list(vocab.STAGES))
    env: dict[str, str] = field(default_factory=dict)
    source: str = ""

    @classmethod
    def from_toml(cls, text: str, source: str) -> "Driver":
        data = tomllib.loads(text)
        unknown = set(data) - {"name", "command", "cwd", "timeout_s", "result", "read_only", "act_args",
                               "read_only_enforced", "stages", "duties", "env", "scope_fingerprint"}
        if "duties" in data:  # pre-graph drivers named four duties; observe-capable ones serve every observe stage
            stages = {LEGACY_STAGE.get(d, d) for d in data.pop("duties")}
            data["stages"] = sorted(stages | ({"research", "shape", "retro"} if "plan" in stages else set()))
        bad = set(data.get("stages", [])) - set(vocab.STAGES)
        if bad:
            raise DriverError(f"{source}: unknown stages {sorted(bad)}")
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


def spawn_message(brief: dict) -> str:
    return (f"You are {brief.get('employee_name') or 'a Holon employee'} ({brief.get('employee_id') or 'unaffiliated'}). "
            f"Load the holon-employee skill, then do the {brief['stage']} stage described in {brief['brief_md']}.")


def render(driver: Driver, brief: dict) -> tuple[str, str]:
    stage = brief["stage"]
    values = {
        "brief_dir": str(Path(brief["brief_md"]).parent), "brief_md": brief["brief_md"],
        "context_md": brief["context_md"], "identity_md": brief.get("identity_md") or "",
        "result_path": brief["result_path"], "workdir": brief["workdir"], "run_id": brief["run_id"],
        "task_id": brief["task_id"], "stage": stage, "duty": stage, "role": vocab.STAGE_ROLE[stage],
        "posture": vocab.POSTURE[stage], "employee_id": brief.get("employee_id") or "",
        "employee_slug": brief.get("employee_slug") or "", "holon": brief["holon"],
        "prompt": Path(brief["brief_md"]).read_text(), "spawn": spawn_message(brief),
        "scope": (brief.get("scope") or {}).get("name") or "org",
    }
    values["identity"] = Path(brief["identity_md"]).read_text() if brief.get("identity_md") else ""
    values["briefing"] = (values["identity"] + "\n\n---\n\n" if values["identity"] else "") + values["prompt"]
    quoted = {k: shlex.quote(v) for k, v in values.items()}
    quoted["holon"] = values["holon"]  # already a shell-ready command
    for k in ("stage", "duty", "role", "posture", "employee_slug", "scope"):
        quoted[k] = values[k]  # identifiers: safe, and used inside larger tokens (e.g. alias names)
    quoted["read_only"] = driver.read_only if vocab.POSTURE[stage] == "observe" else ""
    quoted["act_args"] = driver.act_args if vocab.POSTURE[stage] == "act" else ""
    try:
        cmd = driver.command.format(**quoted)
        cwd = driver.cwd.format(**values)
    except KeyError as e:
        raise DriverError(f"driver {driver.name}: unknown placeholder {e}") from None
    return cmd, cwd


def run_env(home: Home, brief: dict, token: str | None) -> dict[str, str]:
    """The environment every push-mode run gets (docs/CONTRACT.md §1 lists it; `holon lint` checks that)."""
    stage = brief["stage"]
    env = {**kernel_env(home), "HOLON_RUN_ID": brief["run_id"], "HOLON_TASK_ID": brief["task_id"],
           "HOLON_STAGE": stage, "HOLON_ROLE": vocab.STAGE_ROLE[stage], "HOLON_POSTURE": vocab.POSTURE[stage],
           "HOLON_EMPLOYEE_ID": brief.get("employee_id") or "", "HOLON_BRIEF_DIR": str(Path(brief["brief_md"]).parent),
           "HOLON_RESULT_PATH": brief["result_path"]}
    if token:
        env["HOLON_RUN_TOKEN"] = token
    return env


def run(home: Home, driver: Driver, brief: dict, token: str | None = None, alive=None,
        alive_every_s: float = 60.0) -> dict:
    """Invoke the harness and return {'exit_code', 'envelope' | None, 'log_path', 'stdout_tail'}.
    `alive()` is called every `alive_every_s` while the harness process runs (the kernel renews the
    run's lease with it), so a lease expires only when the dispatcher itself has died."""
    if brief["stage"] not in driver.stages:
        raise DriverError(f"driver {driver.name} does not serve the '{brief['stage']}' stage")
    cmd, cwd = render(driver, brief)
    if not Path(cwd).is_dir():
        cwd = str(Path(brief["brief_md"]).parent)
    clean = {k: v for k, v in os.environ.items() if k not in ("HOLON_CHAIR_KEY", "HOLON_KEY")}
    env = {**clean, **run_env(home, brief, token), **driver.env}
    log_path = Path(brief["brief_md"]).parent / "driver.log"
    proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    deadline = time.monotonic() + driver.timeout_s
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=max(0.1, min(alive_every_s, deadline - time.monotonic())))
            exit_code = proc.returncode
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                os.killpg(proc.pid, signal.SIGKILL)  # the shell and the harness it started
                stdout, _ = proc.communicate()
                exit_code, stderr = 124, f"driver timed out after {driver.timeout_s}s"
                break
            if alive:
                alive()
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


def extract_last_json(text: str) -> dict | None:
    """Best effort for harnesses that print instead of writing result.json: the last top-level JSON
    object in the text."""
    decoder = json.JSONDecoder()
    found, i = None, 0
    while True:
        start = text.find("{", i)
        if start < 0:
            return found
        try:
            obj, end = decoder.raw_decode(text, start)
            if isinstance(obj, dict):
                found = obj
            i = end
        except json.JSONDecodeError:
            i = start + 1
