"""Core primitives, envelopes, memory, context, and the agnosticism guarantees."""

import ast
import sqlite3
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from holon.capabilities.context import compile_context
from holon.core.db import migrate
from holon.core.ids import new_id, ulid
from holon.execution.cron import Cron, CronError
from holon.execution.envelopes import EnvelopeError, extract_last_json, validate_executor, validate_planner
from tests.helpers import KernelCase

PKG = Path(__file__).resolve().parents[1] / "holon"


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


class Envelopes(unittest.TestCase):
    def test_planner_shapes(self):
        validate_planner({"kind": "plan", "findings": "t", "steps": ["a"]})
        validate_planner({"kind": "needs_input", "question": "q?"})
        validate_planner({"kind": "decompose", "rationale": "r",
                          "children": [{"title": "a", "intent": "x"}, {"title": "b", "intent": "y"}]})
        for bad in ({"kind": "plan"}, {"kind": "decompose", "rationale": "r", "children": [{"title": "a"}]},
                    {"kind": "nope"}, [], {"kind": "plan", "findings": "t", "steps": [], "writes": "x"}):
            with self.assertRaises(EnvelopeError):
                validate_planner(bad)

    def test_executor_shape(self):
        validate_executor({"outcome": "success", "summary": "s", "documentation": "d",
                           "learnings": ["x", {"content": "y", "kind": "fact"}]})
        with self.assertRaises(EnvelopeError) as cm:
            validate_executor({"outcome": "meh", "summary": "", "learnings": [{"kind": "bogus"}]})
        self.assertGreaterEqual(len(cm.exception.problems), 3)

    def test_extract_last_json(self):
        self.assertEqual(extract_last_json('noise {"a": 1} more {"b": {"c": 2}} tail'), {"b": {"c": 2}})
        self.assertIsNone(extract_last_json("no json here"))


class Migrations(unittest.TestCase):
    def test_idempotent(self):
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.row_factory = sqlite3.Row
        first = migrate(conn, "work")
        self.assertTrue(first)
        self.assertEqual(migrate(conn, "work"), [])


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
        # a trajectory.db + logbook.db shaped like the live ones, read-only to the kernel
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
        md, meta = compile_context(self.home, self.k.work, self.k.memory, t["id"], "plan")
        self.assertIn("Ship holon", md)
        self.assertIn("Never import harness code", md)
        self.assertIn("Kernel thoughts", md)
        self.assertEqual(meta["degraded"], [])
        small, meta = compile_context(self.home, self.k.work, self.k.memory, t["id"], "plan", budget=600)
        self.assertLessEqual(len(small), 600 + 400)  # body within budget; gap report appended
        self.assertTrue(meta["degraded"])

    def test_missing_sources_degrade_not_fail(self):
        t = self.add(project_ref="traj:nowhere")
        md, meta = compile_context(self.home, self.k.work, self.k.memory, t["id"], "plan", run_id="run_x")
        self.assertIn("trajectory.db absent", meta["degraded"])
        self.assertIn("Context gaps", md)
        row = self.k.mconn.execute("SELECT degraded FROM context_log WHERE run_id='run_x'").fetchone()
        self.assertIn("trajectory", row[0])


class Agnosticism(unittest.TestCase):
    """The kernel's defining guarantees: stdlib only, and no harness named in code."""

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

    def test_no_harness_named_in_kernel_code(self):
        offenders = []
        for f in PKG.rglob("*.py"):
            text = f.read_text().lower()
            for word in ("hermes", "zeroclaw", "operant", "~/.hermes", "claude"):
                if word in text:
                    offenders.append(f"{f.relative_to(PKG)}: {word}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
