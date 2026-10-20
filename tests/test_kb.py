"""Knowledge bases (plan docs/plans/2026-10-19-holon-knowledge-layer.md, K1 to K4): seeding, the seat policy,
commits, drafts, the CLI and MCP surfaces, and the department's context section."""

import hashlib
import io
import json
import os
import subprocess
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from holon import cli, mcp
from holon.capabilities import context, kb
from holon.integrity import backup
from holon.execution.security import Principal
from holon.execution.service import Kernel
from tests.helpers import KernelCase


class KnowledgeBases(KernelCase):
    def setUp(self):
        super().setUp()
        self.chair = Principal("chair")
        self.cy = Principal("employee", self.dev1)  # eng
        self.ada = Principal("employee", self.ceo)  # executive
        self.slug = "pipeline"
        kb.seed(self.k, self.chair, "venture-pipeline", self.slug, "eng")

    def git_log(self, n: int) -> list[str]:
        repo = self.home.root / "kb" / self.slug
        out = subprocess.run(["git", "log", f"-{n}", "--format=%an|%s"], cwd=repo, capture_output=True, text=True)
        return out.stdout.splitlines()

    def cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--home", str(self.home.root), *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_seed_binds_to_one_department(self):
        self.assertEqual(kb.listing(self.k, self.cy)[self.slug]["dept"], "eng")
        self.assertEqual(kb.listing(self.k, self.ada), {})
        self.assertIn(self.slug, kb.listing(self.k, self.chair))
        self.assertIn("lint: clean", kb.run(self.k, self.cy, self.slug, "lint", []))
        with self.assertRaises(PermissionError):
            kb.run(self.k, self.ada, self.slug, "lint", [])

    def test_a_write_is_a_commit_by_the_seat_that_made_it(self):
        kb.run(self.k, self.cy, self.slug, "new", ["application", "--title", "Staff engineer", "--set", "employer=Acme"])
        self.assertEqual(self.git_log(1), [f"emp:{self.dev1}|new application --title Staff engineer --set employer=Acme"])

    def test_human_moves_are_refused_by_holon_including_prefixes(self):
        before = self.git_log(1)
        for flag in ("--human", "--hum", "--human=Ishan"):
            with self.assertRaises(PermissionError):
                kb.run(self.k, self.cy, self.slug, "move", ["x", "closed-won", flag, "Ishan"])
        self.assertEqual(self.git_log(1), before)

    def test_genome_refuses_a_human_only_state_without_sign_off(self):
        ou = json.loads(kb.run(self.k, self.cy, self.slug, "new", ["outcome", "--set", "venture=job"]))["id"]
        with self.assertRaisesRegex(kb.KbError, "human-only"):  # `won` is human-only: no sign-off, no move
            kb.run(self.k, self.cy, self.slug, "move", [ou, "won"])

    def test_two_writers_at_once_both_land(self):
        errors = []

        def write(title: str) -> None:
            k = Kernel(self.home)  # sqlite connections are per thread
            try:
                kb.run(k, self.cy, self.slug, "new", ["application", "--title", title, "--set", f"employer={title}"])
            except Exception as e:  # reported by the assertion below
                errors.append(e)
            finally:
                k.close()

        threads = [threading.Thread(target=write, args=(f"Role {i}",)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.git_log(10)), 3)  # the seed and the two writes

    def test_drafts_are_checked_and_only_the_chair_promotes(self):
        self.assertTrue(kb.draft(self.k, self.cy, "pipeline-lite", "venture-pipeline")["pass"])
        self.assertTrue(kb.check(self.k, self.cy, "pipeline-lite")["pass"])
        with self.assertRaises(PermissionError):
            kb.promote(self.k, self.cy, "pipeline-lite")
        with self.assertRaises(PermissionError):
            kb.draft(self.k, Principal("anonymous"), "other-lite", "venture-pipeline")
        promoted = kb.promote(self.k, self.chair, "pipeline-lite")
        self.assertTrue(Path(promoted["file"]).exists())
        kb.seed(self.k, self.chair, "pipeline-lite", "lite", "eng")
        self.assertIn("lite", kb.listing(self.k, self.chair))

    def test_the_department_wake_stays_within_the_cap(self):
        # measured: a fresh venture-pipeline `genome wake` is 558 chars, under WAKE_CAP
        text = kb.wake_for_dept(self.home, "eng")
        self.assertIn(f"### kb/{self.slug}\n", text)
        self.assertLessEqual(len(text.split(f"### kb/{self.slug}\n", 1)[1]), kb.WAKE_CAP)
        self.assertEqual(kb.wake_for_dept(self.home, "executive"), "")

    def test_context_adds_the_section_and_degrades_on_a_broken_registry(self):
        bundle = context.Bundle()
        context._kb(self.home, "eng", bundle)
        self.assertEqual([t for t, _ in bundle.sections], ["Knowledge bases (your department)"])
        broken = Path(self.home.root / "kb" / "registry.json")
        broken.write_text("{")
        bundle = context.Bundle()
        context._kb(self.home, "eng", bundle)
        self.assertEqual(bundle.sections, [])
        self.assertIn("knowledge bases unavailable", bundle.degraded[0])

    def test_a_stage_in_the_department_compiles_with_its_kb(self):
        t = self.add("Ship the pipeline", dept="eng")
        stage = self.k.work.stages(t["id"])[0]
        md, meta = context.compile_context(self.home, self.k.work, self.k.memory, stage["id"])
        self.assertIn(f"### kb/{self.slug}", md)
        self.assertIn("Knowledge bases (your department)", meta["sections"])

    def test_the_instance_backup_carries_the_kbs(self):
        out = backup.backup(self.k, str(self.tmp / "backups"))["path"]
        self.assertTrue((Path(out) / "kb" / self.slug / "genome.toml").exists())
        self.assertEqual(backup.verify(out)["corrupt"], [])

    def test_the_actor_classes_hold_on_the_cli_and_mcp(self):
        key = self.k.auth.issue_employee_key(self.dev1)
        with mock.patch.dict(os.environ, {"HOLON_KEY": key, "HOLON_HOME": str(self.home.root)}):
            self.assertEqual(self.cli("kb", "seed", "venture-pipeline", "other", "--dept", "eng")[0], cli.FORBIDDEN)
            self.assertEqual(self.cli("kb", "blueprint", "promote", "x")[0], cli.FORBIDDEN)
            self.assertEqual(self.cli("kb", "list")[0], 0)
            self.assertEqual(self.cli("kb", "run", self.slug, "move", "x", "closed-won", "--human", "Ishan")[0],
                             cli.FORBIDDEN)
            kb.run(self.k, self.cy, self.slug, "new", ["application", "--title", "Acme Corp", "--set", "employer=Acme Corp"])
            is_error, text = mcp.call("holon_kb_run", {"slug": self.slug, "verb": "find", "tokens": ["application"]})
            self.assertFalse(is_error, text)
            self.assertIn("Acme Corp", text)
        names = {t["name"] for t in mcp.tools()}
        self.assertIn("holon_kb_run", names)
        self.assertNotIn("holon_kb_seed", names)
        self.assertNotIn("holon_kb_blueprint_promote", names)


