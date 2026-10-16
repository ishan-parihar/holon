"""R1: `holon lint` (artifacts) and `holon doctor` (live state) catch what they claim to catch."""

import contextlib
import io
import json
import os
import types
import unittest
from pathlib import Path
from unittest import mock

from holon import cli as holon_cli
from holon.core.db import now
from holon.execution.service import Kernel
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

    def test_a_secret_in_config_is_flagged_a_key_file_reference_is_not(self):
        """G8: [secrets.dept.<slug>] values are 0600 key-file paths. The secret itself in config,
        or a key file others can read, is an error — config and its backups must never hold values."""
        (self.tmp / "eng.key").write_text("eng-imap-secret\n")
        (self.tmp / "eng.key").chmod(0o600)
        self.home.config_file.write_text(
            self.home.config_file.read_text()
            + f'\n[secrets.dept.eng]\nIMAP_KEY = "{self.tmp / "eng.key"}"\n'
            + 'PLAINTEXT = "hck_the_secret_itself"\n')
        self.k.close()
        self.k = Kernel(self.home)
        errs = {(f["rule"], f["where"]) for f in lint.run(self.k) if f["severity"] == "error"}
        self.assertIn(("secrets.plaintext", "[secrets.dept.eng] PLAINTEXT"), errs,
                      "the secret itself in config is an error")
        self.assertNotIn(("secrets.plaintext", "[secrets.dept.eng] IMAP_KEY"), errs,
                          "a key-file reference is how secrets are configured")
        (self.tmp / "eng.key").chmod(0o644)
        errs = {(f["rule"], f["where"]) for f in lint.run(self.k) if f["severity"] == "error"}
        self.assertIn(("secrets.plaintext", "[secrets.dept.eng] IMAP_KEY"), errs,
                      "a world-readable key file is the same error")

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

    def test_a_doc_with_frontmatter_still_has_its_title_seen(self):
        """The corpus convention is YAML frontmatter (status/date/description) before the title;
        depth.doc must see through it, not demand the fence be the title."""
        d = self.tmp / "doc.md"
        d.write_text("---\nstatus: living\ndate: 2026-10-16\ndescription: a doc with frontmatter\n---\n\n"
                     "# A titled doc\n\n" + ("purpose paragraph with enough words to pass the rubric. " * 20))
        with mock.patch.object(lint, "_docs", return_value=[d]):
            self.assertNotIn("depth.doc", rules(lint.run(self.k, ["depth"])))
            d.write_text("no title at all, just frontmatter-less text that breaks the rubric check")
            self.assertIn("depth.doc", rules(lint.run(self.k, ["depth"])))

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

    def test_the_read_api_is_versioned_and_complete(self):
        """H2 (boundary doc 4.1): every api_* view exists with the columns its api_version
        promises (3: v2 plus the genome views api_units/api_services — U1 — and api_memory on
        memory.db — K-4), and the api pack catches a dropped, reshaped or unknown-version read
        API — a consumer that opened the database read-only must never see a view drift under it
        silently."""
        conn = self.k.wconn
        self.assertEqual(conn.execute("SELECT value FROM api_meta WHERE key='api_version'").fetchone()[0], "3")
        self.assertEqual(lint.run(self.k, ["api"]), [])
        conn.execute("DROP VIEW api_board")
        conn.execute("DROP VIEW api_messages")
        conn.execute("CREATE VIEW api_messages AS SELECT id, ts FROM messages")
        found = lint.run(self.k, ["api"])
        self.assertEqual({f["rule"] for f in found}, {"api.views"})
        self.assertTrue(any("api_board missing" in f["message"] for f in found))
        self.assertTrue(any("api_messages columns ('id', 'ts') do not match api_version 3" in f["message"]
                            for f in found))
        self.k.mconn.execute("DROP VIEW api_memory")
        self.assertTrue(any("api_memory columns" in f["message"] for f in lint.run(self.k, ["api"])),
                        "the memory-side view is checked too")
        conn.execute("UPDATE api_meta SET value='9' WHERE key='api_version'")
        self.assertTrue(any(f["rule"] == "api.version" for f in lint.run(self.k, ["api"])),
                        "a version this Holon does not know is an error, not silence")


class Doctor(KernelCase):
    def test_a_healthy_instance_is_clean_once_the_chair_key_exists(self):
        self.assertIn("security.auth", rules(doctor.run(self.k)))
        self.k.auth.init_chair_key()
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        self.k.submit(c.run["id"], self.exec_env())
        self.k.verify(t["id"], "pass", "ok")
        self.assertEqual([f["check"] for f in doctor.run(self.k)], ["isolation.off"],
                         "healthy, except that agents still run as the kernel's user (a warning until isolated)")

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


