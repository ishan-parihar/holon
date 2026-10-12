"""`holon mcp`: the organization as native tools, over the Model Context Protocol (stdio, JSON-RPC 2.0).

Any MCP-capable harness can mount this server instead of teaching agents shell commands. Each tool
is a command from the CLI registry (holon/cli.py), called in-process with the same authentication:
the server inherits the harness's environment, so `HOLON_RUN_TOKEN` (push mode) or `HOLON_KEY`
(pull mode) identifies the employee, and every call is checked against the command's actor class.
Two tools are MCP-only: `holon_whoami` (who you are, what you are working on) and `holon_submit`,
which takes the result envelope as a structured argument, so observe-stage agents that have no
file-write tool still hand in a typed result instead of printing JSON.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

from holon import __version__

PROTOCOL = "2025-06-18"
# the commands an employee needs while working; chair-only and kernel commands are never exposed
AGENT_COMMANDS = ("emp card", "org", "dept roles", "task show", "task tree", "plan show", "memory search",
                  "memory add", "memory context", "msg send", "msg inbox", "msg request", "skill list", "skill show",
                  "skill invoke", "run show", "run heartbeat", "metrics", "claim")


def _tool_name(path: tuple[str, ...]) -> str:
    return "holon_" + "_".join(p.replace("-", "_") for p in path)


def _arg_schema(a: tuple, kw: dict) -> tuple[str, dict, bool, bool] | None:
    """(name, json-schema, required, positional) for one argparse argument; None for hidden ones."""
    if kw.get("help") is argparse.SUPPRESS or kw.get("dest") == "as_":
        return None
    positional = not a[0].startswith("-")
    name = kw.get("dest") or a[-1].lstrip("-").replace("-", "_")
    action = kw.get("action")
    if action == "store_true":
        s: dict = {"type": "boolean"}
    elif action == "append":
        s = {"type": "array", "items": {"type": "string"}}
    elif kw.get("type") is int:
        s = {"type": "integer"}
    else:
        s = {"type": "string"}
    if kw.get("choices"):
        s["enum"] = list(kw["choices"])
    if kw.get("help"):
        s["description"] = kw["help"]
    required = (positional and kw.get("nargs") != "?") or bool(kw.get("required"))
    return name, s, required, positional


def tools() -> list[dict]:
    from holon.cli import REGISTRY
    by_path = {" ".join(c.path): c for c in REGISTRY}
    out = [
        {"name": "holon_whoami", "description": "Who you are in the organization and what you are working on now.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "holon_submit",
         "description": "Hand in this run's result: the JSON envelope the stage brief describes, as an object. "
                        "Use this instead of writing result.json when you have no file-write tool.",
         "inputSchema": {"type": "object", "required": ["result"], "properties": {
             "result": {"type": "object", "description": "the result envelope (see the brief's Output section)"},
             "run_id": {"type": "string", "description": "default: the run your token belongs to"}}}},
    ]
    for p in AGENT_COMMANDS:
        c = by_path[p]
        props, required = {}, []
        for a, kw in c.args:
            spec = _arg_schema(a, kw)
            if spec is None:
                continue
            name, s, req, _ = spec
            props[name] = s
            if req:
                required.append(name)
        schema: dict = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        out.append({"name": _tool_name(c.path), "description": c.help, "inputSchema": schema})
    return out


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    from holon import cli
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli.main(["--json", *argv])
        except SystemExit as e:  # argparse usage errors
            rc = e.code if isinstance(e.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


def _principal():
    from holon.core.paths import Home
    from holon.execution.service import Kernel
    k = Kernel(Home.resolve(None), actor="anonymous")
    try:
        return k.auth.principal(None), k.home
    finally:
        k.close()


def call(name: str, arguments: dict) -> tuple[bool, str]:
    """Run one tool; returns (is_error, text)."""
    from holon.cli import REGISTRY
    if name == "holon_whoami":
        p, home = _principal()
        if p.kind != "employee":
            return False, json.dumps({"principal": p.kind, "note": "not an employee session"})
        rc, card, err = _run_cli(["emp", "card", p.employee_id])
        info = {"employee_id": p.employee_id, "run_id": p.run_id, "home": str(home),
                "identity": json.loads(card).get("markdown") if rc == 0 else err.strip()}
        if p.run_id:
            rc, run, _ = _run_cli(["run", "show", p.run_id])
            if rc == 0:
                r = json.loads(run)
                info["stage"] = {k: r["stage"][k] for k in ("id", "stage", "parent_id", "title")}
                info["brief_md"] = str(home.run_dir(p.run_id) / "BRIEF.md")
        return False, json.dumps(info, indent=2)
    if name == "holon_submit":
        run_id = arguments.get("run_id") or _principal()[0].run_id
        if not run_id:
            return True, "no run: pass run_id, or call from a run (HOLON_RUN_TOKEN)"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(arguments.get("result"), fh)
        try:
            rc, out, err = _run_cli(["submit", run_id, "--file", fh.name])
        finally:
            os.unlink(fh.name)
        return rc != 0, out.strip() or err.strip()
    cmd = next((c for c in REGISTRY if _tool_name(c.path) == name and " ".join(c.path) in AGENT_COMMANDS), None)
    if cmd is None:
        return True, f"unknown tool {name}"
    argv, opts = list(cmd.path), []
    for a, kw in cmd.args:
        spec = _arg_schema(a, kw)
        if spec is None:
            continue
        key, s, _, positional = spec
        if key not in arguments or arguments[key] is None:
            continue
        v = arguments[key]
        if positional:
            argv.append(str(v))
        elif s["type"] == "boolean":
            if v:
                opts.append(a[0])
        elif s["type"] == "array":
            for item in v:
                opts += [a[0], str(item)]
        else:
            opts += [a[0], str(v)]
    rc, out, err = _run_cli(argv + opts)
    if rc == 3:
        return False, out.strip() or "nothing to do"
    return rc != 0, out.strip() or err.strip() or f"exit {rc}"


def handle(msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:  # a notification (e.g. notifications/initialized): no reply
        return None
    try:
        if method == "initialize":
            requested = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
            result = {"protocolVersion": requested, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "holon", "version": __version__},
                      "instructions": "Holon: you are an employee of an organization. Call holon_whoami first; "
                                      "hand in your stage result with holon_submit."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tools()}
        elif method == "tools/call":
            p = msg.get("params") or {}
            is_error, text = call(p.get("name", ""), p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}], "isError": is_error}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}
    except Exception as e:  # a failing tool must not take the server down
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(msg) if isinstance(msg, dict) else None
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
