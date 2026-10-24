"""Knowledge bases (plan docs/plans/2026-10-19-holon-knowledge-layer.md, K1 to K4): seeding, the seat policy,
commits, drafts, the CLI and MCP surfaces, and the department's context section."""

import hashlib
import io
import json
import os
import shutil
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
        self.assertEqual([t for t, _ in bundle.sections],
                         ["Knowledge bases (your department, then the organization)"])
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
        self.assertIn("Knowledge bases (your department, then the organization)", meta["sections"])

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
        """The distribution's own entry point reads the one copy of the CLI.

        This test started life asserting two byte-identical files, which every re-pin re-
        duplicated: a rule engine whose only job is to keep a copy of another rule engine
        in sync is entropy with a test around it. The copy is gone now -- `scripts/genome`
        is a symlink to `holon/vendor/genome.py` -- so what this asserts is that the link
        still resolves and still reads the same bytes. A re-pin is one write."""
        repo = Path(__file__).resolve().parents[1]
        vendored = repo / "holon" / "vendor" / "genome.py"
        shipped = repo / "scripts" / "genome"
        self.assertTrue(vendored.exists(), "the vendored CLI is missing")
        self.assertTrue(shipped.exists(), "scripts/genome is missing")
        self.assertEqual(vendored.resolve(), shipped.resolve(),
                         "scripts/genome no longer resolves to the vendored CLI: the one copy of "
                         "the genome is at holon/vendor/genome.py, and this must point at it")
        self.assertEqual(vendored.read_bytes(), shipped.read_bytes(),
                         "scripts/genome does not read the vendored CLI's bytes: the symlink is "
                         "broken or a second copy was dropped back into place")

    def test_the_pinned_checksum_matches_the_vendored_cli(self):
        digest = hashlib.sha256(kb.GENOME.read_bytes()).hexdigest()
        self.assertEqual(digest, kb.GENOME_SHA256,
                         f"the vendored CLI's checksum moved: update GENOME_SHA256 (now {digest})")

    def test_the_plan_the_code_cites_exists_and_names_the_phases(self):
        """`holon/capabilities/kb.py` cites `docs/plans/2026-10-19-holon-knowledge-layer.md` as
        its plan. The citation pointed at a file that did not exist: the module's own docstring
        named a plan no reader could open, and the phases it names (K1 to K4) were only in the
        code's section comments. The document exists, and its claims are checked against the
        code — a plan nobody can open is not a plan."""
        repo = Path(__file__).resolve().parents[1]
        plan = repo / "docs" / "plans" / "2026-10-19-holon-knowledge-layer.md"
        self.assertTrue(plan.exists(), "the plan the KB module cites is missing")
        text = plan.read_text()
        for phase in ("K1", "K2", "K3", "K4"):
            self.assertIn(f"**{phase}**", text, f"the plan does not describe {phase}")
        # the verbs the plan names are the ones the kernel accepts
        for verb in kb.READ_VERBS + kb.WRITE_VERBS:
            self.assertIn(verb, text, f"the plan does not name the verb {verb!r}")
        # and the plan's own claims about the seat policy hold
        self.assertIn("The seat policy", text)


