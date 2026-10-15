"""Core primitives: ids, cron, the result contract, migrations (incl. converting pre-graph instances),
memory, context, grading, and the agnosticism guarantees."""

import ast
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from holon.capabilities.context import compile_context
from holon.core import contract, vocab
from holon.core.contract import EnvelopeError
from holon.core.db import connect, migrate
from holon.core.ids import new_id, ulid
from holon.execution.cron import Cron, CronError
from holon.execution.drivers import extract_last_json
from holon.execution.grading import grade_plan
from holon.execution.service import Kernel
from tests.helpers import KernelCase

PKG = Path(__file__).resolve().parents[1] / "holon"
REPO = PKG.parent


class Ids(unittest.TestCase):
    def test_sortable_and_unique(self):
        ids = [ulid() for _ in range(2000)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(new_id("task").startswith("tsk_"))


class CronTests(unittest.TestCase):
    def test_next_after(self):
        base = datetime(2026, 10, 12, 10, 7, tzinfo=timezone.utc)  # a Monday
        self.assertEqual(Cron("*/15 * * * *").next_after(base), base.replace(minute=15))
        self.assertEqual(Cron("0 9 * * *").next_after(base), datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(Cron("30 8 * * 0").next_after(base), datetime(2026, 10, 18, 8, 30, tzinfo=timezone.utc))
        self.assertEqual(Cron("0 0 1 1 *").next_after(base), datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc))

    def test_rejects_bad_expressions(self):
        for bad in ("* * * *", "61 * * * *", "*/0 * * * *", "a b c d e"):
            with self.assertRaises(CronError):
                Cron(bad)


class Contract(unittest.TestCase):
    def test_a_bad_label_on_a_learning_is_dropped_not_fatal(self):
        env = contract.validate("retro", {"summary": "s", "cause": "c",
                                          "learnings": [{"content": "x", "kind": "insight", "confidence": "sure"}]})
        self.assertEqual(env["learnings"], [{"content": "x"}])
        with self.assertRaises(contract.EnvelopeError):
            contract.validate("verify", {"verdict": "maybe", "note": "n"})

    def test_every_stage_has_a_spec(self):
        self.assertEqual(set(contract.SPECS), set(vocab.STAGES))

    def test_plan_variants(self):
        contract.validate("plan", {"kind": "plan", "findings": "t", "steps": ["a"]})
        contract.validate("plan", {"kind": "needs_input", "question": "q?"})
        contract.validate("plan", {"kind": "decompose", "rationale": "r", "children": [
            {"title": "a", "intent": "x"}, {"title": "b", "intent": "y", "after": [0]}]})
        for bad in ({"kind": "plan"}, {"kind": "decompose", "rationale": "r", "children": [{"title": "a"}]},
                    {"kind": "nope"}, [], {"kind": "plan", "findings": "t", "steps": [], "writes": "x"},
                    {"kind": "plan", "findings": "t", "steps": ["a"], "grade": "G9"}):
            with self.assertRaises(EnvelopeError):
                contract.validate("plan", bad)

    def test_execute_and_common_fields(self):
        contract.validate("execute", {"outcome": "success", "summary": "s", "documentation": "d",
                                      "learnings": ["x", {"content": "y", "kind": "fact"}],
                                      "proposals": ["p", {"title": "t", "intent": "i"}]})
        with self.assertRaises(EnvelopeError) as cm:
            contract.validate("execute", {"outcome": "meh", "summary": "", "learnings": [{"kind": "bogus"}]})
        self.assertGreaterEqual(len(cm.exception.problems), 3)

    def test_usage_is_a_common_field_of_numbers(self):
        base = {"outcome": "success", "summary": "s", "documentation": "d"}
        env = contract.validate("execute", {**base, "usage": {"tokens_in": 100, "tokens_out": 50,
                                                              "cost": 0.25, "tool_calls": 7,
                                                              "duration_s": 12.5}})
        self.assertEqual(env["usage"]["cost"], 0.25)
        contract.validate("execute", {**base, "usage": {}})
        for bad in ({"tokens_in": "many"}, {"tool_calls": True}, {"duration_s": -1}, "cheap"):
            with self.assertRaises(EnvelopeError):
                contract.validate("execute", {**base, "usage": bad})

    def test_conditional_requirements(self):
        contract.validate("approve", {"decision": "approve"})
        contract.validate("approve", {"decision": "reject", "note": "why"})
        with self.assertRaises(EnvelopeError):
            contract.validate("approve", {"decision": "reject"})
        contract.validate("shape", {"decision": "redirect", "note": "theirs", "dept": "ops"})
        with self.assertRaises(EnvelopeError):
            contract.validate("shape", {"decision": "redirect", "note": "theirs"})

    def test_research_and_retro(self):
        contract.validate("research", {"summary": "s", "terrain": [{"content": "c", "source": "ls"}]})
        with self.assertRaises(EnvelopeError):
            contract.validate("research", {"summary": "s", "terrain": [{"content": "c"}]})
        contract.validate("retro", {"summary": "s", "cause": "c", "actions": [{"title": "t", "intent": "i"}]})

    def test_published_schemas_match_the_contract(self):
        for stage in vocab.STAGES:
            published = REPO / "schemas" / f"{stage}-result.schema.json"
            self.assertTrue(published.exists(), f"missing {published}; run holon contract schemas --write schemas")
            self.assertEqual(published.read_text(), contract.schema_text(stage), f"{published} drifted")

    def test_extract_last_json(self):
        self.assertEqual(extract_last_json('noise {"a": 1} more {"b": {"c": 2}} tail'), {"b": {"c": 2}})
        self.assertIsNone(extract_last_json("no json here"))


class Migrations(unittest.TestCase):
    def test_idempotent(self):
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.row_factory = sqlite3.Row
        self.assertTrue(migrate(conn, "work"))
        self.assertEqual(migrate(conn, "work"), [])
        self.assertFalse(conn.execute("SELECT 1 FROM schema_migrations WHERE checksum IS NULL").fetchone())
        conn.execute("UPDATE schema_migrations SET checksum=NULL WHERE name='0001_init.sql'")
        migrate(conn, "work")
        self.assertTrue(conn.execute("SELECT checksum FROM schema_migrations WHERE name='0001_init.sql'").fetchone()[0],
                        "pre-checksum instances adopt the shipped files as their baseline")

    def test_pre_graph_instance_converts(self):
        """An instance created before the work graph (migrations 0001-0003) keeps every task, plan and result."""
        d = Path(tempfile.mkdtemp())
        conn = connect(d / "work.db")
        conn.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
        pkg = resources.files("holon.migrations").joinpath("work")
        for name in ("0001_init.sql", "0002_search.fts.sql", "0003_roles_and_profiles.sql"):
            conn.executescript(pkg.joinpath(name).read_text())
            conn.execute("INSERT INTO schema_migrations VALUES (?, 'x')", (name,))
        ts = "2026-10-12T00:00:00.000000Z"
        x = conn.execute
        x("INSERT INTO departments (id, slug, name, created_at, head_id) VALUES ('d1','eng','Eng',?, 'e1')", (ts,))
        x("INSERT INTO employees (id,name,title,dept_id,job_description,duties,driver,status,hired_at,hired_reason) "
          "VALUES ('e1','Bo','HOD','d1','jd','[\"plan\",\"review\"]','x','active',?,'r')", (ts,))
        x("INSERT INTO employees (id,name,title,dept_id,job_description,duties,driver,status,hired_at,hired_reason) "
          "VALUES ('e2','Cy','Dev','d1','jd','[\"execute\"]','x','active',?,'r')", (ts,))

        def task(tid, status):
            x("INSERT INTO tasks (id, dept_id, title, intent, source, status, created_at, updated_at) "
              "VALUES (?, 'd1', ?, 'do it', 'chair', ?, ?, ?)", (tid, tid, status, ts, ts))

        def plan(pid, tid, status, approver="chair"):
            x("INSERT INTO plans (id, task_id, version, author, findings, steps, writes, grade, approver, status,"
              " created_at) VALUES (?,?,1,'e1','f','[\"s\"]','[]',2,?,?,?)", (pid, tid, approver, status, ts))

        task("t_inbox", "inbox")
        task("t_wait", "awaiting_approval")
        plan("pln_wait", "t_wait", "awaiting_approval")
        task("t_done", "done")
        plan("pln_done", "t_done", "approved")
        x("INSERT INTO runs (id, task_id, plan_id, kind, employee_id, started_at, status) "
          "VALUES ('run_x', 't_done', 'pln_done', 'execute', 'e2', ?, 'succeeded')", (ts,))
        x("INSERT INTO results (run_id, outcome, summary, documentation, verified, verified_by, created_at) "
          "VALUES ('run_x', 'success', 'did it', '# doc', 1, 'emp:e1', ?)", (ts,))
        x("INSERT INTO schedules (id, kind, cron, employee_id, created_at) VALUES ('sch1','plan','* * * * *','e1',?)",
          (ts,))
        applied = migrate(conn, "work")
        self.assertIn("0004_work_graph.py", applied)
        get = lambda q, *a: conn.execute(q, a).fetchone()  # noqa: E731
        self.assertEqual(get("SELECT status FROM tasks WHERE id='t_inbox'")[0], "open")
        self.assertEqual(get("SELECT stage, status FROM tasks WHERE parent_id='t_inbox'")[:], ("plan", "ready"))
        self.assertEqual(get("SELECT stage, status, outcome FROM tasks WHERE id='pln_wait'")[:], ("plan", "done", "plan"))
        self.assertEqual(get("SELECT status, approver FROM tasks WHERE parent_id='t_wait' AND stage='approve'")[:],
                         ("ready", "chair"))
        self.assertEqual(get("SELECT status FROM tasks WHERE id='t_done'")[0], "done")
        self.assertEqual(get("SELECT o.verified FROM outputs o JOIN tasks t ON t.id=o.task_id "
                             "WHERE t.parent_id='t_done' AND o.kind='execute'")[0], 1)
        self.assertEqual(json.loads(get("SELECT roles FROM employees WHERE id='e1'")[0]), ["head", "planner"])
        self.assertEqual(get("SELECT kind, stage FROM schedules WHERE id='sch1'")[:], ("wake", "plan"))
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        conn.close()


class MemoryBehaviour(KernelCase):
    def test_exact_duplicate_reinforces_and_near_duplicate_supersedes(self):
        m = self.k.memory
        a = m.add_item("proj:x", "fact", "The API base URL is https://api.example.com/v2", "human")
        b = m.add_item("proj:x", "fact", "the api base url is https://api.example.com/v2!", "human")
        self.assertEqual((b["action"], b["id"]), ("reinforced", a["id"]))
        c = m.add_item("proj:x", "fact", "The API base URL is https://api.example.com/v2 (production)", "agent")
        self.assertEqual(c["action"], "superseded")
        self.assertEqual(m.get_item(a["id"])["superseded_by"], c["id"])
        self.assertEqual([i["id"] for i in m.live_items(["proj:x"])], [c["id"]])

    def test_context_budget_lineage_and_logbook(self):
        self.home.trajectory_db.parent.mkdir(parents=True, exist_ok=True)
        tc = sqlite3.connect(self.home.trajectory_db)
        tc.executescript("""CREATE TABLE nodes (id TEXT PRIMARY KEY, parent_id TEXT, item_type TEXT, name TEXT,
            status TEXT, description TEXT);
            INSERT INTO nodes VALUES ('v1', NULL, 'Vision-Statement', 'Sovereign life', 'Active', '');
            INSERT INTO nodes VALUES ('g1', 'v1', 'Annual-Goal', 'Ship holon', 'Active', 'agnostic kernel');
            INSERT INTO nodes VALUES ('p1', 'g1', 'Project', 'Kernel P1', 'Active', '');""")
        tc.close()
        self.home.logbook_db.parent.mkdir(parents=True, exist_ok=True)
        lc = sqlite3.connect(self.home.logbook_db)
        lc.executescript("""CREATE TABLE entries (id TEXT PRIMARY KEY, date TEXT, channel TEXT, title TEXT, body TEXT);
            CREATE VIRTUAL TABLE entries_fts USING fts5(title, body, content='entries', content_rowid='rowid');
            INSERT INTO entries VALUES ('e1','2026-10-01','strategic','Kernel thoughts','the kernel must be agnostic');
            INSERT INTO entries_fts(rowid, title, body) SELECT rowid, title, body FROM entries;""")
        lc.close()
        t = self.add("Kernel review", intent="Review the kernel design", project_ref="traj:p1")
        self.k.memory.add_item("traj:p1", "constraint", "Never import harness code", "human", pinned=True)
        stage = self.k.work.stages(t["id"])[0]
        md, meta = compile_context(self.home, self.k.work, self.k.memory, stage["id"])
        self.assertIn("Ship holon", md)
        self.assertIn("Never import harness code", md)
        self.assertIn("Kernel thoughts", md)
        self.assertEqual(meta["degraded"], [])
        small, meta = compile_context(self.home, self.k.work, self.k.memory, stage["id"], budget=600)
        self.assertLessEqual(len(small), 600 + 400)
        self.assertTrue(meta["degraded"])

    def test_missing_sources_degrade_not_fail(self):
        t = self.add(project_ref="traj:nowhere")
        md, meta = compile_context(self.home, self.k.work, self.k.memory, t["id"], run_id="run_x")
        self.assertIn("trajectory.db absent", meta["degraded"])
        self.assertIn("Context gaps", md)
        row = self.k.mconn.execute("SELECT degraded FROM context_log WHERE run_id='run_x'").fetchone()
        self.assertIn("trajectory", row[0])


class Grading(KernelCase):
    config = '[grading]\nnovelty_min_verified = 0\nsensitive_paths = ["*/billing/*"]\n'

    def test_floor_signals(self):
        t = self.add(dept="eng", task_class="ops", risk="low")
        g = lambda env, w=t: grade_plan(self.k.wconn, w, env, self.k.config)  # noqa: E731
        self.assertEqual(g({"kind": "plan", "writes": ["a.txt"]})["grade"], 2, "no proposal -> default G2")
        self.assertEqual(g({"kind": "plan", "grade": 0})["grade"], 0)
        self.assertEqual(g({"kind": "plan", "grade": 0, "writes": ["/etc/hosts"]})["grade"], 2)
        r = g({"kind": "plan", "grade": 0, "writes": ["/srv/billing/x.py"]})
        self.assertEqual(r["grade"], 3)
        self.assertTrue(any("sensitive" in x for x in r["reasons"]))
        irreversible = self.add(dept="eng", task_class="ops", risk="irreversible")
        self.assertEqual(g({"kind": "plan", "grade": 0}, irreversible)["grade"], 4)


class Agnosticism(unittest.TestCase):
    """The kernel's defining guarantees: stdlib only, and no harness named anywhere in the distribution's
    kernel (code, skills, genome)."""

    def test_only_stdlib_imports(self):
        stdlib = set(sys.stdlib_module_names)
        offenders = []
        for f in PKG.rglob("*.py"):
            for node in ast.walk(ast.parse(f.read_text())):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                for n in names:
                    top = n.split(".")[0]
                    if top != "holon" and top not in stdlib:
                        offenders.append(f"{f.relative_to(PKG)}: {n}")
        self.assertEqual(offenders, [])

    def test_no_harness_named_in_kernel(self):
        from holon.integrity.lint import Findings, harness_names, pack_agnostic
        self.assertTrue({"zeroclaw", "hermes", "claude"} <= harness_names())
        f = Findings()
        pack_agnostic(None, f)
        self.assertEqual(f, [])


if __name__ == "__main__":
    unittest.main()


class ChairChannels(KernelCase):
    """H8 (phase F): the Chair's channel identities — a runtime that fronts a channel (a gateway)
    may run Chair commands only for one of these; the broker still forbids agents every Chair
    command, so the list is read by runtimes, never by agent-reachable code paths."""

    config = '[chair]\nchannels = ["telegram:123456", "notion:user-7"]\n'

    def test_channel_identities_come_from_config(self):
        self.assertEqual(self.k.chair_channels(), {"telegram:123456", "notion:user-7"})
        self.home.config_file.write_text(self.pipeline + "\n")
        self.k.close()
        from holon.execution.service import Kernel
        self.assertEqual(Kernel(self.home).chair_channels(), set(), "no channels configured")

    def test_the_cli_exposes_the_channel_refs_for_runtimes(self):
        # a runtime that fronts a channel (a gateway) reads the refs through
        # the CLI — one parser of [chair], never a second one in the runtime
        from holon import cli
        rc = cli.main(["--home", str(self.home.root), "--json", "chair-channels"])
        self.assertEqual(rc, 0)
        rc = cli.main(["--home", str(self.home.root), "chair-channels"])
        self.assertEqual(rc, 0)  # the text arm prints; the exit is the contract


class DatabaseVersion(KernelCase):
    """H-09 (integration audit): a database migrated by a newer Holon must refuse to run under
    older code — silently writing to a newer schema is undefined behaviour, and two checkouts
    sharing one launcher made it a live hazard."""

    def _inject_future_migration(self):
        for db in (self.home.work_db, self.home.memory_db):
            c = sqlite3.connect(db)
            c.execute("INSERT INTO schema_migrations VALUES ('9999_future.sql', ?, 'x')",
                      (datetime.now(timezone.utc).isoformat(),))
            c.commit()
            c.close()

    def test_a_database_newer_than_the_code_refuses_to_run(self):
        self._inject_future_migration()
        with self.assertRaises(RuntimeError) as cm:
            Kernel(self.home, "chair")
        self.assertIn("9999_future.sql", str(cm.exception))
        self.assertIn("newer Holon", str(cm.exception))
