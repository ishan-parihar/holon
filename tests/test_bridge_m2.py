"""Tests first for the bridge plan's M2 — G4, the Chair-key-free intake.

`task add` is `chair`-only and `msg request` is `employee`-only, so a scanner in a
venture pool — a cron job with no Holon identity — cannot file a lead for itself.
Every external intake stops at the Chair, which is what makes "the ventures run
themselves" impossible and why the pilot could never be more than a demonstration.

M2 adds an **intake key**: a per-department, scoped credential that authorizes filing
work into that department only, at a grade floor the Chair sets per department. Three
guards, and each is a test here:

- a key for one department cannot file into another;
- a key cannot file above its floor;
- a revoked key is refused.

The fourth property is structural: an intake key must never carry the Chair's
authority, so it cannot approve, decide, or reach the Chair's commands at all.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.helpers import KernelCase

REPO = Path(__file__).resolve().parents[1]


class IntakeKey(KernelCase):
    """M2. A scoped credential that can file work, and nothing else."""

    def with_intake_config(self, extra: str = "") -> None:
        self.home.config_file.write_text(
            self.home.config_file.read_text()
            + "\n[intake.dept.eng]\ngrade_floor = \"G1\"\ntask_classes = [\"lead\"]\n" + extra)
        self.k.close()
        from holon.execution.service import Kernel
        self.k = Kernel(self.home)

    def issue(self, dept: str = "eng") -> str:
        # `eng` already exists in KernelCase; `ops` does not
        if not any(d["slug"] == "ops" for d in self.k.org.depts()):
            self.k.org.create_dept("ops", "Ops", "Run the things")
        self.with_intake_config()
        return self.k.auth.issue_intake_key(dept, floor="G1", task_classes=["lead"])

    def test_a_key_files_work_into_its_own_department(self):
        """The point of G4: a scanner with an intake key files its own lead, without
        the Chair."""
        key = self.issue("eng")
        out = self.k.intake_file(key, "New lead from a scanner", "A lead the scanner found",
                                 task_class="lead")
        self.assertEqual(self.k.org.dept(out["dept_id"])["slug"], "eng",
                         "the work lands in the key's department")
        self.assertEqual(out["status"], "open")
        self.assertEqual(out["source"], "intake", "the work says an intake filed it")
        self.assertEqual(out["requested_by"], "intake:eng", "and which intake")
        self.assertTrue(out["id"].startswith("tsk_"))

    def test_a_key_cannot_file_into_another_department(self):
        """Guard 1: the key is scoped. A scanner that can file into any department is a
        second Chair."""
        key = self.issue("eng")
        with self.assertRaises(PermissionError) as caught:
            self.k.intake_file(key, "Cross-department", "Not mine to file", dept="ops",
                                task_class="lead")
        self.assertIn("ops", str(caught.exception), "the refusal names the department it tried")

    def test_a_key_cannot_file_above_its_floor(self):
        """Guard 2: the Chair sets the floor per department. A key at G1 cannot file a
        G3 job — that is the Chair's decision, not the scanner's."""
        key = self.issue("eng")
        with self.assertRaises(PermissionError) as caught:
            self.k.intake_file(key, "Irreversible thing", "Do something outside the organization",
                               task_class="lead", risk="irreversible")
        self.assertIn("floor", str(caught.exception).lower())

    def test_a_key_cannot_file_a_task_class_outside_its_list(self):
        """Guard 3: the key names the classes it may file. A key for `lead` does not
        file `objective`."""
        key = self.issue("eng")
        with self.assertRaises(PermissionError):
            self.k.intake_file(key, "An objective", "Not my class", task_class="objective")

    def test_a_revoked_key_is_refused(self):
        """Guard 4: a key the Chair takes back stops working, and the refusal says so."""
        key = self.issue("eng")
        self.k.auth.revoke_intake_key("eng")
        with self.assertRaises(PermissionError) as caught:
            self.k.intake_file(key, "After revocation", "Should not land", task_class="lead")
        self.assertIn("revoked", str(caught.exception).lower()
                      + " unknown" if "revoked" not in str(caught.exception).lower() else "revoked")

    def test_an_intake_key_is_never_the_chair(self):
        """The structural property: an intake key authenticates as an intake, and the
        Chair's commands stay the Chair's. A key that could reach `plan approve` would
        be a second Chair key."""
        key = self.issue("eng")
        p = self.k.auth.intake_principal(key)
        self.assertEqual(p.kind, "intake")
        self.assertNotEqual(p.kind, "chair")
        self.assertFalse(p.legacy)

    def test_an_unknown_key_is_refused(self):
        """A key that was never issued, or was rotated away, is nobody."""
        with self.assertRaises(PermissionError):
            self.k.auth.intake_principal("hik_not_a_real_key")

    def test_the_chair_can_issue_and_list_intake_keys(self):
        """The Chair's side: issue a key, see which departments have one and at what
        floor, and revoke one."""
        self.with_intake_config()
        key = self.k.auth.issue_intake_key("eng", floor="G1", task_classes=["lead"])
        self.assertTrue(key.startswith("hik_"))
        rows = self.k.auth.intake_keys()
        self.assertEqual([r["dept"] for r in rows], ["eng"])
        self.assertEqual(rows[0]["floor"], "G1")
        self.assertEqual(rows[0]["task_classes"], ["lead"])
        self.assertNotIn(key, json.dumps(rows), "a listing never shows the key itself")

    def test_a_key_is_stored_hashed(self):
        """Only hashes are stored, like every other credential here: the key lives in
        the scanner's own environment, never in the instance."""
        key = self.issue("eng")
        row = self.k.wconn.execute("SELECT * FROM settings WHERE key LIKE 'intake%'").fetchall()
        self.assertTrue(row, "the key's hash is stored")
        self.assertNotIn(key, json.dumps([list(r) for r in row]),
                         "the key itself must never be stored")

    def test_the_intake_key_reaches_the_cli(self):
        """The end-to-end shape: a scanner outside the organization files work through
        the ordinary CLI, authenticated by its key."""
        key = self.issue("eng")
        p = subprocess.run([sys.executable, "-m", "holon", "--home", str(self.home.root),
                            "task", "add", "--intake-key", key, "Scanner lead", "--intent", "found by a scanner",
                            "--task-class", "lead", "--dept", "eng"],
                           capture_output=True, text=True, cwd=str(REPO),
                           env={**os.environ, "PYTHONPATH": str(REPO)})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Scanner lead", p.stdout)