class Retention(KernelCase):
    """H-14 (integration audit): nothing was ever collected. gc collects run directories of
    closed work and old messages, keeps what the organization still learns from, and refuses
    to delete without a recent backup."""

    def _closed_work_with_old_run(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        rd = self.home.run_dir(c.run["id"])          # a real harness writes its result to disk
        (rd / "result.json").write_text(json.dumps(self.exec_env()))
        (rd / "trajectory.json").write_text(json.dumps({"steps": [{"tool": "x", "outcome": "y"}]}))
        self.k.submit(c.run["id"], self.exec_env())
        v = self.k.claim("verify", self.dev2)
        self.k.submit(v.run["id"], {"verdict": "pass", "note": "ok"})
        self.assertEqual(self.k.work.get(t["id"])["status"], "done")
        self.k.wconn.execute("INSERT INTO judgments (work_id, playbook, version, criterion, p, passed, build,"
                             " created_at) VALUES (?,?,?,?,?,?,?,?)",
                             (t["id"], None, None, "does the job", 0.9, 1, "test-1",
                              "2026-01-01T00:00:00.000000Z"))
        self.k.wconn.execute("UPDATE runs SET ended_at='2020-01-01T00:00:00.000000Z'")
        self.k.wconn.execute("UPDATE messages SET ts='2020-01-01T00:00:00.000000Z' WHERE EXISTS"
                             " (SELECT 1 FROM messages)")
        self.k.wconn.execute("INSERT INTO messages (id, sender, to_ref, body, kind, ts, task_id)"
                             " VALUES ('msg_old','emp:x','org','an old note','channel',"
                             " '2020-01-01T00:00:00.000000Z',NULL)")
        self.k.wconn.commit()
        return t, c

    def _configure(self):
        text = self.home.config_file.read_text()
        if "[healing]" in text:
            text = text.replace("[healing]\n", f"[healing]\nbackup_to = \"{self.tmp / 'backups'}\"\n")
        else:
            text += f"\n[healing]\nbackup_to = \"{self.tmp / 'backups'}\"\n"
        text += "\n[retention]\nruns_days = 7\nmessages_days = 7\nbackup_max_age_days = 7\n"
        self.home.config_file.write_text(text)
        self.k.close()
        self.k = Kernel(self.home)

    def test_gc_plans_but_never_deletes_on_a_dry_run(self):
        t, c = self._closed_work_with_old_run()
        self._configure()
        from holon.integrity import gc
        p = gc.plan(self.k)
        self.assertEqual(len(p["run_dirs"]), 3, "the work's plan, execute and verify run directories")
        keeps = {r["dir"]: r["keep"] for r in p["run_dirs"]}
        kept = [r for r in p["run_dirs"] if r["keep"]]
        self.assertEqual(len(kept), 1, "only the judged execute run keeps files")
        self.assertIn("result.json", kept[0]["keep"])
        self.assertGreater(p["messages"], 0)
        self.assertTrue((self.home.run_dir(c.run["id"]) / "result.json").exists(), "the dry run deleted nothing")

    def test_apply_refuses_without_a_recent_backup_then_collects(self):
        from holon.integrity import gc, backup
        t, c = self._closed_work_with_old_run()
        self._configure()
        p = gc.plan(self.k)
        with self.assertRaises(PermissionError):
            gc.apply(self.k, p)                     # no backup at all
        backup.backup(self.k, str(self.tmp / "backups"))
        out = gc.apply(self.k, gc.plan(self.k))
        self.assertEqual(out["run_dirs_removed"], 3)
        self.assertEqual(out["run_dirs_trimmed_to_kept_files"], 1, "the judged execute run keeps its files")
        d = self.home.run_dir(c.run["id"])
        self.assertEqual(sorted(f.name for f in d.iterdir()), ["result.json", "trajectory.json"],
                         "the kept run's directory is trimmed to what the organization still learns from")
        self.assertEqual(self.k.wconn.execute("SELECT count(*) FROM messages").fetchone()[0], 0,
                         "old messages pruned")

    def test_doctor_nags_when_configured_retention_is_overdue(self):
        """gc is the Chair's manual act (H-14): doctor says so when it has been longer than the
        configured window — the organization exists nowhere else, and nobody else collects."""
        self._configure()                          # runs_days = 7
        rules = lambda: {(f["check"], f["severity"]) for f in doctor.run(self.k)}
        self.assertIn(("retention.overdue", "warning"), rules(), "never collected: say so")
        self.k.auth._put("gc:last_run", now())
        self.assertNotIn(("retention.overdue", "warning"), rules(), "a fresh collection is quiet")
        self.k.auth._put("gc:last_run", "2020-01-01T00:00:00.000000Z")
        self.assertIn(("retention.overdue", "warning"), rules(), "stale again: say so again")


class KeysList(KernelCase):
    """H-14: the Chair's key dir collects orphans (scratch instances); `holon keys list --orphans`
    names them — deletion stays the Chair's, by hand."""

    def test_keys_list_names_orphans(self):
        d = Path("~/.config/holon/keys").expanduser()
        d.mkdir(parents=True, exist_ok=True)
        stray = d / "conformance-scratch-123.chair"
        stray.write_text("hck_orphan")
        self.addCleanup(stray.unlink)
        args = types.SimpleNamespace(orphans=True, json=True)
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            holon_cli.cmd_keys_list(args, self.k)
        rows = json.loads(buf.getvalue())
        self.assertTrue(all(not r["this_instance"] for r in rows))
        self.assertIn("conformance-scratch-123.chair", {r["file"] for r in rows})


class KeyHygiene(unittest.TestCase):
    """The suite's own footprint (found by the 2026-10-16 red-team pass: 170+ orphan keys):
    no process running these tests may write a Chair key into the operator's real key dir.
    tests.helpers pins HOLON_CHAIR_KEY_FILE for the whole process; KernelCase narrows it
    per test — this holds the floor itself."""

    def test_no_test_process_writes_chair_keys_into_the_real_key_dir(self):
        from tests import helpers
        default = Path("~/.config/holon/keys").expanduser()
        self.assertFalse(helpers.TEST_KEY_FILE.is_relative_to(default),
                         "the suite's key file lives outside the operator's key dir")
        self.assertEqual(os.environ.get("HOLON_CHAIR_KEY_FILE"), str(helpers.TEST_KEY_FILE),
                         "the process floor holds whenever no test has narrowed it")
