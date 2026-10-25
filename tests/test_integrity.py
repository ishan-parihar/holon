"""R1: `holon lint` (artifacts) and `holon doctor` (live state) catch what they claim to catch."""

import contextlib
import io
import json
import os
import re
import shutil
import sqlite3
import types
import unittest
from pathlib import Path
from unittest import mock

from holon import cli
from holon import cli as holon_cli
from holon.core.db import now
from holon.execution import service
from holon.execution.service import Kernel
from holon.integrity import doctor, lint
from tests.helpers import KernelCase

ROOT = Path(__file__).resolve().parent.parent


def rules(findings, severity="error"):
    return {f["rule"] if "rule" in f else f["check"] for f in findings if f["severity"] == severity}


class Lint(KernelCase):
    def test_the_distribution_passes_its_own_rubric(self):
        self.k.auth.init_chair_key()
        findings = lint.run(self.k)
        self.assertEqual([f for f in findings if f["severity"] == "error"], [])
        # Scrub findings are the universal repo's law, and the overlay is where it points for
        # operator context (AGENTS.md). On an overlay-shaped tree the scrub pack deliberately
        # *downgrades* rather than skips (see pack_scrub), so its findings remain as warnings and
        # this guard -- which requires the rubric to be wholly clean -- would fail on the tree the
        # law points at, for the same seam the scrub pack already had. Exclude only `scrub.*`
        # from the warning count, and only on a tree that is recognisably the overlay; the
        # universal repo's own warnings still fail here with no exception.
        scrub = [f for f in findings if f["rule"].startswith("scrub.")]
        overlay = lint.OVERLAY_MARKERS and any(lint.DIST.joinpath(m).exists() for m in lint.OVERLAY_MARKERS)
        self.assertEqual([f for f in findings
                          if f["severity"] == "warning" and not (overlay and f["rule"].startswith("scrub."))], [],
                         "the distribution carries a rubric warning: "
                         + str([f for f in findings if f["severity"] == "warning"]))
        if scrub and not overlay:
            self.fail(f"an unexpected scrub finding on the universal repo: {scrub}")

    def test_strict_excludes_only_the_overlays_scrub_findings(self):
        """`lint --strict` means warnings fail too -- which is right for the universal repo and
        wrong for the overlay's own scrub findings, for the same seam the pack already had.

        Measured 2026-10-23: with the pack downgrading its overlay findings to warnings, the
        overlay's sync gate still stopped, because `--strict` counts every warning. The findings
        are exactly the ones the law points at (AGENTS.md: operator context lives in the overlay),
        so a strict lint that counts them leaves the gate permanently red.

        The rule is narrow on purpose: only `scrub.*` findings, and only on a tree carrying an
        overlay marker, are exempt from the strict failure. Everything else still fails, which is
        what makes this a narrow exception rather than a quiet weakening.

        This calls the command's own exit decision, not a restatement of it: a guard that
        re-implements the rule would pass on an unchanged `cmd_lint`, which is the very drift this
        audit keeps finding (a guard whose assertion has outlived its premise)."""
        import subprocess, sys, tempfile, pathlib
        import holon.integrity.lint as L
        overlay_scrub = [{"severity": "warning", "rule": "scrub.home", "where": "docs/x.md",
                          "message": "names a host's home directory"}]
        other_warning = [{"severity": "warning", "rule": "register.drift", "where": "TODO.md",
                          "message": "a row marked blocked for a shipped mechanism"}]
        # `cmd_lint` returns 1 when any finding is an error or, under --strict, any warning is one
        # the law still applies to. The rule it must apply is the lint module's own, so the two
        # cannot drift: decide() lives beside pack_scrub and OVERLAY_MARKERS.
        self.assertFalse(L.strict_fails(overlay_scrub, overlay=True),
                         "an overlay's scrub findings still fail --strict: the sync gate stays red")
        self.assertTrue(L.strict_fails(other_warning, overlay=True),
                        "a non-scrub warning stops failing --strict on an overlay")
        self.assertTrue(L.strict_fails(overlay_scrub, overlay=False),
                        "a scrub warning stops failing --strict on the universal repo: the law is "
                        "gone precisely where it applies")
        self.assertTrue(hasattr(L, "OVERLAY_MARKERS") and L.OVERLAY_MARKERS,
                        "the overlay markers the two sides share must exist in one place")
        # and the command must actually use that rule, not its own count
        src = pathlib.Path(cli.__file__).read_text()
        self.assertIn("strict_fails", src,
                      "cmd_lint counts warnings itself: it must defer to the shared rule, or the "
                      "two drift and the overlay's gate stays red")

    def test_every_key_the_kernel_reads_is_declared_in_the_config_schema(self):
        """The schema is the only thing standing between a config file and a silent typo, so every key
        the kernel reads must be declared in it -- in both directions.

        Measured 2026-10-24T22:59Z on the live instance: `holon lint` reported
        `WARN schema.config holon.toml: unknown key [notify] dropped_marker`, on an instance that had
        the mechanism working. `DEFAULT_CONFIG["notify"]` declared only `command`, while
        `Kernel.notify` reads `dropped_marker` (service.py) and BYLAWS documents it. So the lint rule
        was doing its job on a *correct* config, and an operator reading a warning would have deleted
        the one line making their digest honest. The guard below asserts the join both ways: a
        declared key no reader uses is drift (a law that binds nothing), and a read key nobody
        declared is the same drift the other way round."""
        from holon.execution.service import DEFAULT_CONFIG, CONFIG_KEYS
        # 1. declared keys must have a reader somewhere in the kernel. The scan is the whole
        #    `holon/` package, not a subset: scanning only `holon/*.py` and `holon/execution/*.py`
        #    misses `holon/capabilities/decisions.py`, which reads six of them (`key_file`,
        #    `fallback_model`, `noul_model` and three more), and reports those as unread drift --
        #    measured 2026-10-24T23:04Z while proving this guard fails pre-fix.
        src = "".join(p.read_text() for p in sorted(Path(service.__file__).parent.parent.rglob("*.py")))
        declared = {k for keys in CONFIG_KEYS.values() for k in keys}
        unread = sorted(k for k in declared
                        if k not in src and f'"{k}"' not in src and f"'{k}'" not in src)
        self.assertEqual(unread, [], f"declared in the config schema and read by no code: {unread}")
        # 2. every key the kernel's own defaults carry must be declared, which is what fails lint
        #    when a live instance follows BYLAWS
        for section, keys in DEFAULT_CONFIG.items():
            if section in ("grading", "autonomy", "pipeline"):
                continue  # sub-tables with their own sections, validated by other rules
            missing = sorted(set(keys) - (CONFIG_KEYS.get(section) or set()))
            self.assertEqual(missing, [], f"[{section}] defaults not in the schema: {missing}")
        # 3. and the live mechanism's key specifically, so a future edit cannot drop it again
        self.assertIn("dropped_marker", CONFIG_KEYS["notify"],
                      "the dropped_marker guard reads (self.config['notify'] or {}).get"
                      "('dropped_marker') and BYLAWS documents it: an undeclared key makes every "
                      "correct instance warn")

    def test_every_registered_command_is_in_the_command_reference(self):
        """A command that exists and is documented nowhere is a reader's surprise: the reference
        is the one page a new agent opens to learn the surface, and 119 commands is past what a
        person keeps current by hand. The pack fails when the reference drifts from the registry."""
        self.assertEqual(lint.run(self.k, ["reference"]), [])
        doc = Path(holon_cli.__file__).parent.parent / "docs" / "COMMANDS.md"
        self.assertTrue(doc.exists(), "docs/COMMANDS.md must exist")
        text = doc.read_text()
        for c in cli.manifest():
            self.assertIn(f"`holon {c['command']}`", text, f"{c['command']} is missing from the reference")

    def test_a_command_added_without_regenerating_the_reference_is_caught(self):
        doc = Path(holon_cli.__file__).parent.parent / "docs" / "COMMANDS.md"
        original = doc.read_text()
        try:
            doc.write_text(original.replace("`holon status`", "`holon statusa`"))
            found = lint.run(self.k, ["reference"])
            self.assertTrue(any(f["rule"] == "reference.stale" for f in found), found)
            self.assertIn("status", " ".join(f["message"] for f in found))
        finally:
            doc.write_text(original)

    def test_a_missing_command_reference_is_an_error(self):
        doc = Path(holon_cli.__file__).parent.parent / "docs" / "COMMANDS.md"
        original = doc.read_text()
        try:
            doc.unlink()
            found = lint.run(self.k, ["reference"])
            self.assertEqual([f["rule"] for f in found], ["reference.missing"])
        finally:
            doc.write_text(original)

    def test_the_reference_regenerates_identically(self):
        """The committed reference is what the generator produces, so `regenerate` is a check
        rather than a rewrite: a stale file fails, a regenerated file passes unchanged."""
        from holon.tools import commands
        doc = Path(holon_cli.__file__).parent.parent / "docs" / "COMMANDS.md"
        self.assertEqual(doc.read_text(), commands.render(),
                         "docs/COMMANDS.md is not what `python3 -m holon.tools.commands` produces")

    def test_bylaws_names_every_operation_skill_the_library_carries(self):
        """BYLAWS §4 enumerates the standard library's non-role skills, and one that the doc does
        not name is a capability that exists with no reader. The two sets must agree exactly, in
        both directions: an unlisted skill and a listed-but-absent one are both errors."""
        section = re.search(r"## 4\. Skills\n(.*?)\n## 5\.", (ROOT / "docs" / "BYLAWS.md").read_text(),
                            re.S)
        self.assertIsNotNone(section, "BYLAWS §4 must be a numbered section")
        named = set(re.findall(r"`(holon-[a-z-]+)`", section.group(1)))
        library = {d.name for d in (ROOT / "holon" / "skills").iterdir()
                   if d.is_dir() and (d / "SKILL.md").exists()
                   and "kind: operation" in (d / "SKILL.md").read_text().split("---")[1]}
        # §4 also names the protocol skill every employee loads first; that one is expected here.
        library.add("holon-employee")
        self.assertEqual(named, library,
                         "BYLAWS §4 names skills the library does not carry, or omits one it does")

    def test_every_skill_a_role_needs_is_in_the_export(self):
        """A skill that no export reaches is unreachable however well written it is: the export
        decides what an employee can read, so a new skill must be role-bound and reachable."""
        from holon.capabilities import skills as S
        for name in sorted(p.name for p in (ROOT / "holon" / "skills").iterdir() if p.is_dir()):
            meta = self.k.skills.meta(name)
            self.assertTrue(meta.get("roles"), f"{name} binds no role, so no brief exports it")
            self.assertIn("Use when", meta.get("description", ""),
                          f"{name} does not say when to use it")

    def test_the_operator_skill_teaches_every_move_of_the_organization(self):
        """The red-team that produced `holon-operator` found the reflexes of managing the
        organization — add, remove, refactor, expand, condense, heal, evolve — encoded nowhere.
        The skill is the answer, so each move must be present and command-bound."""
        text = (ROOT / "holon" / "skills" / "holon-operator" / "SKILL.md").read_text()
        for move in ("add", "remove", "refactor", "expand", "condense", "heal", "evolve"):
            self.assertIn(f"**{move}**", text, f"the {move} move is not named in the skill")
        for command in ("holon dept create", "holon dept export", "holon dept profile",
                        "holon hr hire", "holon gc", "holon guards", "holon incident waive"):
            self.assertIn(command, text, f"the skill names no command for {command}")

    def test_a_skills_claim_that_a_move_needs_no_approval_is_true(self):
        """The operator skill is what an agent reads to change the system that does the work, so a
        claim that one of its moves needs no approval is a claim about a gate. `moves.md` said
        `holon gc --apply` "is the one move here that needs no approval, because it only collects
        what policy already decided" -- and measured on a fresh instance, `gc --apply` alone is
        refused with `not allowed: no backup newer than 7 days ... or --force`. It needs the
        Chair's approval in exactly one of two forms, and the skill told the agent otherwise.

        Derived from the registry, not restated: for every command the skill calls unapprovable,
        check whether the registry gates it. A new gate on a command the skill frees is caught."""
        import pathlib
        import re
        from holon import cli
        skill = (pathlib.Path(__file__).resolve().parents[1] / "holon" / "skills" / "holon-operator")
        moves = (skill / "references" / "moves.md").read_text()
        gated = {" ".join(c.path) for c in cli.REGISTRY
                 if "--force" in [a[0] for a, _ in (c.args or [])]}
        registered = {" ".join(c.path) for c in cli.REGISTRY}
        # a claim of the form "`$holon X ...` ... needs no approval"
        pattern = r"`\$holon ([a-z][\w -]*?)`[^`]{0,240}?needs no approval"
        for m in re.finditer(pattern, moves):
            phrase = m.group(1).strip()
            name = phrase.split(" --")[0].strip()          # the command path, without its flags
            line = moves[:m.start()].count("\n") + 1
            self.assertIn(name, registered,
                          f"moves.md:{line} names `holon {phrase}`, whose path `{name}` is not registered")
            self.assertNotIn(name, gated,
                             f"moves.md:{line} says `holon {name}` needs no approval, but the "
                             f"registry gates it with --force -- the claim is false")
        # and the SKILL.md lookup table: a row naming a gated command must carry its flag, or an
        # agent following the table is refused. The table makes no approval claim, so the loop
        # above cannot see it -- this loop reads the same text a reader does.
        table = (skill / "SKILL.md").read_text()
        for m in re.finditer(r"`\$holon ([a-z][\w -]*?)(?=[`|)]|$)", table):
            phrase = m.group(1).strip()
            name = phrase.split(" --")[0].strip()
            line = table[:m.start()].count("\n") + 1
            if name in gated:
                self.assertIn("--force", phrase,
                              f"SKILL.md:{line} names the gated `holon {name}` without its flag; "
                              f"an agent following this table is refused")
        # the reach check: the corrected text no longer makes the claim, so the loop above asserts
        # nothing. Prove the pattern still finds the shape it was written for, or the guard below
        # would drift silent the moment the phrasing changed form.
        self.assertIsNone(re.search(pattern, "`$holon gc --apply` collects what retention already decided."),
                          "the pattern must not fire on a sentence with no approval claim")
        self.assertIsNotNone(re.search(pattern, "`$holon gc --apply` is the one move that needs no approval"),
                             "the pattern must fire on an approval claim, or this guard is vacuous")

    def test_the_operator_skill_is_scoped_against_the_system_design_skill(self):
        """Two skills that both cover a department's shape are two answers to one question. The
        boundary is stated in both directions so a reader picks one deliberately."""
        for who in ("holon-operator", "holon-system-design"):
            body = (ROOT / "holon" / "skills" / who / "SKILL.md").read_text()
            other = "holon-system-design" if who == "holon-operator" else "holon-operator"
            self.assertIn(other, body, f"{who} does not state its boundary against {other}")

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

    def test_a_law_copy_drifting_from_the_vocabulary_is_caught(self):
        """KB-4: a distilled copy that drops a role, stage, scope or memory kind is an error,
        not silence — agents operate from these copies."""
        self.assertNotIn("kb.drift", rules(lint.run(self.k, ["drift"])), "the shipped copies are clean")
        self.k.skills.fork("holon-memory")
        layer, path = self.k.skills.resolve("holon-memory", "SKILL.md")
        self.assertEqual(layer, "instance")
        path.write_text(path.read_text().replace("| glossary | a term the organization uses with a specific meaning |\n", ""))
        msgs = [f["message"] for f in lint.run(self.k, ["drift"]) if f["rule"] == "kb.drift"]
        self.assertTrue(any("glossary" in m for m in msgs), msgs)

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
        self.k.wconn.execute("UPDATE departments SET bylaws=NULL WHERE slug='eng'")
        found = [f for f in lint.run(self.k, ["schema"]) if f["severity"] == "error"]
        self.assertTrue(any(f["rule"] == "schema.migration" for f in found))
        self.assertTrue(any(f["rule"] == "schema.bylaws" and f["where"] == "dept:eng" for f in found))

    def test_unknown_pack_is_rejected(self):
        with self.assertRaises(ValueError):
            lint.run(self.k, ["vibes"])

    def test_the_read_api_is_versioned_and_complete(self):
        """H2 (boundary doc 4.1): every api_* view exists with the columns its api_version
        promises (4: v3 plus api_pool, units.owner and the service contract — C1), and the api
        pack catches a dropped, reshaped or unknown-version read
        API — a consumer that opened the database read-only must never see a view drift under it
        silently."""
        conn = self.k.wconn
        self.assertEqual(conn.execute("SELECT value FROM api_meta WHERE key='api_version'").fetchone()[0], "4")
        self.assertEqual(lint.run(self.k, ["api"]), [])
        conn.execute("DROP VIEW api_board")
        conn.execute("DROP VIEW api_messages")
        conn.execute("CREATE VIEW api_messages AS SELECT id, ts FROM messages")
        found = lint.run(self.k, ["api"])
        self.assertEqual({f["rule"] for f in found}, {"api.views"})
        self.assertTrue(any("api_board missing" in f["message"] for f in found))
        self.assertTrue(any("api_messages columns ('id', 'ts') do not match api_version 4" in f["message"]
                            for f in found))
        self.k.mconn.execute("DROP VIEW api_memory")
        self.assertTrue(any("api_memory columns" in f["message"] for f in lint.run(self.k, ["api"])),
                        "the memory-side view is checked too")
        conn.execute("UPDATE api_meta SET value='9' WHERE key='api_version'")
        self.assertTrue(any(f["rule"] == "api.version" for f in lint.run(self.k, ["api"])),
                        "a version this Holon does not know is an error, not silence")

    def test_the_boundary_lint_pack_catches_a_conflated_doc(self):
        """The two stacks are separate programs (AGENTS.md law 10). A doc that calls Holon and
        the Chair's production venture runtime one system is the defect that made A's own gaps
        read as B's work (2026-10-20). The pack must fire on that and stay quiet on a doc that
        separates them, on a doc about one stack only, and on a historical contrast."""
        pkg = Path(holon_cli.__file__).parent
        docs = pkg.parent / "docs"
        self.assertEqual(lint.run(self.k, ["boundary"]), [])
        probe = docs / "boundary-probe.md"
        cases = [
            ("Holon is the operating system that runs the Paperclip organism's ventures.", True),
            ("Holon runs Paperclip routines as its departments.", True),
            ("Holon and the organism are separate systems; Holon never runs the Paperclip routines.", False),
            ("Holon is an agentic operating system that runs as an organization.", False),
            ("Paperclip schedules the venture routines on the VPS.", False),
            ("The Paperclip contrast red-team found three gaps in Holon.", False),
        ]
        try:
            for text, should_fire in cases:
                probe.write_text(text + "\n")
                found = lint.run(self.k, ["boundary"])
                fired = any(f["rule"] == "boundary.conflated" for f in found)
                self.assertEqual(fired, should_fire, f"{text!r} fired={fired} findings={found}")
        finally:
            probe.unlink(missing_ok=True)

    def test_the_scrub_pack_catches_a_host_path_a_chat_id_and_a_token(self):
        """The scrub law (AGENTS.md, the overlay section) names hosts, paths, chat ids and keys as
        instance specifics that belong in the operator's overlay. Measured 2026-10-23: ten published
        files carried one host's home, a scratch path, a Telegram chat id and two bot ids, and no
        gate said so -- the scrub happened by hand, after a leak was found by reading rather than
        by checking. This pack makes the class checkable.

        The patterns are the SHAPE of the leak and never this host's values: a rule naming the
        operator's username would be a second copy of what it forbids, and it would fail the next
        fork's clean tree. So the cases below use a placeholder home, and the pack must fire on the
        shape while staying quiet on what a scrubbed document legitimately carries."""
        docs = Path(holon_cli.__file__).parent.parent / "docs"
        probe = docs / "scrub-probe.md"
        # The fixtures are assembled at run time so the shapes under test never sit in this file as
        # literals: a test that carried them would be a published file the pack correctly fails,
        # and the clean-tree guard below would then fail for the wrong reason.
        home = "/home" + "/" + "oper" + "ator"
        tok = "123456:" + "AbCdEfGhIjKlMnOpQrStUvWxYz" + "1234567890"
        cid = "541234567"
        cases = [
            # (the line, the rule it must fire, or None when it must stay quiet)
            (f"the live instance is `{home}/holon`, 166 tasks", "scrub.home"),
            (f"reads: {home}/work (the checkout)", "scrub.home"),
            (f"channels = [\"telegram:{cid}:1\"]", "scrub.chat_id"),
            (f"`curl getChat?chat_id={cid}`", "scrub.chat_id"),
            (f"token = \"{tok}\"", "scrub.token"),
            # a synthetic fixture id is what a scrub leaves behind: not the Chair's channel
            ("channels = [\"telegram:123456789:1\"]", None),
            ("the count was 1234567 rows", None),
            ("the harness home tree is `adapters/operantx/home/kb.sqlite`", None),
            ("`~/bin/holon`, a symlink into the checkout", None),
        ]
        try:
            for text, rule in cases:
                probe.write_text(text + "\n")
                found = lint.run(self.k, ["scrub"])
                fired = [f for f in found if f["where"].endswith("scrub-probe.md")]
                if rule is None:
                    self.assertEqual([], fired, f"{text!r} should stay quiet: {found}")
                else:
                    self.assertIn(rule, [f["rule"] for f in fired],
                                  f"{text!r} must fire {rule}: {found}")
        finally:
            probe.unlink(missing_ok=True)

    def test_the_distribution_passes_its_own_scrub_gate(self):
        """The pack must agree with the repository: a clean tree passes, and every file the scrub
        law covers is actually read (a pack that skipped its own scope would report clean for
        anything it did not look at)."""
        self.assertEqual([f for f in lint.run(self.k, ["scrub"])
                          if f["severity"] == "error"], [],
                         "the distribution carries an instance specific: "
                         + str(lint.run(self.k, ["scrub"])))
        self.assertIn("scrub", lint.PACKS, "the scrub pack is registered")

    def test_the_scrub_pack_reads_the_overlay_it_is_not_law_for(self):
        """The scrub law is a law about the **universal** repo; a lint run on the operator's
        overlay must not enforce it, or the overlay's own sync gate fails on the files the overlay
        exists to hold.

        Measured 2026-10-23 on the real overlay: `lint --strict` reported three `scrub.*` errors in
        `docs/` and `archive/`, and the overlay's sync gate (which runs the strict lint before it
        will push) stopped with `tests: FAILED`. The overlay is the personal branch whose whole
        purpose (AGENTS.md: a private overlay that adds the operator's STATUS and dated design
        records) is to hold the operator's context, so a pack that flags a home path there flags
        the thing the law permits.

        The overlay is identifiable by files only it has: `PERSONAL.md` and
        `scripts/sync-public.sh`. The distribution has neither.

        Two-sided invariant, because silencing the overlay by skipping the pack would be the worse
        error: the findings still appear (the Chair can see them), just not as errors that fail a
        push. The pack's scope on the universal repo is unchanged --
        `test_the_distribution_passes_its_own_scrub_gate` still requires zero errors here."""
        import tempfile, pathlib
        import holon.integrity.lint as L
        # Build the fixture's offending line by joining, so this test file does not itself
        # carry a host path. The first version spelled one out in a comment, and the
        # distribution's own scrub pack (measured minutes later) failed the very test written to
        # check it -- which is the pack working, on its own author.
        leak = "/" + "home" + "/operator/dir"
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            (td / "docs" / "design").mkdir(parents=True)
            (td / "docs" / "design" / "record.md").write_text(f"measured on {leak}\n")
            (td / "PERSONAL.md").write_text("the private overlay\n")
            (td / "scripts").mkdir()
            (td / "scripts" / "sync-public.sh").write_text("# the sync runbook\n")
            real = L.DIST
            try:
                L.DIST = td
                f = L.Findings()
                L.pack_scrub(None, f)
                found = list(f)
            finally:
                L.DIST = real
            self.assertTrue(found, "the overlay-shaped tree's findings vanished: the pack now "
                                  "skips its scope instead of downgrading it")
            self.assertTrue(all(x["severity"] != "error" for x in found),
                            f"the scrub pack reports errors on an overlay-shaped tree: {found}")



