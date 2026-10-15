"""Drivers: how a harness is invoked. One TOML file per harness; the kernel knows none by name.

    name      = "my-harness"
    command   = "my-agent --prompt {prompt} --json"   # placeholders are shell-quoted
    cwd       = "{workdir}"            # default
    timeout_s = 1800
    result    = "file"                 # "file": agent writes {result_path}; "stdout-json": last JSON object on stdout
    read_only = "--plan-only"   # substituted as {read_only} for observe-posture stages, else ""
    act_args  = "--yes"         # substituted as {act_args} for the execute stage (act posture), else ""
    stages    = ["plan", "execute"]    # optional restriction (default: every stage)
    usage_line = 'in=(?P<tokens_in>[0-9]+) out=(?P<tokens_out>[0-9]+)'   # optional: a line the harness
                                 # writes per model call; their sum is the run's usage, even when the
                                 # harness is stopped before it prints a summary
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
import re
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
    usage_line: str = ""               # regex with tokens_in/tokens_out groups, matched on stdout and stderr;
                                        # when it matches, it is the run's usage (else the JSON summary's)
    build_cmd: str = ""                 # a cheap command naming the harness build (a version report);
                                        # run once per driver per tick and cached, it is the build fallback (H10)
    stages: list[str] = field(default_factory=lambda: list(vocab.STAGES))
    env: dict[str, str] = field(default_factory=dict)
    source: str = ""

    @classmethod
    def from_toml(cls, text: str, source: str) -> "Driver":
        data = tomllib.loads(text)
        unknown = set(data) - {"name", "command", "cwd", "timeout_s", "result", "read_only", "act_args",
                               "read_only_enforced", "stages", "duties", "env", "scope_fingerprint", "usage_line",
                               "build_cmd"}
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
        try:
            groups = set(re.compile(data.get("usage_line", "")).groupindex)
        except re.error as e:
            raise DriverError(f"{source}: usage_line: {e}") from None
        if data.get("usage_line") and not {"tokens_in", "tokens_out"} <= groups:
            raise DriverError(f"{source}: usage_line needs (?P<tokens_in>…) and (?P<tokens_out>…) groups")
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


def run_env(home: Home, brief: dict, token: str | None, broker: str | None = None) -> dict[str, str]:
    """The environment every push-mode run gets (docs/CONTRACT.md §1 lists it; `holon lint` checks that)."""
    stage = brief["stage"]
    env = {**kernel_env(home), "HOLON_RUN_ID": brief["run_id"], "HOLON_TASK_ID": brief["task_id"],
           "HOLON_STAGE": stage, "HOLON_ROLE": vocab.STAGE_ROLE[stage], "HOLON_POSTURE": vocab.POSTURE[stage],
           "HOLON_EMPLOYEE_ID": brief.get("employee_id") or "",
           "HOLON_RUN_DIR": str(Path(brief["brief_md"]).parent),
           "HOLON_BRIEF_DIR": str(Path(brief["brief_md"]).parent),
           "HOLON_RESULT_PATH": brief["result_path"]}
    if token:
        env["HOLON_RUN_TOKEN"] = token
    if broker:  # isolation: the agent's `holon` talks to the broker, not to the databases
        env["HOLON_BROKER"] = broker
    return env


def run(home: Home, driver: Driver, brief: dict, token: str | None = None, alive=None,
        alive_every_s: float = 60.0, isolation: dict | None = None) -> dict:
    """Invoke the harness and return {'exit_code', 'envelope' | None, 'log_path', 'stdout_tail'}.
    `alive()` is called every `alive_every_s` while the harness process runs (the kernel renews the
    run's lease with it), so a lease expires only when the dispatcher itself has died. When it returns
    False (the agent already handed in its result, or the run was reclaimed) the harness is stopped:
    whatever it does after that cannot change the outcome and only holds the department's slot.
    `isolation` ({"runner", "broker"}): the harness starts through the runner, as the agent user, with only
    the run's environment (never the kernel's), and reaches the organization through the broker."""
    if brief["stage"] not in driver.stages:
        raise DriverError(f"driver {driver.name} does not serve the '{brief['stage']}' stage")
    cmd, cwd = render(driver, brief)
    if not Path(cwd).is_dir():
        cwd = str(Path(brief["brief_md"]).parent)
    log_path = Path(brief["brief_md"]).parent / "driver.log"
    if isolation:
        from holon.execution import isolation as iso
        env = {**run_env(home, brief, token, isolation["broker"]), **driver.env}
        exit_code, stdout, stderr = iso.run(isolation["runner"], cmd, cwd, env, driver.timeout_s, alive, alive_every_s)
    else:
        clean = {k: v for k, v in os.environ.items() if k not in ("HOLON_CHAIR_KEY", "HOLON_KEY")}
        env = {**clean, **run_env(home, brief, token), **driver.env}
        exit_code, stdout, stderr = execute(cmd, cwd, env, driver.timeout_s, alive, alive_every_s)
    log_path.write_text(f"$ {cmd}\n[cwd] {cwd}\n[exit] {exit_code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n")
    envelope = None
    outer = extract_last_json(stdout) if driver.result == "stdout-json" else None
    try:
        from holon.execution.isolation import read_agent_file
        text = read_agent_file(brief["result_path"])
        envelope = json.loads(text) if text is not None else None
    except (ValueError, OSError, RuntimeError):
        envelope = None
    if envelope is None and outer is not None:
        envelope = outer
        reply = next((outer[k] for k in ("result", "text") if isinstance(outer.get(k), str)), None)
        if reply is not None:
            # harnesses that wrap the model reply in {"result": "..."} or {"text": "..."}: look inside it
            envelope = extract_last_json(reply) or envelope
    usage, calls = usage_of(outer), usage_from_lines(driver.usage_line, stdout + "\n" + stderr)
    if calls:  # every call, counted the harness's way (some count cached input inside input, some apart)
        usage = {**calls, "cost": (usage or {}).get("cost")}
    return {"exit_code": exit_code, "envelope": envelope, "log_path": str(log_path), "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-2000:], "usage": usage}


def execute(cmd: str, cwd: str, env: dict, timeout_s: float, alive=None, alive_every_s: float = 60.0
            ) -> tuple[int, str, str]:
    """Run a shell command in its own process group as this user; (exit code, stdout, stderr)."""
    proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=max(0.1, min(alive_every_s, deadline - time.monotonic())))
            exit_code = proc.returncode
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                os.killpg(proc.pid, signal.SIGKILL)  # the shell and the harness it started
                stdout, _ = proc.communicate()
                exit_code, stderr = 124, f"driver timed out after {timeout_s}s"
                break
            if alive and alive() is False:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    stdout, stderr = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    stdout, stderr = proc.communicate()
                exit_code, stderr = 0, (stderr or "") + "\n[stopped: the run's result was already in]"
                break
    return exit_code, stdout, stderr


def usage_of(outer: dict | None) -> dict | None:
    """Tokens and cost from a harness's JSON summary ({"usage": {"input_tokens", "output_tokens"}, and
    "total_cost_usd" or "cost"}), when it prints one."""
    if not isinstance(outer, dict) or not isinstance(outer.get("usage"), dict):
        return None
    u = outer["usage"]
    tin = u.get("input_tokens") if isinstance(u.get("input_tokens"), int) else None
    if tin is not None and isinstance(u.get("cache_read_input_tokens"), int):
        tin += u["cache_read_input_tokens"]
    tout = u.get("output_tokens") if isinstance(u.get("output_tokens"), int) else None
    cost = next((outer[k] for k in ("total_cost_usd", "cost") if isinstance(outer.get(k), (int, float))), None)
    return {"tokens_in": tin, "tokens_out": tout, "cost": cost}


def usage_from_lines(pattern: str, text: str) -> dict | None:
    """Tokens summed over a harness's per-call usage lines: complete even when the harness was stopped
    (its agent had already submitted) before it could print its own summary."""
    hits = list(re.finditer(pattern, text)) if pattern else []
    if not hits:
        return None
    return {"tokens_in": sum(int(m["tokens_in"]) for m in hits),
            "tokens_out": sum(int(m["tokens_out"]) for m in hits), "cost": None}


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
