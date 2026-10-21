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
from pathlib import Path

from holon import __version__

PROTOCOL = "2025-06-18"

# The agent-facing surface is DERIVED from the registry's own actor class, never hand-listed.
# The actor class is the law (vocab.ACTOR_CLASSES, law 8 in AGENTS.md): a command an employee may
# run with no approval is one an MCP employee may call, and a chair-only or kernel-only command
# is never exposed however useful it looks. A hand-maintained tuple silently drifted from that law
# — 23 `any` commands were unreachable, including `task list`, so a mounted employee could not
# discover the work it was meant to claim (2026-10-21 reachability audit, F1/F8). Deriving the list
# makes that class of omission a test failure instead of a runtime surprise.
AGENT_ACTORS = ("any", "employee")
# The short list of exceptions, each with its reason, because derivation has to stay closed:
#   mcp   — the server itself; exposing it would let a harness spawn a nested server
#   help  — the registry dump; `mcp.tools()` already publishes the schema of every exposed tool
#   lint  — instance lint is the kernel's self-check on artifacts; an employee runs `doctor`
#   whoami|submit — MCP-only tools with typed arguments, published separately below
#   skill check — validates the library, which an employee may read but not author
#   contract schemas — its `--write DIR` publishes the schemas to a directory an agent names, which
#                     is a file write outside the workdir: the actor class is `any`, but the write
#                     reaches where a body must not reach (test_agents_reach_only_agent_commands)
AGENT_BLOCKLIST = ("mcp", "help", "lint", "whoami", "submit", "skill check", "contract schemas")
MCP_ONLY = ("whoami", "submit")


def agent_commands() -> list[tuple[str, ...]]:
    """Every registry command an employee may call, derived from its actor class."""
    from holon.cli import REGISTRY
    return sorted(c.path for c in REGISTRY
                  if c.actor in AGENT_ACTORS and " ".join(c.path) not in AGENT_BLOCKLIST)


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
    elif action == "append" or kw.get("nargs") == argparse.REMAINDER:
        s = {"type": "array", "items": {"type": "string"}}
    elif kw.get("type") is int:
        s = {"type": "integer"}
    else:
        s = {"type": "string"}
    if kw.get("choices"):
        s["enum"] = list(kw["choices"])
    if kw.get("help"):
        s["description"] = kw["help"]
    required = (positional and kw.get("nargs") not in ("?", argparse.REMAINDER)) or bool(kw.get("required"))
    return name, s, required, positional


def _properties_of(stage: str) -> dict:
    """The properties `contract.json_schema(stage)` describes, in the flat form a client needs.

    A stage is either one shape (a plain object of properties) or several (a `oneOf` over its
    variants, each keyed by `kind`). Both are the same contract — the renderer differs, not the
    rules — so both are flattened here: a field any variant declares is published with the type it
    declared. The stage's `kind` discriminator is kept, because a client that cannot type `kind`
    cannot choose a variant."""
    from holon.core import contract
    schema = contract.json_schema(stage)
    props: dict = {}
    for variant in (schema.get("oneOf") or [schema]):
        for name, spec in (variant.get("properties") or {}).items():
            props.setdefault(name, spec)
    return {k: v for k, v in props.items() if k != "kind"} | {"kind": {"type": "string"}}


# The union of every stage's published properties, for a server that cannot tell which stage it is
# serving. A stage-less tool that publishes nothing tells the client nothing, and a client that
# guesses wraps its lists (measured 2026-10-20: a real plan, lost to a shape it never chose).
ALL_STAGE_PROPERTIES: dict[str, dict] | None = None


def _all_properties() -> dict:
    global ALL_STAGE_PROPERTIES
    if ALL_STAGE_PROPERTIES is None:
        from holon.core import vocab
        merged: dict = {}
        for stage in vocab.STAGES:
            try:
                for name, spec in _properties_of(stage).items():
                    merged.setdefault(name, spec)
            except Exception:  # a stage with no schema yet adds nothing; it never erases a type
                continue
        ALL_STAGE_PROPERTIES = merged
    return ALL_STAGE_PROPERTIES


