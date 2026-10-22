"""Doctor and gc's share of the placement law (2026-10-22 audit): a dangling artifact pointer is
named before someone needs it, a directory that grew at the instance root without an owner is
reported, and gc never collects a run directory a pinned artifact still points into."""

import json
import time
import unittest

from holon.integrity import doctor, gc
from tests.helpers import KernelCase


class DanglingArtifacts(KernelCase):
    def _work_with_artifact(self, path):
        w = self.add("Build report", dept="eng")
        self.plan_and_approve(w, planner=self.hod, assignee=self.dev1,
                              writes=[str(self.workdir / "rpt.md")])
        c = self.k.claim("execute", self.dev1, task_id=self.k.work.latest(w["id"], "execute")["id"])
        self.k.submit(c.run["id"], self.exec_env(artifacts=[str(path)]))
        return w

    def test_doctor_reports_a_dangling_artifact_pointer(self):
        """A3: an artifact that stopped resolving was invisible — the record rotted in
        `outputs` and nothing re-read it. Doctor re-resolves every execute result's artifacts;
        a missing path is a warning naming the work, the path and the fix."""
        ghost = self.tmp / "gone" / "rpt.md"
        w = self._work_with_artifact(ghost)
        findings = [f for f in doctor.run(self.k) if f["check"] == "artifacts.dangling"]
        self.assertTrue(any(w["id"] in f["message"] and "gone" in f["message"] for f in findings),
                        findings)
        ghost.parent.mkdir(parents=True)
        ghost.write_text("here")
        self.assertEqual([f for f in doctor.run(self.k) if f["check"] == "artifacts.dangling"], [],
                          "the warning clears once the path resolves again")

    def test_a_relative_artifact_resolves_against_its_workdir(self):
        """A relative artifact names a thing inside the work, so doctor resolves it there —
        not against the kernel's own cwd, where it would dangle forever."""
        (self.workdir / "rel.md").write_text("here")
        self._work_with_artifact(self.workdir / "rel.md")
        # record the artifact as a relative path, the shape the contract allows
        w = self.add("Second report", dept="eng")
        self.plan_and_approve(w, planner=self.hod, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1, task_id=self.k.work.latest(w["id"], "execute")["id"])
        run_dir = self.home.run_dir(c.run["id"])
        (self.workdir / "rel.md").write_text("here")
        self.k.wconn.execute(
            "UPDATE outputs SET body=json_set(body, '$.artifacts', json(?)) WHERE task_id=?",
            (json.dumps([str(self.workdir / "rel.md")]), c.run["task_id"]))
        self.assertEqual([f for f in doctor.run(self.k) if f["check"] == "artifacts.dangling"], [],
                         "a resolving absolute path is never dangling")