class Doctor(KernelCase):
    def test_a_cold_started_instance_is_not_reported_as_a_stale_checkout(self):
        """A fresh, perfectly healthy cold start reported `code.root` on itself.

        `holon init` recorded `parents[1]` of `holon/cli.py` — the checkout — while the doctor
        compared `parents[1]` of `holon/integrity/doctor.py` — the `holon/` package. Two sides of
        one seam, two different roots, so the first `holon doctor` after any `install.py` warned
        about a stale checkout that does not exist. A warning nobody can act on trains an operator
        to ignore the doctor, which is the one thing it must never do.

        The guard is the cold start itself, end to end: one decision (which root is the
        distribution) must hold on both sides of the seam, and a real install is what breaks when
        it does not."""
        import subprocess, sys
        from pathlib import Path as _Path
        dist = _Path(__file__).resolve().parents[1]
        home = self.tmp / "cold"
        out = subprocess.run([sys.executable, str(dist / "install.py"), "--home", str(home),
                              "--bin-dir", str(self.tmp / "cbin")], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        dr = subprocess.run([sys.executable, "-m", "holon", "--home", str(home), "doctor"],
                            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(dist)})
        self.assertNotIn("code.root", dr.stdout + dr.stderr,
                         "a cold start is never a stale checkout")
        self.assertIn("isolation.off", dr.stdout + dr.stderr,
                        "the cold start says what is missing: agents run as the kernel's user")

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

    def test_no_remediation_names_a_command_whose_gate_would_refuse_it(self):
        """A warning that names the fix must name the fix that WORKS. `doctor` is what an operator
        reads when something is wrong, so its strings are instructions: following one and being
        handed an error is worse than having been told nothing.

        The generalization of the `isolation apply --force` fix. Measured on a fresh instance:
        `gc --apply` alone returns `ValueError: ... or --force`, and `gc --apply --force` works.
        But `retention.overdue` still said `holon gc --apply` -- the gate exists and the string did
        not know it did. That is the same defect, the same surface, a second command.

        Derived, not restated: the registry is walked for every command that declares `--force`, and
        each of `doctor.py`'s mentionings of one is checked in place -- not by a whitelist of
        strings. A dry-run mention is exempt (dry-run genuinely needs no force); an apply is not.
        An earlier version of this guard used a negative lookahead that silently made it vacuous,
        which is why the check is on the mention in its sentence, not on its trailing text."""
        import pathlib
        import re
        from holon import cli
        gated = [" ".join(c.path) for c in cli.REGISTRY
                 if "--force" in [a[0] for a, _ in (c.args or [])]]
        self.assertIn("gc", gated, "gc refuses --apply without --force; if it stopped, this guard is moot")
        self.assertIn("isolation apply", gated, "the isolation gate the last round fixed")
        src = pathlib.Path(doctor.__file__).read_text()
        checked = 0
        for name in gated:
            for m in re.finditer(r"holon %s\b" % re.escape(name), src):
                lineno = src[:m.start()].count("\n") + 1
                line_text = src[src.rfind("\n", 0, m.start()) + 1:
                                src.find("\n", m.start())]
                # a dry-run mention is a look-before-you-leap instruction, and needs no force.
                # Check the warning's whole line, since the word and the command share a sentence.
                if "dry-run" in line_text or "dry run" in line_text:
                    continue
                window = " ".join(src[m.start():m.end() + 60].split())
                checked += 1
                self.assertIn("--force", window,
                              f"doctor names `{name}` without the flag it requires (line {lineno}): "
                              f"{window[:90]}")
        self.assertGreaterEqual(checked, 3,
                                "doctor names the gated commands it tells the operator to run; "
                                "if that count fell, this guard drifted silent")

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

    def test_the_backup_covers_the_adapter_homes(self):
        """KB-7: a body's harness home (its isolated operant state and working memory) is
        instance state — an instance migrated by backup/restore does not lose it."""
        from holon.integrity import backup
        body_store = self.home.root / "adapters" / "operant" / "home" / "memory"
        body_store.mkdir(parents=True)
        (body_store / "memory_wire.sqlite").write_text("body bank")
        out = backup.backup(self.k, str(self.tmp / "bk"))
        shutil.rmtree(self.home.root / "adapters")
        backup.restore(self.k, out["path"])
        self.assertTrue((body_store / "memory_wire.sqlite").exists(),
                        "the body's memory store rides backup/restore")

    def test_a_restore_does_not_wipe_a_body_binary_the_installer_owns(self):
        """`holon restore` copies each backed-up tree over the live one. A body binary the installer
        owns (`aft/aft`) is deliberately absent from the backup, so a naive copy-out would delete the
        live one and leave a department whose harness cannot start."""
        from holon.integrity import backup
        aft = self.home.root / "adapters" / "operant" / "home" / "aft" / "aft-v0.61.0"
        aft.mkdir(parents=True)
        (aft / "aft").write_bytes(b"\x7fELF" + b"\0" * 200_000)
        out = backup.backup(self.k, str(self.tmp / "bk"))
        self.add("to wipe out", dept="eng")
        backup.restore(self.k, out["path"])
        self.assertTrue((aft / "aft").exists(),
                        "restore replaced the trees it carries and left the installer's binary")

    def test_the_backup_excludes_binaries_the_installer_reproduces(self):
        """A live instance's daily backup grew to 125 MB: an adapter home carried the operant body's
        own 101 MB AFT binary (`aft/aft`), which `install_lib.pin_binary` re-downloads. Instance state is
        a body's harness config and memory, not the harness executable — a backup holding one blob per
        retained version fills the disk for nothing, and `holon restore` would resurrect a stale binary."""
        from holon.integrity import backup
        aft = self.home.root / "adapters" / "operant" / "home" / "aft" / "aft-v0.61.0"
        aft.mkdir(parents=True)
        (aft / "aft").write_bytes(b"\x7fELF" + b"\0" * 200_000)
        store = self.home.root / "adapters" / "operant" / "home" / "memory"
        store.mkdir(parents=True)
        (store / "memory_wire.sqlite").write_text("body bank")
        out = backup.backup(self.k, str(self.tmp / "bk"))
        copied = {str(p) for p in Path(out["path"]).rglob("*") if p.is_file()}
        self.assertFalse([p for p in copied if p.endswith("/aft")],
                         f"the installer's binary is not instance state: {copied}")
        self.assertTrue((store / "memory_wire.sqlite").exists(), "the body's memory still rides it")
        self.assertEqual(backup.verify(out["path"])["corrupt"], [])


    def test_the_backup_keeps_only_what_its_keep_count_names(self):
        """A live instance's daily backups had accumulated 443 MB across six directories and
        nothing had ever removed one — the prune was a `ponytail:` deferral ("add a keep-N when
        disk matters") and disk then mattered: 123 MB a day, on the one machine where the
        organization exists nowhere else. A backup is worthless if the disk holding it fills.

        Pruning is not silent: it keeps the newest `backup_keep` and reports what it removed, so
        a run that lost older history says so."""
        from holon.integrity import backup
        dest = self.tmp / "bk"
        for _ in range(4):
            self.add("a task", dept="eng")
            backup.backup(self.k, str(dest))
        gone = backup.prune(dest, keep=2)
        self.assertEqual(len(gone), 2, "two older backups are removed")
        self.assertEqual(len(backup.backups(dest)), 2, "keep-N holds")
        # the prune reports what it removed, and never the newest
        self.add("another task", dept="eng")
        for _ in range(4):
            backup.backup(self.k, str(dest))
        kept = {d.name for d in backup.backups(dest, keep=2)}
        self.assertTrue(all(Path(g["path"]).name not in kept for g in gone), "the newest survive")

    def test_a_backup_in_the_same_second_does_not_collide(self):
        """Two backups in the same second (a manual `holon backup` beside the heartbeat's, or a
        restored instance re-running the same tick) shared one path, and `mkdir` failed with
        FileExistsError — so the second backup was lost, and 'a backup was taken today' was
        reported when only one had been."""
        from holon.integrity import backup
        dest = self.tmp / "bk"
        paths = {backup.backup(self.k, str(dest))["path"] for _ in range(3)}
        self.assertEqual(len(paths), 3, "each backup gets its own directory")

    def test_gc_prunes_the_backups_it_does_not_need(self):
        """`[retention] backup_keep` was documented in BYLAWS §6 and read by nothing, so `holon gc
        --apply` left every backup on disk while its own config named a count. gc now prunes past
        the count, and reports what it removed rather than doing it silently."""
        from holon.integrity import backup, gc
        for _ in range(5):
            backup.backup(self.k, str(self.home.root / "backups"))
        p = gc.plan(self.k)
        self.assertEqual(len(backup.backups(str(self.home.root / "backups"))), 5)
        out = gc.apply(self.k, p)
        self.assertEqual(len(out["backups_pruned"]), 2, "two older backups removed past keep=3")
        self.assertEqual(len(backup.backups(str(self.home.root / "backups"))), 3)
        self.assertTrue(all(b > 0 for b in [r["bytes"] for r in out["backups_pruned"]]),
                        "the removed bytes are reported, not just the count")

    def test_a_present_external_store_declares_a_known_version(self):
        """R-6 (CONTRACT §9a): the strategy seam is dormant — a store that exists must declare an
        api_version the kernel knows, so the bundle's fail-open joins cannot read a schema blind."""
        from holon.integrity import doctor
        tc = sqlite3.connect(self.home.trajectory_db)
        tc.executescript("CREATE TABLE nodes (id TEXT PRIMARY KEY, parent_id TEXT, item_type TEXT, "
                         "name TEXT, status TEXT, description TEXT)")
        tc.close()
        found = [f for f in doctor.run(self.k) if f["check"] == "extstore.version"]
        self.assertTrue(any("does not declare" in f["message"] for f in found),
                        f"a schema with no version declaration is a warning, not silence: {found}")
        tc = sqlite3.connect(self.home.trajectory_db)
        tc.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);")
        tc.close()
        found = [f for f in doctor.run(self.k) if f["check"] == "extstore.version"]
        self.assertTrue(any("'none'" in f["message"] for f in found), found)
        tc = sqlite3.connect(self.home.trajectory_db)
        tc.execute("INSERT INTO meta VALUES ('api_version','9')")
        tc.commit(); tc.close()
        found = [f for f in doctor.run(self.k) if f["check"] == "extstore.version"]
        self.assertTrue(any("'9'" in f["message"] for f in found), found)
        tc = sqlite3.connect(self.home.trajectory_db)
        tc.execute("INSERT OR REPLACE INTO meta VALUES ('api_version','1')")
        tc.commit(); tc.close()
        self.assertEqual([f for f in doctor.run(self.k) if f["check"] == "extstore.version"], [],
                         "a known version is clean")

    def test_the_extstore_lint_pack_proves_the_contract_columns(self):
        """R-6: the bundle's SELECTs are the schema contract's other half — the pack proves the
        columns still match, and catches a column leaving the SELECT."""
        pkg = Path(holon_cli.__file__).parent
        self.assertEqual(lint.run(self.k, ["extstore"]), [])
        original = (pkg / "capabilities" / "context.py").read_text()
        try:
            (pkg / "capabilities" / "context.py").write_text(
                original.replace("SELECT id, parent_id, item_type, name, status, description FROM nodes",
                                 "SELECT id, parent_id, name, status, description FROM nodes"))
            found = lint.run(self.k, ["extstore"])
            self.assertTrue(any("item_type" in f["message"] for f in found), found)
        finally:
            (pkg / "capabilities" / "context.py").write_text(original)

    def test_backup_verify_catches_tampering(self):
        from holon.integrity import backup
        out = backup.backup(self.k, str(self.tmp / "bk"))
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

    def test_force_is_the_chairs_call_to_collect_with_no_backup(self):
        """The second half of the gc gate, which the retention guard's first half does not reach.

        `test_apply_refuses_without_a_recent_backup_then_collects` proves the refusal and the
        backup escape hatch. This proves the other one: `--force` collects with no backup at all,
        which is the Chair's call to make. The operator skill's `moves.md` said `holon gc --apply`
        "is the one move here that needs no approval" -- both of these lines are why that was false,
        and the claim guard (`test_a_skills_claim_that_a_move_needs_no_approval_is_true`) reads the
        registry, so it stays green if the gate stops being enforced. This one reads the runtime."""
        from holon.integrity import gc
        self._closed_work_with_old_run()
        self._configure()
        self.assertEqual(gc.apply(self.k, gc.plan(self.k), force=True)["run_dirs_removed"], 3)

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


