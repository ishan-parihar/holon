"""Push mode through a driver (fake harness), concurrency, schedules, recurring plans."""

import multiprocessing as mp
import unittest

from holon.core.paths import Home
from holon.execution.service import Kernel
from holon.execution.work import TransitionError
from tests.helpers import JD, KernelCase


class PushMode(KernelCase):
    def test_dispatch_through_driver_end_to_end(self):
        t = self.add(dept="eng", task_class="demo")
        out = self.k.dispatch("plan", self.hod)
        self.assertEqual(out["task"]["status"], "awaiting_approval")
        self.k.approve(out["plan"]["id"])
        out = self.k.dispatch("execute", self.dev1)
        self.assertEqual(out["task"]["status"], "verifying")
        self.assertTrue((self.workdir / "hello.txt").exists())
        out = self.k.dispatch("verify", self.dev2)
        self.assertEqual(out["task"]["status"], "done")
        self.assertTrue(self.k.memory.live_items(["dept:eng"], kinds=["constraint"]))
        run = self.k.wconn.execute("SELECT id FROM runs WHERE task_id=? AND kind='verify'", (t["id"],)).fetchone()[0]
        self.assertIn("[exit] 0", (self.home.run_dir(run) / "driver.log").read_text())

    def test_review_duty_through_driver(self):
        self.k.org.set_authority(f"emp:{self.ceo}", "*", 4, "test: CEO may approve anything")
        self.add(dept="eng")
        self.k.dispatch("plan", self.hod)
        out = self.k.dispatch("review", self.ceo)
        self.assertEqual(out["task"]["status"], "approved")

    def test_read_only_guard_rejects_writing_planner(self):
        self.install_fake_driver("fake", mode="dirty-planner")
        t = self.add(dept="eng")
        out = self.k.dispatch("plan", self.hod)
        self.assertIn("read-only", out["breach"][0])
        self.assertEqual(self.k.work.get_task(t["id"])["status"], "inbox")

    def test_stdout_json_driver(self):
        self.install_fake_driver("fake", result="stdout-json", mode="stdout")
        self.add(dept="eng")
        self.assertEqual(self.k.dispatch("plan", self.hod)["task"]["status"], "awaiting_approval")

    def test_garbage_output_from_executor_is_wrapped(self):
        self.add(dept="eng")
        self.k.approve(self.k.dispatch("plan", self.hod)["plan"]["id"])
        self.install_fake_driver("fake", result="stdout-json", mode="garbage")
        out = self.k.dispatch("execute", self.dev1)
        self.assertEqual(out["task"]["status"], "verifying")
        self.assertIn("trust me", self.k.work.latest_result(out["task"]["id"])["documentation"])

    def test_concurrency_cap(self):
        self.add(dept="eng")
        self.add("second", dept="eng")
        self.k.claim("plan", self.hod)  # the HOD now holds one running run
        self.assertIsNone(self.k.dispatch("plan", self.hod))

    def test_paused_employee_cannot_work(self):
        self.k.org.set_status(self.hod, "paused", "testing")
        with self.assertRaises(RuntimeError):
            self.k.dispatch("plan", self.hod)


def _claimer(home_root, n, q):
    k = Kernel(Home(home_root), actor="worker")
    got = []
    for _ in range(n):
        c = k.claim("plan")
        if c:
            got.append(c.task["id"])
    k.close()
    q.put(got)


class Concurrency(KernelCase):
    def test_parallel_claims_never_double_claim(self):
        ids = {self.add(f"t{i}", workdir=None)["id"] for i in range(24)}
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
        self.assertEqual(len(claimed), len(set(claimed)), "a task was claimed twice")
        self.assertEqual(set(claimed), ids)


class Schedules(KernelCase):
    def test_take_due_advances_and_tick_runs_foreground(self):
        s = self.k.schedules.add("plan", "*/5 * * * *", employee_id=self.hod)
        self.k.wconn.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (s["id"],))
        self.add(dept="eng")
        out = self.k.tick(foreground=True)
        self.assertEqual(out["fired"][0]["dispatched"], True)
        self.assertEqual(self.k.work.counts().get("awaiting_approval"), 1)
        after = self.k.schedules.get(s["id"])
        self.assertGreater(after["next_run_at"], after["last_run_at"])
        self.assertEqual(self.k.tick(foreground=True)["fired"], [])

    def test_schedule_requires_the_duty(self):
        with self.assertRaises(ValueError):
            self.k.schedules.add("verify", "0 * * * *", employee_id=self.dev1)

    def test_recurring_plan_creates_preapproved_runs(self):
        self.add("Daily digest", dept="eng", task_class="digest")
        c = self.k.claim("plan", self.hod)
        plan = self.k.submit(c.run["id"], self.plan_env(writes=[], assignee=self.dev1))["plan"]
        with self.assertRaises(TransitionError):
            self.k.schedules.add("recurring", "0 9 * * *", plan_id=plan["id"])
        self.k.approve(plan["id"], note="ok as a daily template", recurring=True)
        s = self.k.schedules.add("recurring", "0 9 * * *", plan_id=plan["id"])
        self.k.wconn.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (s["id"],))
        new_id = self.k.tick()["fired"][0]["recurring_task"]
        new = self.k.work.get_task(new_id)
        self.assertEqual((new["status"], new["source"], new["assignee"]), ("approved", "schedule", self.dev1))
        self.assertEqual(self.k.work.latest_plan(new_id, "approved")["decided_by"], f"recurring:{plan['id']}")
        self.assertIsNotNone(self.k.claim("execute", self.dev1, task_id=new_id))


class Hiring(KernelCase):
    def test_hire_validates_and_head_takes_reports(self):
        with self.assertRaises(RuntimeError):
            self.k.org.hire(name="X", title="Y", dept="eng", job_description=JD, duties=["dance"], driver="fake",
                            reason="r")
        with self.assertRaises(RuntimeError):
            self.k.org.hire(name="X", title="Y", dept="eng", job_description=JD, duties=["plan"], driver="fake",
                            reason="r", head=True)  # eng already has a head
        self.k.org.create_dept("ops", "Operations")
        worker = self.k.org.hire(name="W", title="Op", dept="ops", job_description=JD, duties=["execute"],
                                 driver="fake", reason="r")
        self.assertIsNone(worker["reports_to"])
        head = self.k.org.hire(name="H", title="Head of Ops", dept="ops", job_description=JD, duties=["plan"],
                               driver="fake", reason="r", head=True)
        self.assertEqual(self.k.org.employee(worker["id"])["reports_to"], head["id"])
        self.assertEqual(head["reports_to"], self.ceo)


if __name__ == "__main__":
    unittest.main()