class StrayRootDirectories(KernelCase):
    def test_doctor_reports_a_stray_root_directory(self):
        """A4: a directory at the instance root that no workdir names, no [scope] root covers
        and no kernel vocabulary owns grew because a body put it there. Doctor names it and
        the fix: declare it or remove it."""
        stray = self.home.root / "notes-digest"
        stray.mkdir()
        (stray / "digest.md").write_text("x")
        findings = [f for f in doctor.run(self.k) if f["check"] == "scope.stray"]
        self.assertTrue(findings, "a stray root directory was not reported")
        self.assertIn("notes-digest", findings[0]["message"])
        self.assertIn("scope", findings[0]["message"])

    def test_a_directory_a_work_names_is_not_stray(self):
        """The check must not cry wolf: a root directory used as a live work's workdir is
        declared by that use, and kernel vocabulary directories are never stray."""
        used = self.home.root / "pioneer-data"
        used.mkdir()
        self.add("Live work", workdir=str(used))
        vocab = self.home.root / "config"
        self.assertEqual([f for f in doctor.run(self.k) if f["check"] == "scope.stray"], [],
                          "a used workdir or kernel vocabulary dir is not a stray")

    def test_a_scope_root_covers_its_own_root_directory(self):
        """[scope] roots are the Chair's declaration of where a department may work: a
        directory under one is declared, not stray."""
        covered = self.home.root / "declared"
        covered.mkdir()
        self.home.config_file.write_text(self.home.config_file.read_text()
                                        + '\n[scope]\nroots = ["' + str(covered) + '"]\n')
        self.k = type(self.k)(self.home)   # reload config
        stray = self.home.root / "stray"
        stray.mkdir()
        findings = [f for f in doctor.run(self.k) if f["check"] == "scope.stray"]
        msgs = " ".join(f["message"] for f in findings)
        self.assertIn("stray/", msgs, findings)
        self.assertNotIn("declared/", msgs, "a [scope]-covered directory is declared, not stray")

    def test_the_heartbeat_liveness_dir_is_not_stray(self):
        """The heartbeat stamps its liveness at .clock/ (the operant gateway's clock organ
        runs `holon tick` and reads that stamp; the kernel's own timer does the same). That
        directory is kernel-owned placement, never a stray — flagging it cried wolf on the
        live instance and hid the real strays."""
        clock_dir = self.home.root / ".clock"
        (clock_dir).mkdir()
        (clock_dir / "last_tick").write_text(str(int(time.time())))
        findings = [f for f in doctor.run(self.k) if f["check"] == "scope.stray"]
        self.assertEqual([f["message"] for f in findings
                          if ".clock" in f["message"]], [],
                         "the heartbeat's liveness directory is not a stray")

    def test_doctor_warns_when_the_heartbeat_stamp_goes_stale(self):
        """.clock/last_tick is the heartbeat's live word: a stamp older than three cadences
        (6+ minutes) says the heartbeat stopped — the organization's schedules do not fire,
        leases do not renew, held results are never handed in. Doctor reads the stamp (a live
        one silences it) instead of asking the Chair to notice missing ticks."""
        clock = self.home.root / ".clock"
        clock.mkdir()
        stale = time.time() - 60 * 60  # one hour old: well past three cadences
        (clock / "last_tick").write_text(str(int(stale)))
        findings = [f for f in doctor.run(self.k) if f["check"] == "heartbeat.stale"]
        self.assertTrue(findings, "a stale liveness stamp was not reported")
        self.assertIn("last_tick", findings[0]["message"])
        (clock / "last_tick").write_text(str(int(time.time())))
        self.assertEqual([f for f in doctor.run(self.k) if f["check"] == "heartbeat.stale"], [],
                        "a fresh stamp silences the warning")

    def test_tick_stamps_the_heartbeat_liveness(self):
        """`holon tick` writes the stamp the doctor reads: one line, the unix seconds of the
        tick. Without it the heartbeat could be dead while doctor says nothing — the stamp is
        how the heartbeat proves it is alive, and it must survive being read back."""
        stamp = self.home.root / ".clock" / "last_tick"
        before = time.time() - 1
        self.k.tick()
        content = stamp.read_text().strip()
        self.assertTrue(content.isdigit(), f"stamp holds unix seconds, got {content!r}")
        self.assertGreaterEqual(float(content), before, "the stamp carries this tick's time")
        self.assertTrue(stamp.exists() and stamp.parent == self.home.root / ".clock")


class GcKeepsPinnedArtifacts(KernelCase):
    def test_gc_keeps_a_run_dir_a_pinned_artifact_still_points_into(self):
        """A7: gc collects run directories past [retention], and a pinned artifact may point
        into one (a body that leaves its evidence in the run dir). A run whose memory holds
        an artifact pointer into it is kept, named in the plan, and its dir survives."""
        import os
        from datetime import datetime, timedelta, timezone
        self.home.config_file.write_text(
            '[pipeline]\nresearch = "never"\nshape = "never"\nretro = "never"\n'
            '[healing]\ndoctor_every_min = 0\nbackup_every_h = 0\n'
            '[retention]\nruns_days = 1\nmessages_days = 0\n')
        self.k = type(self.k)(self.home)
        w = self.add("Build report", dept="eng")
        self.plan_and_approve(w, planner=self.hod, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1, task_id=self.k.work.latest(w["id"], "execute")["id"])
        run_dir = self.home.run_dir(c.run["id"])
        art = run_dir / "evidence.md"
        art.write_text("the thing itself")
        out = self.k.submit(c.run["id"], self.exec_env(artifacts=[str(art)]))
        self.k.wconn.execute("UPDATE tasks SET status='done' WHERE id=?", (w["id"],))
        self.k.wconn.execute("UPDATE runs SET ended_at=? WHERE id=?", (
            (datetime.now(timezone.utc) - timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            c.run["id"]))
        p = gc.plan(self.k)
        entry = [e for e in p["run_dirs"] if e["run"] == c.run["id"]]
        self.assertTrue(entry, "gc does not see the run at all")
        self.assertIn("evidence.md", entry[0]["keep"], p["run_dirs"])
        self.assertIn("result.json", entry[0]["keep"], "the envelope survives with the evidence")
        # a run with no pinned artifact pointing into it collects everything as before
        self.assertFalse([e for e in p["run_dirs"]
                          if e["run"] != c.run["id"] and e["keep"]])


if __name__ == "__main__":
    unittest.main()