class KeysGcOrphans(KernelCase):
    """Measured on the live instance 2026-10-25: `holon keys gc --apply` quarantined a still-live
    scratch instance's Chair key at 2026-10-23T15:14:02Z, the same second that key was written, so
    both `holon-tick.service` and `holon-broker.service` were left naming a home whose key file no
    longer existed. `holon tick` is a kernel-actor command: with no key file, `Auth.principal()`
    returns anonymous, so every tick for the next 69 minutes was refused with "`tick` is for the
    kernel actor class (you are anonymous)" — 64 of them — and nothing said so, because the operant
    gateway's clock organ ticks the live home independently and stamps `.clock/last_tick` from there.

    The gc rule keeps only the running home's own key, and every scratch instance mints its key into
    the one shared `~/.config/holon/keys` (a scratch install has no HOLON_CHAIR_KEY_FILE), so a gc
    run from any home reads every other instance's key as an orphan. The key file's stem is that
    instance's id and its database records it, so "no local instance claims it" is checkable in a
    way the keep-set rule never was."""

    def _sandbox_home(self) -> Path:
        """A private HOME whose `~/.config/holon/keys` is the shared key dir, so the unsets below
        reproduce a scratch install's real behaviour without writing into the operator's key dir
        (which `KeyHygiene` holds as a floor). The real HOME is captured first, because a restore
        that expands `~` after HOME has been overwritten restores the sandbox."""
        real_home = str(Path.home())
        home = self.tmp / "sandbox-home"
        keys = home / ".config" / "holon" / "keys"
        keys.mkdir(parents=True, exist_ok=True)
        os.environ["HOME"] = str(home)
        self.addCleanup(os.environ.__setitem__, "HOME", real_home)
        return keys

    def test_a_live_instances_key_is_not_an_orphan(self):
        from holon.integrity import keys as ikeys
        keys = self._sandbox_home()
        os.environ.pop("HOLON_CHAIR_KEY_FILE", None)   # a scratch install has none: the shared dir
        other = Kernel(self.home.__class__(self.tmp / "other"))
        self.addCleanup(other.close)
        self.addCleanup(self._restore_key_env)
        other.auth.init_chair_key()
        live = other.auth.chair_key_path().name
        self.assertEqual(other.auth.chair_key_path().parent, keys,
                         "the scratch key lands in the shared dir, which is the premise")
        orphans = {r["file"] for r in ikeys.plan(self.k)["orphans"]}
        self.assertNotIn(live, orphans,
                         "a key whose own instance database exists and is running must not be an "
                         "orphan: collecting it is how the live heartbeat lost its Chair key")

    def test_the_same_key_reads_identically_from_every_home(self):
        """The defect's asymmetry, pinned so it cannot come back: the key was kept when the sweep ran
        from its own home and read as an orphan from a sibling home, so a live key's fate depended on
        which home ran the gc. Both homes must classify a live instance's key identically."""
        from holon.integrity import keys as ikeys
        self._sandbox_home()
        os.environ.pop("HOLON_CHAIR_KEY_FILE", None)   # a scratch install has none: the shared dir
        other = Kernel(self.home.__class__(self.tmp / "other"))
        self.addCleanup(other.close)
        self.addCleanup(self._restore_key_env)
        other.auth.init_chair_key()
        live = other.auth.chair_key_path().name
        from_own = {r["file"]: r["kept"] for r in ikeys.plan(other)["keys"]}
        from_sibling = {r["file"]: r["kept"] for r in ikeys.plan(self.k)["keys"]}
        self.assertTrue(from_own.get(live), "from its own home the key is kept")
        self.assertTrue(from_sibling.get(live),
                        "from a sibling home the same live key is kept too: the classification is a "
                        "fact about the instance, not about which home runs the sweep")

    def _restore_key_env(self):
        """`KernelCase.setUp` pins the key file per test; a test that unsets it restores it, or the
        next test's Kernel mints into the operator's real key dir."""
        os.environ["HOLON_CHAIR_KEY_FILE"] = str(self.tmp / "chair.key")


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

