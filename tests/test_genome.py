"""The genome layer (U1–U3, K-4/K-5): units declared in manifests, synced into the database,
published as read API v3, pooled into briefs and reach, consumed through typed requests."""

import io
import json
import os
import time
import types
from contextlib import redirect_stdout

from holon.genome import units
from holon.integrity import doctor, lint
from tests.helpers import JD, KernelCase

VAULT = """
charter = "the research substrate"
category = "service"
tier = "foundation"
roots = ["%s/data/vault"]
pool_of = ["ops"]
consumed = []

[[services]]
id = "kosmos"
command = "kosmos --query"
accepts = "a research query"
returns = "sources and notes"
"""


class Genome(KernelCase):
    def setUp(self):
        super().setUp()
        o = self.k.org
        o.create_dept("ops", "Operations", "Run things")
        self.maker = o.hire(name="Mo", title="Ops engineer", dept="ops", job_description=JD, slot="maker",
                            driver="fake", reason="r", status="active")["id"]
        self.checker = o.hire(name="Ce", title="Ops checker", dept="ops", job_description=JD, slot="checker",
                              driver="fake", reason="r", status="active")["id"]
        d = self.home.genome_units_dir
        d.mkdir(parents=True)
        (d / "vault.toml").write_text(VAULT % self.tmp)

    def _sync(self):
        return units.sync(self.k)

    def test_sync_publishes_units_and_services_as_read_api_v3(self):
        out = self._sync()
        self.assertEqual((out["units"], out["services"]), (1, 1))
        self.assertEqual(self.k.wconn.execute(
            "SELECT value FROM api_meta WHERE key='api_version'").fetchone()[0], "3")
        self.assertEqual([r["slug"] for r in self.k.wconn.execute("SELECT * FROM api_units")], ["vault"])
        self.assertEqual([r["unit"] for r in self.k.wconn.execute("SELECT * FROM api_services")], ["vault"])
        self.assertEqual(self.k.wconn.execute(
            "SELECT command FROM api_services WHERE unit='vault' AND service='kosmos'").fetchone()[0],
            "kosmos --query")
        self.assertEqual(lint.run(self.k, ["api", "genome"]), [])

    def test_api_memory_reads_the_live_memory(self):
        self.k.memory.add_item("org", "decision", "v3 is the genome API", "this test")
        self._sync()
        rows = self.k.mconn.execute("SELECT scope, kind, content FROM api_memory").fetchall()
        self.assertTrue(any(r["content"] == "v3 is the genome API" for r in rows),
                        "decisions are readable on the memory database, where the view lives")

    def test_bind_service_and_bind_pool_catch_dangling_references(self):
        (self.home.genome_units_dir / "vault.toml").write_text((VAULT % self.tmp).replace(
            'consumed = []', 'consumed = ["ghost:nothing"]').replace('pool_of = ["ops"]', 'pool_of = ["no-such-dept"]'))
        findings = lint.run(self.k, ["genome"])
        msgs = {f["message"] for f in findings}
        self.assertTrue(any("bind.service" in m for m in msgs), msgs)
        self.assertTrue(any("bind.pool" in m for m in msgs), msgs)
        self._sync()   # the source is the source: sync still lands parse-valid rows; lint is the guard
        self.assertEqual(self.k.wconn.execute(
            "SELECT other FROM unit_edges WHERE kind='consumes' AND slug='vault'"
        ).fetchone()[0], "ghost:nothing", "the dangling edge is visible until fixed")

    def test_pooled_units_join_the_reach_and_render_in_briefs(self):
        self._sync()
        roots = self.k.scope("ops")["roots"]
        self.assertIn(str(self.tmp / "data" / "vault"), [str(r) for r in roots],
                      "the pooled unit's roots join the department's reach (U2)")
        w = self.add(dept="ops", task_class="demo")
        self.plan_and_approve(w, planner=self.maker, assignee=self.maker, writes=[])
        c = self.k.claim("execute", self.maker)
        ctx = (c.brief_dir / "context.md").read_text()
        self.assertIn("Pooled units", ctx, "presence: the executor sees what it can call (U2)")
        self.assertIn("vault:kosmos", ctx)
        self.assertIn("msg request", ctx, "writes to a pooled unit go through a request, never the pen")

    def test_a_typed_request_names_the_entry_point(self):
        self._sync()
        from holon import cli
        args = types.SimpleNamespace(as_=self.maker, dept="eng", title="Query the vault",
                                     intent="get sources", acceptance=None, priority=3,
                                     service="vault:kosmos", json=True,
                                     principal=types.SimpleNamespace(kind="employee",
                                                                     employee_id=self.maker))
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_msg_request(args, self.k)
        t = json.loads(buf.getvalue())
        self.assertTrue(t["title"].startswith("[vault:kosmos]"), t["title"])
        args.service = "vault:nope"
        with self.assertRaises(ValueError):
            with redirect_stdout(io.StringIO()):
                cli.cmd_msg_request(args, self.k)

    def test_emit_derives_the_registry_and_doctor_flags_staleness(self):
        self._sync()
        p = units.emit(self.k)
        self.assertIn("vault:kosmos", p.read_text())
        self.assertEqual([f for f in doctor.run(self.k) if f["check"].startswith("genome")], [])
        import os
        import time
        (self.home.genome_units_dir / "vault.toml").write_text(
            (VAULT % self.tmp).replace("the research substrate", "the research substrate, revised"))
        os.utime(self.home.genome_units_dir / "vault.toml", (time.time() + 5, time.time() + 5))
        checks = {f["check"] for f in doctor.run(self.k)}
        self.assertIn("genome.stale", checks, "a manifest newer than the sync is a warning")
