"""Push mode through a driver (fake harness), concurrency, schedules, recurring plans, hiring."""

import multiprocessing as mp
import unittest

from holon.core.paths import Home
from holon.execution.service import Kernel
from holon.execution.work import TransitionError
from tests.helpers import JD, KernelCase


class PushMode(KernelCase):
    def test_dispatch_through_driver_end_to_end(self):
        t = self.add(dept="eng", task_class="demo")
        out = self.k.dispatch(self.hod, "plan")
        self.assertEqual(out["approval"]["status"], "ready")
        self.k.approve(out["approval"]["id"])
        out = self.k.dispatch(self.dev1, "execute")
        self.assertEqual(self.pipeline_of(t)[-1], ("verify", "ready"))
        self.assertTrue((self.workdir / "hello.txt").exists())
        self.assertEqual(out["proposals_routed"], 1)
        out = self.k.dispatch(self.dev2, "verify")
        self.assertEqual(out["work"]["status"], "done")
        self.assertTrue(self.k.memory.live_items(["dept:eng"], kinds=["constraint"]))
        run = self.k.wconn.execute("SELECT id FROM runs WHERE kind='verify'").fetchone()[0]
        self.assertIn("[exit] 0", (self.home.run_dir(run) / "driver.log").read_text())

    def test_dispatch_without_a_stage_takes_whatever_the_roles_allow(self):
        self.add(dept="eng")
        out = self.k.dispatch(self.hod)
        self.assertEqual(out["stage"], "plan")

    def test_approve_stage_through_driver(self):
        self.k.org.set_authority(f"emp:{self.ceo}", "*", 4, "test: CEO may approve anything")
        t = self.add(dept="eng")
        self.k.dispatch(self.hod, "plan")
        out = self.k.dispatch(self.ceo, "approve")
        self.assertEqual(out["decider"], f"emp:{self.ceo}")
        self.assertEqual(self.pipeline_of(t)[-1], ("execute", "ready"))

    def test_read_only_guard_rejects_a_writing_observer(self):
        self.install_fake_driver("fake", mode="dirty-planner")
        t = self.add(dept="eng")
        out = self.k.dispatch(self.hod, "plan")
        self.assertIn("read-only", out["breach"][0])
        self.assertEqual(self.pipeline_of(t), [("plan", "ready")])

    def test_harness_read_only_is_trusted_only_when_the_instance_says_so(self):
        self.install_fake_driver("fake", mode="dirty-planner", extra="read_only_enforced = true")
        self.add(dept="eng")
        self.assertIn("breach", self.k.dispatch(self.hod, "plan"), "defense in depth: the kernel still checks")
        self.k.config["planner"]["trust_harness_read_only"] = True
        self.add("again", dept="eng")
        self.assertNotIn("breach", self.k.dispatch(self.hod, "plan"))

    def test_caches_left_by_running_tests_are_not_a_breach(self):
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=self.workdir, check=True)
        (self.workdir / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=self.workdir, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"], cwd=self.workdir,
                       check=True)
        self.install_fake_driver("fake", mode="pycache")
        self.add(dept="eng")
        self.assertNotIn("breach", self.k.dispatch(self.hod, "plan"))

    def test_a_breach_after_the_agent_submitted_itself_is_recorded_not_fatal(self):
        self.install_fake_driver("fake", mode="dirty-planner+self-submit")
        t = self.add(dept="eng")
        out = self.k.dispatch(self.hod, "plan")
        self.assertTrue(out["submitted_by_agent"])
        self.assertIn("read-only", out["breach"][0])
        stage = self.k.work.stages(t["id"], "plan")[0]
        self.assertTrue(any("read-only breach" in (e["note"] or "") for e in self.k.work.events(stage["id"])))

    def test_stdout_json_driver(self):
        self.install_fake_driver("fake", result="stdout-json", mode="stdout")
        self.add(dept="eng")
        self.assertIn("approval", self.k.dispatch(self.hod, "plan"))

    def test_agent_may_submit_itself_with_its_run_token(self):
        self.install_fake_driver("fake", mode="self-submit")
        t = self.add(dept="eng")
        out = self.k.dispatch(self.hod, "plan")
        self.assertTrue(out.get("submitted_by_agent"))
        self.assertEqual(self.pipeline_of(t)[-1], ("approve", "ready"))

    def test_garbage_output_from_executor_is_wrapped(self):
        self.add(dept="eng")
        self.k.approve(self.k.dispatch(self.hod, "plan")["approval"]["id"])
        self.install_fake_driver("fake", result="stdout-json", mode="garbage")
        out = self.k.dispatch(self.dev1, "execute")
        self.assertTrue(out["breach"])
        self.assertIn("trust me", self.k.work.last_execute_output(out["work"]["id"])["documentation"])

    def test_concurrency_cap(self):
        self.add(dept="eng")
        self.add("second", dept="eng")
        self.k.claim("plan", self.hod)
        self.assertIsNone(self.k.dispatch(self.hod, "plan"))

    def test_paused_employee_cannot_work(self):
        self.k.org.set_status(self.hod, "paused", "testing")
        with self.assertRaises(RuntimeError):
            self.k.dispatch(self.hod, "plan")

    def test_legacy_driver_duties_map_to_stages(self):
        self.install_fake_driver("old", extra='duties = ["plan", "review"]')
        from holon.execution import drivers
        d = drivers.get(self.home, "old")
        self.assertEqual(d.stages, ["approve", "plan", "research", "retro", "shape"])