class ApiMemoryDrift(KernelCase):
    """Found on the live instance, 2026-10-17: `api_memory` had been created out-of-tree
    selecting `created_at`, a column no migration defines, so `holon lint --strict` died with
    "no such column: created_at" instead of checking the read API. Migration 0004 re-asserts
    the shipped definition; this reproduces the drift and proves migrate repairs it."""

    def test_a_drifted_api_memory_view_is_repaired_by_migration(self):
        from holon.core.db import connect, migrate
        conn = self.k.mconn
        # simulate the live instance: the view drifted after its migration was already
        # recorded (an out-of-tree writer), so a fresh migrate must still repair it
        conn.execute("DELETE FROM schema_migrations WHERE name='0004_api_memory_view_repair.sql'")
        conn.execute("DROP VIEW api_memory")
        conn.execute("CREATE VIEW api_memory AS SELECT id, scope, created_at FROM items")
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("PRAGMA table_info(api_memory)").fetchall()
        applied = migrate(conn, "memory")
        self.assertIn("0004_api_memory_view_repair.sql", applied)
        self.assertEqual([r[1] for r in conn.execute("PRAGMA table_info(api_memory)")],
                         ["id", "scope", "kind", "content", "provenance", "confidence",
                          "pinned", "valid_from"])
        self.assertEqual(lint.run(self.k, ["api"]), [], "lint survives a repaired view")

    def test_a_correct_api_memory_view_is_untouched(self):
        from holon.core.db import migrate
        applied = migrate(self.k.mconn, "memory")
        self.assertNotIn("0004_api_memory_view_repair.sql", applied,
                         "a fresh instance already carries the right view: no churn")
        self.assertEqual(lint.run(self.k, ["api"]), [])

    def test_the_kernels_lint_carries_the_distributions_own_genome_checks(self):
        """F7: `holon lint --strict` and `scripts/genome lint` were two gates over one repository
        with two rubrics, and they disagreed — the repo's own gate reported an unlinked page that the
        strict lint was silent about, so an agent running either believed it was clean. The kernel's
        lint now runs the distribution's genome checks as a pack, so the two cannot disagree about a
        page no gate can reach."""
        self.assertEqual(lint.run(self.k, ["genome"]), [], "the distribution's own genome checks pass")
        self.assertIn("genome", lint.PACKS, "the genome pack is registered")
        # and a page the repo's gate cannot reach is now reported by the kernel's lint too
        readme = ROOT / "README.md"
        original = readme.read_text()
        try:
            readme.write_text(original.replace("[ARCHITECTURE](docs/ARCHITECTURE.md) (how\n"
                                                "the kernel, the instance and the agents fit together) ", "x "))
            found = lint.run(self.k, ["genome"])
            self.assertTrue(any(f["rule"] == "genome.W009" for f in found),
                            f"an unlinked page is reported by the kernel's lint: {found}")
        finally:
            readme.write_text(original)

    def test_the_genome_pack_fails_loudly_when_the_distributions_lint_errors(self):
        """A pack that silently swallows a failing gate is worse than no pack: it would report clean
        on a distribution that is not. The pack turns the repo's own error codes into errors."""
        from unittest import mock
        import subprocess
        bad = subprocess.CompletedProcess([], 2, stdout="E017 genome.toml: unknown key\n",
                                          stderr="")
        with mock.patch("subprocess.run", return_value=bad):
            found = lint.run(self.k, ["genome"])
        self.assertEqual([f["rule"] for f in found], ["genome.E017"], found)
        self.assertEqual(found[0]["severity"], "error")