def _result_schema_for_stage(stage: str | None) -> dict:
    """The envelope schema to publish for `stage` (None = the server cannot tell)."""
    props = _properties_of(stage) if stage else _all_properties()
    if not props:
        return {"type": "object", "description": "the result envelope (see the brief's Output section)"}
    which = f"the {stage} result envelope" if stage else "the result envelope"
    return {"type": "object",
            "description": f"{which} (the exact shape the brief describes; lists are arrays)",
            "properties": props}


def _result_schema() -> dict:
    """The envelope schema for THIS run's stage, published to the client.

    **Why (measured 2026-10-20).** A client that cannot type a field coerces an
    untyped array to an object and wraps it as `{"item": [...]}`. A real body produced a
    complete plan; the client transmitted it wrapped; the kernel rejected it and the plan
    was lost. The cause was this tool publishing `result` as a free-form object with no
    `properties` — the client had no field types to coerce *to*, so it guessed.

    The kernel already knows the shape: `contract.json_schema(stage)` is the one writer
    of the rules, and the same schema is rendered beside every brief as
    `envelope.schema.json`. Publishing it here means the tool and the brief agree, and a
    coercing client has the types it needs.

    The stage comes from the run the token belongs to. When it cannot be read — a server started
    by hand, a probe, a harness that mounts the server outside a run — the published schema is the
    union of every stage's, not a free-form object. The tolerance that makes a shape repair safe
    (`contract._repair_untyped_lists`) is a floor, not a reason to leave the client blind.
    """
    return _result_schema_for_stage(_run_stage())


def _run_stage() -> str | None:
    """The stage of the run this server belongs to, from its run token."""
    token = os.environ.get("HOLON_RUN_TOKEN")
    if not token:
        return None
    try:
        from holon.core.paths import Home
        from holon.execution.service import Kernel
        home = Home.resolve(Path(os.environ.get("HOLON_HOME", "~/holon")))
        k = Kernel(home)
        try:
            p = k.auth.principal(token)
            run = k.work.get_run(p.run_id) if p.run_id else None
            return (run or {}).get("kind")
        finally:
            k.close()
    except Exception:
        return None


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
             "result": _result_schema(), "run_id": {"type": "string", "description": "default: the run your token belongs to"}}}},
    ]
    for p in agent_commands():
        c = by_path[" ".join(p)]
        props, required = {}, []
        for a, kw in c.args:
            spec = _arg_schema(a, kw)
            if spec is None:
                continue
            name, s, req, positional = spec
            props[name] = s
            # H-14: the positional marker is part of the tool contract —
            # a runtime building argv from this list must know which
            # properties are positional (native tools read it).
            if positional:
                s["positional"] = True
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


def call(name: str, arguments: dict) -> tuple[bool, str]:
    """Run one tool; returns (is_error, text)."""
    from holon.cli import REGISTRY
    if name == "holon_whoami":
        rc, out, err = _run_cli(["whoami"])
        if rc != 0:
            return True, err.strip()
        info = json.loads(out)
        if info["principal"] != "employee":
            return False, json.dumps({"principal": info["principal"], "note": "not an employee session"})
        rc, card, err = _run_cli(["emp", "card", info["employee_id"]])
        info["identity"] = json.loads(card).get("markdown") if rc == 0 else err.strip()
        return False, json.dumps(info, indent=2)
    if name == "holon_submit":
        run_id = arguments.get("run_id")
        if not run_id:
            rc, out, _ = _run_cli(["whoami"])
            run_id = json.loads(out).get("run_id") if rc == 0 else None
        if not run_id:
            return True, "no run: pass run_id, or call from a run (HOLON_RUN_TOKEN)"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(arguments.get("result"), fh)
        try:
            rc, out, err = _run_cli(["submit", run_id, "--file", fh.name])
        finally:
            os.unlink(fh.name)
        return rc != 0, out.strip() or err.strip()
    # an unknown tool and a chair-only command are the same answer: the allowlist is the boundary,
    # so a name outside it is refused rather than served from the full registry
    cmd = next((c for c in REGISTRY if _tool_name(c.path) == name and c.path in agent_commands()), None)
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
            argv += [str(i) for i in v] if s["type"] == "array" else [str(v)]
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
