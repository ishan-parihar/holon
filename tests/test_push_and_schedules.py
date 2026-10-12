"""Push mode through a driver (fake harness), concurrency, schedules, recurring plans."""

import multiprocessing as mp
import unittest

from holon.core.paths import Home
from holon.execution.work import TransitionError
from holon.execution.service import Kernel
from tests.helpers import KernelCase


class PushMode(KernelCase):
    def setUp(self):
        super().setUp()
        r = self.k.roster
        self.p = r.add_agent("planner", "fake", agent_id="agt_p")["id"]
        self.e = r.add_agent("executor", "fake", agent_id="agt_e")["id"]
        self.v = r.add_agent("verifier", "fake", agent_id="agt_v")["id"]

    def test_dispatch_through_driver_end_to_end(self):
        t = self.add()
        out = self.k.dispatch("planner", self.p)
        self.assertEqual(out["task"]["status"], "awaiting_approval")
        self.k.approve(out["plan"]["id"])
        out = self.k.dispatch("executor", self.e)
        self.assertEqual(out["task"]["status"], "verifying")
        self.assertTrue((self.workdir / "hello.txt").exists())
        out = self.k.dispatch("verifier", self.v)
        self.assertEqual(out["task"]["status"], "done")
        self.assertTrue(self.k.memory.live_items(["global"], kinds=["constraint"]))
        verify_run = self.k.wconn.execute("SELECT id FROM runs WHERE task_id=? AND kind='verify'",
                                          (t["id"],)).fetchone()[0]
        self.assertIn("[exit] 0", (self.home.run_dir(verify_run) / "driver.log").read_text())

    def test_read_only_guard_rejects_writing_planner(self):
        self.install_fake_driver("fake", mode="dirty-planner")
        t = self.add()
        out = self.k.dispatch("planner", self.p)
        self.assertIn("read-only", out["breach"][0])
        self.assertEqual(self.k.work.get_task(t["id"])["status"], "inbox")

    def test_stdout_json_driver(self):
        self.install_fake_driver("fake", result="stdout-json", mode="stdout")
        self.add()
        out = self.k.dispatch("planner", self.p)
        self.assertEqual(out["task"]["status"], "awaiting_approval")

    def test_garbage_output_from_executor_is_wrapped(self):
        self.add()
        self.k.approve(self.k.dispatch("planner", self.p)["plan"]["id"])
        self.install_fake_driver("fake", result="stdout-json", mode="garbage")
        out = self.k.dispatch("executor", self.e)
        self.assertEqual(out["task"]["status"], "verifying")
        self.assertIn("trust me", self.k.work.latest_result(out["task"]["id"])["documentation"])

    def test_concurrency_cap(self):
        self.add()
        self.add("second")
        self.k.claim("planner", self.p, "fake")  # agent now holds one running run
        self.assertIsNone(self.k.dispatch("planner", self.p))

    def test_paused_agent_cannot_dispatch(self):
        self.k.roster.set_agent_status(self.p, "paused", "testing")
        with self.assertRaises(RuntimeError):
            self.k.dispatch("planner", self.p)


def _claimer(home_root, n, q):
    k = Kernel(Home(home_root), actor="worker")
    got = []
    for _ in range(n):
        c = k.claim("planner", "agt_race")
        if c:
            got.append(c.task["id"])
    k.close()
    q.put(got)


class Concurrency(KernelCase):
    def test_parallel_claims_never_double_claim(self):
        ids = {self.add(f"t{i}", workdir=None)["id"] for i in range(24)}
        q = mp.get_context("spawn").Queue()
        procs = [mp.get_context("spawn").Process(target=_claimer, args=(self.home.root, 8, q)) for _ in range(6)]
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
        r = self.k.roster
        r.add_agent("planner", "fake", agent_id="agt_p")
        s = r.add_schedule("planner", "*/5 * * * *", agent_id="agt_p")
        self.k.wconn.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (s["id"],))
        self.add()
        out = self.k.tick(foreground=True)
        self.assertEqual(out["fired"][0]["dispatched"], True)
        self.assertEqual(self.k.work.counts().get("awaiting_approval"), 1)
        after = r.get_schedule(s["id"])
        self.assertGreater(after["next_run_at"], after["last_run_at"])
        self.assertEqual(self.k.tick(foreground=True)["fired"], [])  # not due again yet

    def test_recurring_plan_creates_preapproved_runs(self):
        self.add("Daily digest", task_class="digest")
        c = self.k.claim("planner", "agt_p")
        plan = self.k.submit(c.run["id"], self.plan_env(writes=[]))["plan"]
        with self.assertRaises(TransitionError):
            self.k.roster.add_schedule("recurring", "0 9 * * *", plan_id=plan["id"])  # not recurring yet
        self.k.approve(plan["id"], note="ok as a daily template", recurring=True)
        s = self.k.roster.add_schedule("recurring", "0 9 * * *", plan_id=plan["id"])
        self.k.wconn.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (s["id"],))
        out = self.k.tick()
        new_id = out["fired"][0]["recurring_task"]
        new = self.k.work.get_task(new_id)
        self.assertEqual((new["status"], new["source"]), ("approved", "schedule"))
        p = self.k.work.latest_plan(new_id, "approved")
        self.assertEqual(p["decided_by"], f"recurring:{plan['id']}")
        c = self.k.claim("executor", "agt_e", task_id=new_id)
        self.assertIsNotNone(c)


if __name__ == "__main__":
    unittest.main()