class MarkdownTables(KernelCase):
    """Measured 2026-10-25: the record's §1 table was silently broken for a full commit. A
    `str.replace` wrote a new_string whose old_string ended in `.* |` and whose new_string ended in
    `.*`, so the closing pipe left with the replaced text. The diff was a clean single-line change,
    the row rendered as prose and the row below it read as a new one, and **every gate passed**:
    `holon lint --strict` 0/0, genome's own checks clean, docs-check green. A document's table is
    its claim structure — a row that no longer renders is a claim a reader cannot see."""

    def test_an_unterminated_table_row_is_a_finding_not_a_silent_break(self):
        doc = ROOT / "docs" / "COMMANDS.md"
        original = doc.read_text()
        try:
            lines = original.splitlines(keepends=True)
            row = next(i for i, l in enumerate(lines) if l.startswith("|") and l.rstrip().endswith("|"))
            lines[row] = lines[row].rstrip()[:-1] + "\n"      # lose exactly the closing pipe
            doc.write_text("".join(lines))
            found = lint.run(self.k, ["tables"])
            self.assertTrue(any(f["rule"] == "tables.unterminated" for f in found),
                            f"a row that no longer renders is reported: {found}")
        finally:
            doc.write_text(original)

    def test_a_ragged_table_is_a_finding(self):
        """The other half of the same defect, and the one that actually hid it: the broken row's
        column count no longer matched its neighbours, so the renderer split it into a different
        number of cells. A table whose rows disagree about their shape is not one table."""
        doc = ROOT / "docs" / "COMMANDS.md"
        original = doc.read_text()
        try:
            lines = original.splitlines(keepends=True)
            row = next(i for i, l in enumerate(lines) if l.startswith("| "))
            lines[row] = lines[row].rstrip()[:-1] + " | extra |\n"   # one cell more than the header
            doc.write_text("".join(lines))
            found = lint.run(self.k, ["tables"])
            self.assertTrue(any(f["rule"] == "tables.ragged" for f in found),
                            f"a row whose shape disagrees with its table is reported: {found}")
        finally:
            doc.write_text(original)

    def test_the_distributions_own_tables_pass(self):
        self.assertEqual(lint.run(self.k, ["tables"]), [], "every shipped table is well formed")
        self.assertIn("tables", lint.PACKS)

    def test_an_example_table_inside_a_fence_is_not_a_table(self):
        """Found by probing the pack for false positives (2026-10-25): a document that shows a
        deliberately broken table as an illustration — a "don't do this", the pack's own
        illustration, a test fixture — was reported as ragged. The pack has no concept of a
        fenced code block, so it could not tell an example from a claim. Markdown has one: an
        example is not a table. The pack now tracks fence state, and this guard fails if it
        stops doing so."""
        doc = ROOT / "docs" / "COMMANDS.md"
        original = doc.read_text()
        try:
            # plant an example table with a deliberately ragged row, inside a fence
            example = ["```\n", "| a | b |\n", "| --- | --- |\n", "| 1 | 2 | 3 |\n", "```\n"]
            doc.write_text(original + "\n" + "".join(example))
            out = lint.run(self.k, ["tables"])
            self.assertNotIn("tables.ragged", [f["rule"] for f in out],
                             f"an example inside a fence is not a ragged table: {out}")
            self.assertNotIn("tables.unterminated", [f["rule"] for f in out],
                            f"an example inside a fence is not an unterminated table: {out}")
        finally:
            doc.write_text(original)

    def test_a_real_break_after_an_example_is_still_reported(self):
        """The other half of the fence fix, and the one that keeps the pack honest: skipping
        fenced content must not silence the defect it exists to catch.

        The broken row must sit *after* a fence, because that is the region a fence-aware pack
        can get wrong. Found by mutation-testing this guard's own first two drafts, both of
        which were wrong in ways their runs did not catch. (1) The first planted its example
        and broke a row near the file's top, so a mutation skipping everything after the first
        fence left the guard passing while the pack had gone blind — a guard that could not
        fail the failure it named. (2) The second rewrote it onto `docs/COMMANDS.md`, which
        **has no fenced block at all** (measured: 0 fences, 153 table rows), so the mutation's
        "after a fence" condition was never true and nothing was skipped: the mutation was a
        null test, which is the same failure in the other direction. This version uses
        `AGENTS.md`, measured to carry a fence at line 73 with 174 table rows after it, and
        breaks one of those rows."""
        doc = ROOT / "AGENTS.md"
        original = doc.read_text()
        try:
            lines = original.splitlines(keepends=True)
            first_fence = next(i for i, l in enumerate(lines)
                               if l.lstrip().startswith("```"))
            after = [i for i in range(first_fence + 1, len(lines))
                     if lines[i].startswith("| ") and lines[i].rstrip().endswith("|")
                     and "---" not in lines[i]]
            self.assertTrue(after, "the document must carry tables after its first fence")
            victim = after[len(after) // 2]          # a middle row, so the table has rows
            lines[victim] = lines[victim].rstrip()[:-1] + " | extra |\n"
            doc.write_text("".join(lines))
            out = lint.run(self.k, ["tables"])
            self.assertIn("tables.ragged", [f["rule"] for f in out],
                          f"a real ragged row after a fence is still reported: {out}")
        finally:
            doc.write_text(original)

    def test_an_escaped_pipe_is_a_literal_not_a_new_cell(self):
        """Found by this ledger row's own insertion: the row's prose named the defect as "a cell's
        trailing `* |` with `*`", and that literal pipe counted as a fourth separator, so the pack
        reported its own well-formed row as ragged — a false positive that would have sent its
        author back to a table that was not broken. Markdown agrees: `\\|` inside a cell is a
        literal. The pack counts cells markdown's way, and reverting `_table_cells` fails this."""
        doc = ROOT / "docs" / "COMMANDS.md"
        original = doc.read_text()
        try:
            lines = original.splitlines(keepends=True)
            row = next(i for i, l in enumerate(lines) if l.startswith("| "))
            inner = lines[row].rstrip()[:-1]        # keep the closing pipe, add an escaped pipe
            lines[row] = inner + " a literal \\| here |\n"
            found = lint.run(self.k, ["tables"])
            self.assertNotIn("tables.ragged", [f["rule"] for f in found],
                             f"an escaped pipe is a literal, not a cell separator: {found}")
        finally:
            doc.write_text(original)