class GenomePin(unittest.TestCase):
    """The vendored CLI a KB runs and the distribution's own scripts/genome must be the same build.

    They drifted once: scripts/genome was re-pinned twice while holon/vendor/genome.py stayed on the
    previous release, so `holon kb run <slug> lint` linted with older rules than `scripts/genome lint`
    — a KB could pass Holon's check and fail the repo's own gate. The checksum pin catches a
    hand-edited vendored copy; this test catches the two copies drifting apart.
    """

    def test_the_vendored_cli_is_the_same_build_as_scripts_genome(self):
        repo = Path(__file__).resolve().parents[1]
        vendored = repo / "holon" / "vendor" / "genome.py"
        shipped = repo / "scripts" / "genome"
        self.assertTrue(vendored.exists(), "the vendored CLI is missing")
        self.assertTrue(shipped.exists(), "scripts/genome is missing")
        self.assertEqual(vendored.read_bytes(), shipped.read_bytes(),
                         "holon/vendor/genome.py and scripts/genome are different builds: "
                         "copy the newer one over the other and re-pin GENOME_SHA256")

    def test_the_pinned_checksum_matches_the_vendored_cli(self):
        digest = hashlib.sha256(kb.GENOME.read_bytes()).hexdigest()
        self.assertEqual(digest, kb.GENOME_SHA256,
                         f"the vendored CLI's checksum moved: update GENOME_SHA256 (now {digest})")
