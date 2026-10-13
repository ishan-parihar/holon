"""R1: `holon lint` (artifacts) and `holon doctor` (live state) catch what they claim to catch."""

from pathlib import Path
from unittest import mock

from holon.integrity import doctor, lint
from tests.helpers import KernelCase


def rules(findings, severity="error"):
    return {f["rule"] if "rule" in f else f["check"] for f in findings if f["severity"] == severity}


class Lint(KernelCase):
    def test_the_distribution_passes_its_own_rubric(self):
        self.k.auth.init_chair_key()
        findings = lint.run(self.k)
        self.assertEqual([f for f in findings if f["severity"] == "error"], [])
        self.assertEqual([f for f in findings if f["severity"] == "warning"], [])

    def test_the_regression_ledger_cannot_lose_a_guard(self):
        d = self.tmp / "dist"
        (d / "tests").mkdir(parents=True)
        (d / "tests" / "test_x.py").write_text("def test_kept():\n    pass\n")
        (d / "AGENTS.md").write_text("# x\n\n## Regression ledger\n\n| Found | Defect | Guard |\n|---|---|---|\n"
                                     "| a | b | `test_kept` |\n| a | c | `test_gone` |\n| a | d | none |\n")
        f = lint.Findings()
        with mock.patch.object(lint, "DIST", d):
            lint._guards(f)
        msgs = " ".join(x["message"] for x in f)
        self.assertIn("test_gone", msgs)
        self.assertIn("no guard test", msgs)
        self.assertNotIn("test_kept", msgs)

    def test_a_stub_skill_fails_the_depth_rubric(self):
        self.k.skills.new("stub", "operation", "Does a thing")
        self.assertTrue({"depth.trigger", "depth.size", "depth.runnable"} <= rules(lint.run(self.k, ["depth"])))

    def test_broken_command_references_are_caught(self):
        self.k.skills.new("legacy", "operation", "Use when testing. Old habits")
        p = self.home.root / "skills" / "legacy" / "SKILL.md"
        p.write_text(p.read_text() + "\nRun `$holon claim --duty plan` and `$holon frobnicate now`.\n")
        msgs = [f["message"] for f in lint.run(self.k, ["ref"]) if f["rule"] == "ref.cli"]
        self.assertTrue(any("--duty" in m for m in msgs) and any("frobnicate" in m for m in msgs), msgs)

    def test_saying_the_same_thing_twice_is_caught(self):
        para = ("Approve only what you would defend to the Chair. Plans you approve that fail verification, "
                "or that the Chair disagrees with on audit, cost you authority; clean results earn it, and "
                "the record is kept per task class.")
        self.k.skills.fork("holon-verifier", file="SKILL.md")
        p = self.home.root / "skills" / "holon-verifier" / "SKILL.md"
        p.write_text(p.read_text() + "\n" + para + "\n")
        self.k.skills.fork("holon-planner", file="SKILL.md")
        q = self.home.root / "skills" / "holon-planner" / "SKILL.md"
        q.write_text(q.read_text() + "\n" + para + "\n")
        self.assertIn("dry.paragraph", rules(lint.run(self.k, ["dry"])))

    def test_edited_migrations_and_profileless_departments_are_caught(self):
        self.k.wconn.execute("UPDATE schema_migrations SET checksum='x' WHERE name='0001_init.sql'")
        self.k.wconn.execute("UPDATE departments SET genome=NULL WHERE slug='eng'")
        found = [f for f in lint.run(self.k, ["schema"]) if f["severity"] == "error"]
        self.assertTrue(any(f["rule"] == "schema.migration" for f in found))
        self.assertTrue(any(f["rule"] == "schema.genome" and f["where"] == "dept:eng" for f in found))

    def test_unknown_pack_is_rejected(self):
        with self.assertRaises(ValueError):
            lint.run(self.k, ["vibes"])


class Doctor(KernelCase):
    def test_a_healthy_instance_is_clean_once_the_chair_key_exists(self):
        self.assertIn("security.auth", rules(doctor.run(self.k)))
        self.k.auth.init_chair_key()
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        self.k.submit(c.run["id"], self.exec_env())
        self.k.verify(t["id"], "pass", "ok")
        self.assertEqual(doctor.run(self.k), [])

    def test_a_rewritten_audit_trail_is_detected(self):
        self.add(dept="eng")
        seq = self.k.wconn.execute("SELECT min(seq) FROM events WHERE hash IS NOT NULL").fetchone()[0]
        self.k.wconn.execute("UPDATE events SET note='nothing to see' WHERE seq=?", (seq,))
        self.assertIn("audit.chain", rules(doctor.run(self.k)))

    def test_graph_invariants(self):
        t = self.add(dept="eng")
        self.k.wconn.execute("UPDATE tasks SET status='cancelled' WHERE parent_id=?", (t["id"],))
        c = self.k.claim("plan", self.hod, task_id=self.add("other", dept="eng")["id"])
        self.k.wconn.execute("UPDATE runs SET status='abandoned' WHERE id=?", (c.run["id"],))
        found = rules(doctor.run(self.k))
        self.assertTrue({"graph.stuck", "graph.claimed_run"} <= found, found)

    def test_separation_of_duties_is_audited_in_history(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        self.k.submit(c.run["id"], self.exec_env())
        self.k.wconn.execute("UPDATE outputs SET verified=1, verified_by=? WHERE kind='execute'", (f"emp:{self.dev1}",))
        self.assertIn("separation.verify", rules(doctor.run(self.k)))


class Operations(KernelCase):
    def test_the_heartbeat_backs_the_instance_up(self):
        self.k.config["healing"]["backup_every_h"] = 24
        took = [x for x in self.k.tick()["triggered"] if "backup" in x]
        self.assertEqual(len(took), 1)
        from holon.integrity import backup
        self.assertEqual(backup.verify(took[0]["backup"])["corrupt"], [])
        self.assertFalse([x for x in self.k.tick()["triggered"] if "backup" in x], "once per interval")

    def test_backup_and_restore_round_trip(self):
        from holon.integrity import backup
        keep = self.add("keep me", dept="eng")
        out = backup.backup(self.k, str(self.tmp / "bk"))
        self.assertEqual(backup.verify(out["path"])["corrupt"], [])
        self.add("made after the backup", dept="eng")
        self.k.work.cancel(keep["id"], "oops")
        backup.restore(self.k, out["path"])
        titles = {t["title"]: t["status"] for t in self.k.work.list()}
        self.assertEqual(titles, {"keep me": "open"})
        self.assertNotIn("audit.chain", {f["check"] for f in doctor.run(self.k)}, "the restored trail is intact")
        (Path(out["path"]) / "work.db").write_bytes(b"tampered")
        self.assertIn("work.db", backup.verify(out["path"])["corrupt"])

    def test_budget_caps_hold_work_on_the_board(self):
        self.k.config["budget"] = {"max_running_per_dept": 1, "dept": {}}
        self.add("one", dept="eng")
        self.add("two", dept="eng")
        self.assertIsNotNone(self.k.claim("plan", self.hod))
        self.assertIsNone(self.k.claim("plan", self.hod), "eng is at its cap of one running run")
        self.assertIn("budget.cap", {f["check"] for f in doctor.run(self.k)})
        self.k.config["budget"] = {"max_running_per_dept": 0, "dept": {"eng": {"max_running_per_dept": 2}}}
        self.assertIsNotNone(self.k.claim("plan", self.hod), "a department override raises the cap")
        self.assertEqual(self.k.metrics("eng")["budget"]["running"], 2)
