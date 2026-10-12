"""`holon mcp`: the organization as MCP tools, authenticated by the environment the harness passes on."""

import json
import os
import subprocess
import sys
from pathlib import Path

from holon import mcp
from holon.cli import REGISTRY
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