def _claimer(home_root, n, q):
    k = Kernel(Home(home_root), actor="worker")
    got = []
    for _ in range(n):
        c = k.claim("plan")
        if c:
            got.append(c.node["id"])
    k.close()
    q.put(got)


class Concurrency(KernelCase):
    def test_parallel_claims_never_double_claim(self):
        ids = {self.k.work.stages(self.add(f"t{i}", workdir=None)["id"])[0]["id"] for i in range(24)}
        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        procs = [ctx.Process(target=_claimer, args=(self.home.root, 8, q)) for _ in range(6)]
        for p in procs:
            p.start()
        claimed = []
        for _ in procs:
            claimed += q.get(timeout=120)
        for p in procs:
            p.join(timeout=60)
        self.assertEqual(len(claimed), len(set(claimed)), "a stage was claimed twice")
        self.assertEqual(set(claimed), ids)


class Schedules(KernelCase):
    def test_wake_schedule_dispatches_in_the_foreground(self):
        s = self.k.schedules.add("wake", "*/5 * * * *", employee_id=self.hod, stage="plan")
        self.k.wconn.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (s["id"],))
        t = self.add(dept="eng")
        out = self.k.tick(foreground=True)
        self.assertEqual(out["fired"][0]["dispatched"], True)
        self.assertEqual(self.pipeline_of(t)[-1], ("approve", "ready"))
        after = self.k.schedules.get(s["id"])
        self.assertGreater(after["next_run_at"], after["last_run_at"])
        self.assertEqual(self.k.tick(foreground=True)["fired"], [])

    def test_wake_schedule_requires_the_role(self):
        with self.assertRaises(ValueError):
            self.k.schedules.add("wake", "0 * * * *", employee_id=self.dev1, stage="verify")

    def test_recurring_plan_creates_preapproved_work(self):
        w = self.add("Daily digest", dept="eng", task_class="digest")
        approval = self.plan(w, writes=[], assignee=self.dev1)
        plan_id = self.k.work.needs(approval["id"])[0]
        with self.assertRaises(TransitionError):
            self.k.schedules.add("recurring", "0 9 * * *", template_id=plan_id)
        self.k.approve(approval["id"], note="ok as a daily template", recurring=True)
        s = self.k.schedules.add("recurring", "0 9 * * *", template_id=plan_id)
        self.k.wconn.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (s["id"],))
        new_id = self.k.tick()["fired"][0]["recurring_work"]
        new = self.k.work.get(new_id)
        self.assertEqual((new["status"], new["source"], new["assignee"]), ("open", "schedule", None))
        self.assertEqual(self.k.work.approved_plan(new_id)["approval"]["decided_by"], f"recurring:{plan_id}")
        c = self.k.claim("execute", self.dev1, task_id=new_id)
        self.assertIsNotNone(c)


class Hiring(KernelCase):
    def test_hire_validates_and_head_takes_reports(self):
        with self.assertRaises(RuntimeError):
            self.k.org.hire(name="X", title="Y", dept="eng", job_description=JD, roles=["dancer"], driver="fake",
                            reason="r")
        with self.assertRaises(RuntimeError):
            self.k.org.hire(name="X", title="Y", dept="eng", job_description=JD, roles=["head"], driver="fake",
                            reason="r")  # eng already has a head
        self.k.org.create_dept("ops", "Operations")
        worker = self.k.org.hire(name="W", title="Op", dept="ops", job_description=JD, roles=["executor"],
                                 driver="fake", reason="r")
        self.assertIsNone(worker["reports_to"])
        head = self.k.org.hire(name="H", title="Head of Ops", dept="ops", job_description=JD, slot="lead",
                               driver="fake", reason="r")
        self.assertEqual(self.k.org.employee(worker["id"])["reports_to"], head["id"])
        self.assertEqual(head["reports_to"], self.ceo)
        self.assertEqual(head["stages"], ["shape", "approve", "retro"])


if __name__ == "__main__":
    unittest.main()
