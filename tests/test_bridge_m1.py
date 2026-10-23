"""Tests first for the bridge plan's M1 — import fidelity.

`holon routine import` reads 7 of the 33 frontmatter fields the 62 venture protocols
carry and silently drops 26. The loss that matters is `skills`: every one of the 62
carries it, and without it "run the resume-system skill on this input" becomes
"summarise the inbox" — a different job, imported as if it were the same one.

M1 has three parts, and each is guarded here:
1. **Provenance, always.** Every imported routine names its source protocol and every
   key that was read, so nothing is silently lost and a later mechanism can read it.
2. **First-class where the kernel has a home.** `skills` and `enabled_toolsets` are the
   routine's protocol, and a routine already carries a **playbook** — a versioned
   protocol with a rubric, refined by the kernel's own loop from run outcomes.
3. **A schema test**, so the class of defect cannot recur with a new field.
"""

import json
import sys
import tempfile
from pathlib import Path

from holon import cli
from tests.helpers import KernelCase

PROTOCOL = """---
cron_id: "1bcf46075baf"
employee: emp-1bcf46075baf
name: "job-loop-p2-score"
description: "Phase p2-score of the job-engine autonomous flywheel."
schedule: "15 */4 * * *"
system: "career-pipeline"
status: "active"
sub_system: "loop"
no_agent: false
axe_awareness: true
deliver: "telegram:123456789:129032"
workdir: ""
model: "large-stack"
provider: "Kilo"
enabled_toolsets:
  - terminal
  - web
created: "2026-08-16"
last_updated: "2026-08-17"
notes:
  - "Migrated from revenue-engine as part of AD-054 (2026-08-16)."
skills:
  - job-application-automation
  - resume-system
tags:
  - job
system_pool: job-engine
---

# Job loop, phase p2 — score

Score each open application against the role. Rank them.
"""


class ImportKeepsWhatItReads(KernelCase):
    """M1. Nothing in a protocol is dropped without a trace."""

    def protocol(self, body: str = PROTOCOL) -> Path:
        d = Path(tempfile.mkdtemp(prefix="m1-protocol-"))
        pool = d / "career-pipeline"
        (pool / "cron-jobs").mkdir(parents=True)
        (pool / "scripts").mkdir(parents=True)
        p = pool / "cron-jobs" / "job-loop-p2-score.md"
        p.write_text(body)
        return p

    def test_no_frontmatter_key_is_silently_dropped(self):
        """The class of defect, in general: the importer reads a schema and discards
        keys. Every key the protocol carries must appear either as a routine field or in
        the provenance the import writes."""
        p = self.protocol()
        row = cli.legacy_cron(p)
        carried = set(row["provenance"]["fields"])
        # every key the sample protocol declares must be carried
        declared = {line.split(":", 1)[0].strip() for line in PROTOCOL.splitlines()
                    if ":" in line and not line.startswith((" ", "\t", "-"))}
        declared -= {"name", "schedule", "description", "no_agent", "script", "deliver", "status"}
        self.assertTrue(declared, "the sample protocol declares keys")
        for key in sorted(declared):
            self.assertIn(key, carried, f"the import dropped {key}")
        self.assertEqual(row["provenance"]["dropped"], [],
                         "every key must be carried or explicitly listed as dropped")
        # and the keys the real 62 protocols carry that this sample does not
        for key in ("deliver_target", "department", "pool", "ad_scope", "agent_type",
                    "continuity", "registers_on_spawn", "superseded_by"):
            body = PROTOCOL.replace("system_pool: job-engine",
                                    f"system_pool: job-engine\n{key}: x")
            row2 = cli.legacy_cron(self.protocol(body))
            self.assertIn(key, row2["provenance"]["fields"], f"the import dropped {key}")

    def test_the_provenance_names_the_source_protocol(self):
        """A routine that came from a protocol must say which one, so a reader can go
        back to it — and so the Chair can see what was imported."""
        p = self.protocol()
        row = cli.legacy_cron(p)
        self.assertEqual(row["provenance"]["source"], str(p))
        self.assertIn("job-loop-p2-score.md", row["provenance"]["source"])
        self.assertIn("job-loop-p2-score", row["intent"],
                      "the routine's intent names the protocol it came from")

    def test_the_skills_become_the_routines_playbook_not_free_text(self):
        """A routine already carries a playbook — a versioned protocol with a rubric,
        refined by the kernel's loop. The source's `skills` is exactly that, and it
        belongs there rather than in the intent's prose."""
        p = self.protocol()
        row = cli.legacy_cron(p)
        self.assertEqual(row["skills"], ["job-application-automation", "resume-system"],
                         "the protocol's skills are carried as a field")
        self.assertEqual(row["enabled_toolsets"], ["terminal", "web"],
                         "the tool boundary the pool trusted is carried as a field")

    def test_the_imported_routine_gets_a_playbook_naming_its_skills(self):
        """The end-to-end shape: an imported routine's playbook says which skills run
        it, so a body that loads the playbook knows the protocol."""
        p = self.protocol()
        a = self.k.add_routine("job-loop-p2-score", cli.legacy_cron(p)["intent"], dept="eng",
                               cron="15 */4 * * *", workdir=str(p.parent.parent), strict=False, imported=True)
        self.assertIsNotNone(a)
        # the routine's playbook is written when the routine is approved, so approve it
        plan = self.k.work.plan_for_approval(a["id"])
        self.assertIsNotNone(plan, "the routine went to its approver")
        self.k.approve(plan["id"])
        from holon.capabilities import playbooks
        pb = playbooks.read(self.k.skills, "eng", playbooks.for_routine("job-loop-p2-score"))
        self.assertIsNotNone(pb, "the approved routine carries a playbook")
        self.assertIn("resume-system", pb["protocol"],
                      "the playbook names the skills the protocol runs")

    def test_a_protocol_with_no_frontmatter_still_imports(self):
        """The importer must not require a schema it has never seen: a body-only file
        imports with an empty provenance, not an error."""
        p = self.protocol("# Just a body, no frontmatter.\n")
        row = cli.legacy_cron(p)
        self.assertEqual(row["title"], "job-loop-p2-score", "the file's name is the title")
        self.assertEqual(row["provenance"]["fields"], {})

    def test_a_paused_protocol_stays_paused(self):
        """The lifecycle keys are read: a decommissioned or paused protocol must not
        land as a live routine."""
        body = PROTOCOL.replace('status: "active"', 'status: "decommissioned"')
        row = cli.legacy_cron(self.protocol(body))
        self.assertTrue(row["paused"], "a decommissioned protocol stays paused")
