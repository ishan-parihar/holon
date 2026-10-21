"""`holon mcp`: the organization as MCP tools, authenticated by the environment the harness passes on."""

import json
import os
import subprocess
import sys
from pathlib import Path

from holon import mcp
from holon.cli import REGISTRY, _positional_flags
from tests.helpers import KernelCase

REPO = Path(__file__).resolve().parents[1]


class Protocol(KernelCase):
    def test_tools_come_from_the_registry_and_exclude_chair_commands(self):
        names = {t["name"] for t in mcp.tools()}
        self.assertTrue({"holon_whoami", "holon_submit", "holon_memory_search", "holon_run_heartbeat"} <= names)
        actors = {mcp._tool_name(c.path): c.actor for c in REGISTRY}
        for n in names - {"holon_whoami", "holon_submit"}:
            self.assertIn(actors[n], ("any", "employee"), n)
        self.assertNotIn("holon_plan_approve", names)
        search = next(t for t in mcp.tools() if t["name"] == "holon_memory_search")
        self.assertEqual(search["inputSchema"]["required"], ["query"])
        self.assertNotIn("as_", json.dumps(mcp.tools()), "identity comes from the token, never an argument")

    def test_the_agent_fence_carries_the_knowledge_join_commands(self):
        names = {t["name"] for t in mcp.tools()}
        self.assertTrue({"holon_memory_episodes", "holon_playbook_list", "holon_playbook_show",
                         "holon_events", "holon_skill_path"} <= names,
                        "the kernel's synthesis commands belong in the agent fence (KB-2, KB-3)")

    def test_handshake_and_unknown_methods(self):
        init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2024-11-05"}})
        self.assertEqual(init["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("tools", init["result"]["capabilities"])
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "nope"})["error"]["code"], -32601)