class OrgScope(KernelCase):
    """F5 and F6 (2026-10-21 reachability audit): the organization's own knowledge base.

    A KB bound to no department is the distribution's law and history rather than one department's
    material. Every employee must be able to read it — a rule an agent is judged against but cannot
    read is the defect F3 measured — and none may write it, because what every employee reads as law
    is the Chair's to widen.
    """

    def setUp(self):
        super().setUp()
        self.chair = Principal("chair")
        self.cy = Principal("employee", self.dev1)  # eng
        self.ada = Principal("employee", self.ceo)  # executive: another department entirely

    def seed_org(self, slug="org"):
        return kb.seed(self.k, self.chair, "software", slug, None)

    def test_an_org_scoped_kb_is_readable_by_every_employee_and_writable_by_none(self):
        self.seed_org()
        entry = kb._entry(self.home, "org")
        self.assertIsNone(entry["dept"], "an organization KB binds to no department")
        # every principal reads it: the read verb returns without a PermissionError
        for who in (self.cy, self.ada, self.chair):
            kb.run(self.k, who, "org", "ask", ["scope"])
        # and an employee may not write it, at any write verb
        with self.assertRaises(PermissionError):
            kb.run(self.k, self.cy, "org", "new", ["note", "title", "body"])

    def test_an_employee_may_not_write_the_organizations_kb(self):
        self.seed_org()
        for verb in kb.WRITE_VERBS:
            with self.subTest(verb=verb):
                try:
                    kb.run(self.k, self.cy, "org", verb, ["x", "y"])
                except PermissionError as e:
                    self.assertIn("Chair", str(e))
                else:
                    self.fail(f"{verb} on an org KB must be the Chair's alone")

    def test_a_department_kb_stays_bound_to_its_department(self):
        """The fix is scoped, not a widening: a department's KB is still invisible to other
        departments. Widening the org scope must not have widened a department's."""
        kb.seed(self.k, self.chair, "software", "eng", "eng")
        with self.assertRaises(PermissionError):
            kb.run(self.k, self.ada, "eng", "ask", ["scope"])
        kb.run(self.k, self.cy, "eng", "ask", ["scope"])   # its own department, fine

    def test_a_unique_slug_prefix_resolves_so_one_call_is_enough(self):
        """F3's second half: `kb run org ask "…"` must be one call. The resolution is a unique
        prefix, and an ambiguous one is refused rather than silently picking."""
        self.seed_org()
        self.assertEqual(kb._entry(self.home, "o")["blueprint"], "software")
        self.assertEqual(kb._entry(self.home, "org")["blueprint"], "software")
        kb.seed(self.k, self.chair, "software", "ops-notes", "eng")
        with self.assertRaises(kb.KbError):
            kb._entry(self.home, "o")   # org and ops-notes both start with `o`
        with self.assertRaises(LookupError):
            kb._entry(self.home, "nope")

    def test_the_brief_carries_the_organizations_own_wake(self):
        """F6: `genome wake`'s register and health line — the highest-value context for a body
        working in this repository — was computed for free and carried by no brief."""
        self.seed_org()
        bundle = context.Bundle()
        context._kb(self.home, "eng", bundle)
        titles = [t for t, _ in bundle.sections]
        self.assertTrue(any("the organization" in t for t in titles), titles)
        body = dict(bundle.sections)["Knowledge bases (your department, then the organization)"]
        self.assertIn(f"### kb/org", body)

    def test_recall_joins_the_law_the_distribution_states(self):
        """F3: `recall` joined memory, episodes, skills, playbooks and gold standards, and reached
        none of the documents that state the rules an agent is audited against. It now answers both
        halves of one question in one call."""
        self.seed_org()
        docs = self.home.root / "kb" / "org" / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        src = Path(__file__).resolve().parents[1] / "docs" / "BYLAWS.md"
        shutil.copyfile(src, docs / "BYLAWS.md")
        # `ask` scans the files the agent contract points at, so the KB names its law there
        agents = self.home.root / "kb" / "org" / "AGENTS.md"
        agents.write_text("| what a department may reach | `docs/BYLAWS.md` |\n")
        hits = self.k.kb_search("what a department may reach")
        self.assertTrue(hits, "the KB search found nothing")
        self.assertIn("docs/BYLAWS.md:", hits[0])
        self.assertRegex(hits[0], r"\d+-\d+")
        # and through the CLI surface an agent actually calls
        from holon import cli
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["--home", str(self.home.root), "--json", "recall", "what a department may reach"])
        markdown = json.loads(out.getvalue())["markdown"]
        self.assertIn("Knowledge bases", markdown)
        self.assertIn("BYLAWS.md:", markdown)

    def test_recall_degrades_to_silence_when_no_kb_is_seeded(self):
        """A KB is optional context, never a dependency: recall must still answer from memory,
        episodes and skills with none seeded, and with none it says so."""
        self.assertEqual(self.k.kb_search("anything"), [])
        from holon import cli
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["--home", str(self.home.root), "--json", "recall", "greeting"])
        self.assertNotIn("Knowledge bases", out.getvalue())


class GenomeSingleSource(unittest.TestCase):
    """One CLI, one copy. The genome is a 5893-line rule engine and it lives twice: the kernel's
    knowledge-base layer runs `holon/vendor/genome.py`, the distribution's own lint runs
    `scripts/genome`, and both are 350,867 bytes.

    The existing guard detects the drift but cannot prevent it, so it has been the wrong shape
    from the start: it asserts the two copies are equal, which every re-pin re-duplicates. A rule
    engine whose only job is to keep a copy of another rule engine in sync is entropy with a test.
    The 2026-10-20 ledger row it was written for is real, and the fix is not equality -- it is one
    path.

    The contract: the second copy does not exist as a second copy. One file holds the CLI and
    every consumer resolves it through `kb.GENOME`, so a re-pin is one write the kernel also sees.
    """

    def test_the_genome_cli_exists_exactly_once(self):
        repo = Path(__file__).resolve().parents[1]
        copies = [p for p in (repo / "holon" / "vendor" / "genome.py", (repo / "scripts" / "genome"))
                  if p.exists() and not p.is_symlink()]
        self.assertEqual(1, len(copies),
                         f"the genome CLI is duplicated: {[str(c) for c in copies]}. Keep the "
                         "vendored copy (kb.GENOME's path) and make the other a symlink or a "
                         "resolver, so a re-pin is one write")