class OverStdio(KernelCase):
    def session(self, env, *calls):
        msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": mcp.PROTOCOL}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"}]
        msgs += [{"jsonrpc": "2.0", "id": i + 1, "method": "tools/call", "params": {"name": n, "arguments": a}}
                 for i, (n, a) in enumerate(calls)]
        full = {**os.environ, "PYTHONPATH": str(REPO), "HOLON_HOME": str(self.home.root), **env}
        p = subprocess.run([sys.executable, "-m", "holon", "mcp"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                           capture_output=True, text=True, env=full, timeout=120)
        replies = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
        self.assertEqual(len(replies), len(calls) + 1, p.stderr)
        return [(r["result"]["isError"], r["result"]["content"][0]["text"]) for r in replies[1:]]

    def test_an_observe_stage_agent_works_and_submits_through_tools(self):
        self.k.auth.init_chair_key()
        t = self.add(dept="eng")
        self.k.memory.add_item("dept:eng", "fact", "Greeting files live at the repository root", "test")
        c = self.k.claim("plan", self.hod)
        other = self.k.claim("plan", self.hod, task_id=self.add("other", dept="eng")["id"])
        env = {"HOLON_RUN_TOKEN": c.token, "HOLON_CHAIR_KEY_FILE": str(self.tmp / "absent.key")}
        (who_err, who), (s_err, found), (o_err, _), (sub_err, sub) = self.session(
            env, ("holon_whoami", {}), ("holon_memory_search", {"query": "greeting files"}),
            ("holon_submit", {"run_id": other.run["id"], "result": self.plan_env()}),
            ("holon_submit", {"result": self.plan_env()}))
        self.assertFalse(who_err)
        self.assertEqual(json.loads(who)["stage"]["stage"], "plan")
        self.assertIn("You are Bo", json.loads(who)["identity"])
        self.assertIn("repository root", found)
        self.assertTrue(o_err, "a token covers only its own run")
        self.assertFalse(sub_err, sub)
        self.assertEqual(self.pipeline_of(t)[-1], ("approve", "ready"))

    def test_without_a_token_or_key_tools_are_read_only(self):
        self.k.auth.init_chair_key()
        self.add(dept="eng")
        (err, text), = self.session({"HOLON_CHAIR_KEY_FILE": str(self.tmp / "absent.key")},
                                    ("holon_msg_send", {"to": "dept:eng", "body": "hello"}))
        self.assertTrue(err)
        self.assertIn("is for the employee actor class", text)


class SubmitSchema(KernelCase):
    """G-b (measured 2026-10-21): what `holon_submit` publishes about the envelope's shape.

    The tool's schema is the only thing a client can coerce *to*. Publishing a free-form object
    means the client guesses — and the guess it made (an untyped array becomes `{"item": …}`) lost a
    real body's work. The kernel already knows the shape; the tool must publish it.
    """

    def _submit_tool(self):
        return next(t for t in mcp.tools() if t["name"] == "holon_submit")

    def test_the_published_schema_names_the_envelopes_fields(self):
        schema = self._submit_tool()["inputSchema"]["properties"]["result"]
        props = schema.get("properties") or {}
        self.assertTrue(props, "the client is told the field types, not left to guess")
        for field in ("kind", "steps", "writes", "external_effects", "findings"):
            self.assertIn(field, props, f"{field} is a field the client must type correctly")

    def test_a_list_field_is_published_as_an_array(self):
        """The defect this repairs: a list field published with no type arrives wrapped.
        Every list field the contract names is published as an array."""
        schema = self._submit_tool()["inputSchema"]["properties"]["result"]
        variants = schema.get("oneOf") or [schema]
        for name in ("steps", "writes", "external_effects", "learnings"):
            typed = [s for v in variants for k, s in (v.get("properties") or {}).items() if k == name]
            if not typed:
                continue
            # `steps` is text-or-list in the contract, so it is a oneOf — every other list field is
            # a plain array. What matters in both: a list is ADMITTED, and named as the type to
            # coerce to, which is what the client had no way to learn before.
            spec = typed[0]
            admits_list = spec.get("type") == "array" or "array" in json.dumps(spec)
            self.assertTrue(admits_list, f"{name} admits a list, published as {json.dumps(spec)[:80]}")

    def test_the_stage_less_server_publishes_types_too(self):
        """A server started with no run token (`_run_stage()` is None) is the one a hand-started
        probe hits, and it is where the free-form object came back. The contract's types must be
        published there too — the shape repair is not conditional on having a token."""
        schema = mcp._result_schema_for_stage(None)
        self.assertTrue(schema.get("properties") or schema.get("oneOf"),
                        "a server with no token still publishes types")

    def test_a_real_submit_call_is_accepted_against_the_published_schema(self):
        """The schema is not decoration: an envelope it describes is the envelope the kernel accepts."""
        self.k.auth.init_chair_key()
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        env = self.plan_env(external_effects=[])
        for field in ("steps", "writes", "external_effects"):
            self.assertIsInstance(env.get(field), list, f"{field} is a list in the canonical envelope")
        got = self.k.submit(c.run["id"], env)
        self.assertEqual(got["approval"]["stage"], "approve")

    def test_the_field_a_body_declares_empty_is_published(self):
        schema = self._submit_tool()["inputSchema"]["properties"]["result"]
        self.assertIn("external_effects", schema["properties"],
                      "a field a body may declare empty is published, so its emptiness is intended")


class ArgumentNames(OverStdio):
    """G-c (measured 2026-10-21): the names a runtime sends, and the names the CLI accepts.

    The operant body's own lesson: `holon_dept_roles` sent `{"slug": "conformance"}` and came back
    "unrecognized arguments: --slug". Its native tool layer built `--slug` from the MCP schema's
    property name — which is the name the kernel publishes. A tool that names a property `slug` and
    then rejects `--slug` publishes a contract its own client cannot follow.

    The kernel already marks these `"positional": true` (H-14). A runtime that reads the marker
    sends the value bare; one that does not sends `--name`. Both must work, because the kernel
    publishes the name either way and cannot dictate which runtime reads the marker."""

    def _session_with_token(self):
        self.k.auth.init_chair_key()
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        return {"HOLON_RUN_TOKEN": c.token, "HOLON_CHAIR_KEY_FILE": str(self.tmp / "absent.key")}

    def test_a_named_property_sent_as_a_flag_is_accepted(self):
        """The CLI, reached the way a native tool layer reaches it, must accept the name the MCP
        schema publishes. This is the operant body's exact call: `--slug`, answered with a usage
        dump."""
        env = self._session_with_token()
        (err, text), = self.session(env, ("holon_dept_roles", {"slug": "eng"}))
        self.assertFalse(err, text)
        # the same name, sent the way a runtime that ignores the positional marker builds it:
        argv = _positional_flags(["--json", "dept", "roles", "--slug", "eng"])
        self.assertEqual(argv, ["--json", "dept", "roles", "eng"], "the published name is accepted")

    def test_a_property_that_is_not_a_positional_keeps_failing(self):
        """The normalizer rewrites only what the command names as a positional. An unknown flag, and
        a name that belongs to another command, must still be rejected: a tolerance that silently
        swallowed every `--flag` would hide real mistakes."""
        self.assertEqual(_positional_flags(["dept", "roles", "--bogus", "eng"]),
                         ["dept", "roles", "--bogus", "eng"], "an unknown flag is left to fail")
        self.assertEqual(_positional_flags(["memory", "search", "--slug", "eng"]),
                         ["memory", "search", "--slug", "eng"],
                         "`slug` is not a positional of memory search")

    def test_the_submit_run_id_flag_is_normalized(self):
        """`run_id` is a positional of `submit`, so the flag form a runtime derives is accepted —
        this is the second reported rejection, on the tool whose failure loses a body's work."""
        rid = "run_01M5DX8EBA18TZVX5385Y2CACJ"
        self.assertEqual(_positional_flags(["submit", "--run_id", rid]), ["submit", rid])

    def test_a_positional_property_sent_bare_still_works(self):
        env = self._session_with_token()
        (err, text), = self.session(env, ("holon_playbook_list", {}))
        self.assertFalse(err, f"the bare form the marker asks for is accepted: {text}")

    def test_the_submit_run_id_sent_as_a_flag_is_accepted(self):
        """The same runtime shape, on the tool whose failure loses a body's work: `run_id` sent as
        `--run_id` was the second reported rejection."""
        self.k.auth.init_chair_key()
        t = self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        other = self.k.claim("plan", self.hod, task_id=self.add("other", dept="eng")["id"])
        env = {"HOLON_RUN_TOKEN": other.token, "HOLON_CHAIR_KEY_FILE": str(self.tmp / "absent.key")}
        (err, text), = self.session(env, ("holon_submit", {"run_id": other.run["id"],
                                                      "result": self.plan_env()}))
        self.assertFalse(err, f"run_id names the run: {text}")
        self.assertEqual(self.pipeline_of(t)[-1], ("plan", "claimed"),
                         "a token covers only its own run: the other run's id is not a submission")

