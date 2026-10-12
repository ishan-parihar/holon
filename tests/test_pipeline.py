"""The delegated-work pipeline end to end, in pull mode (no harness involved)."""

import unittest

from holon.execution.work import TransitionError
from tests.helpers import KernelCase


class HappyPath(KernelCase):
    def test_full_lifecycle_with_memory(self):
        t = self.add(project_ref="proj:alpha", task_class="demo")
        c = self.k.claim("planner", "agt_p")
        self.assertEqual(c.task["id"], t["id"])
        self.assertTrue((c.brief_dir / "BRIEF.md").exists())
        self.assertIn("Write hello", (c.brief_dir / "context.md").read_text())
        out = self.k.submit(c.run["id"], self.plan_env())
        self.assertEqual(out["task"]["status"], "awaiting_approval")
        plan = out["plan"]

        self.assertEqual([p["id"] for p in self.k.work.awaiting_approval()], [plan["id"]])
        self.k.approve(plan["id"], note="keep it to one file")
        self.assertEqual(self.k.work.get_task(t["id"])["status"], "approved")

        c = self.k.claim("executor", "agt_e")
        self.assertIn("Approved plan", (c.brief_dir / "context.md").read_text())
        self.assertIn("keep it to one file", (c.brief_dir / "context.md").read_text())
        (self.workdir / "hello.txt").write_text("hi")
        out = self.k.submit(c.run["id"], self.exec_env())
        self.assertEqual(out["task"]["status"], "verifying")
        self.assertEqual(out["memory"]["scope"], "proj:alpha")

        c = self.k.claim("verifier", "agt_v")
        self.assertIn("Result to verify", (c.brief_dir / "context.md").read_text())
        out = self.k.submit(c.run["id"], {"verdict": "pass", "note": "file present"})
        self.assertEqual(out["task"]["status"], "done")

        res = self.k.work.latest_result(t["id"])
        self.assertEqual(res["verified"], 1)
        self.assertIn("wrote hello.txt", res["documentation"])
        # memory: learning, review note (project + class scope), episode, checkpoint
        self.assertTrue(self.k.memory.search("greeting file", scopes=["proj:alpha"]))
        self.assertTrue(self.k.memory.live_items(["class:demo"], kinds=["preference"]))
        self.assertEqual(self.k.memory.get_state("proj:alpha")["next_steps"], "next thing")
        self.assertEqual(len(self.k.memory.episodes(task_id=t["id"])), 1)
        stats = dict(self.wconn_row("SELECT * FROM task_class_stats WHERE task_class='demo'"))
        self.assertEqual((stats["plans"], stats["approved_first_try"], stats["verified_success"]), (1, 1, 1))

    def wconn_row(self, sql):
        return self.k.wconn.execute(sql).fetchone()

    def test_events_record_every_transition(self):
        t = self.add()
        c = self.k.claim("planner", "agt_p")
        self.k.submit(c.run["id"], self.plan_env())
        statuses = [e["to_status"] for e in reversed(self.k.work.events(t["id"]))]
        self.assertEqual(statuses, ["inbox", "planning", "awaiting_approval"])


class HumanGate(KernelCase):
    def test_reject_requires_note_and_replan_sees_it(self):
        t = self.add()
        c = self.k.claim("planner", "agt_p")
        plan = self.k.submit(c.run["id"], self.plan_env())["plan"]
        with self.assertRaises(ValueError):
            self.k.reject(plan["id"], "  ")
        self.k.reject(plan["id"], "use a markdown file instead")
        self.assertEqual(self.k.work.get_task(t["id"])["status"], "inbox")
        c = self.k.claim("planner", "agt_p")
        ctx = (c.brief_dir / "context.md").read_text()
        self.assertIn("Previous plans and review notes", ctx)
        self.assertIn("use a markdown file instead", ctx)
        plan2 = self.k.submit(c.run["id"], self.plan_env(steps=["write hello.md"]))["plan"]
        self.assertEqual(plan2["version"], 2)

    def test_needs_input_round_trip(self):
        t = self.add()
        c = self.k.claim("planner", "agt_p")
        out = self.k.submit(c.run["id"], {"kind": "needs_input", "question": "English or Hindi?"})
        self.assertEqual(out["task"]["status"], "needs_input")
        self.assertEqual(self.k.claim("planner", "agt_p"), None)
        self.k.answer(t["id"], "Hindi")
        self.assertEqual(self.k.work.get_task(t["id"])["status"], "inbox")
        self.assertTrue(any("Hindi" in i["content"] for i in self.k.memory.live_items(["global"], kinds=["decision"])))
        c = self.k.claim("planner", "agt_p")
        self.assertIn("Hindi", (c.brief_dir / "context.md").read_text())

    def test_cannot_approve_twice_or_execute_unapproved(self):
        self.add()
        c = self.k.claim("planner", "agt_p")
        plan = self.k.submit(c.run["id"], self.plan_env())["plan"]
        self.assertIsNone(self.k.claim("executor", "agt_e"))
        self.k.approve(plan["id"])
        with self.assertRaises(TransitionError):
            self.k.approve(plan["id"])


class Decomposition(KernelCase):
    def test_parent_closes_when_children_done(self):
        parent = self.add("Big job", project_ref="proj:big")
        c = self.k.claim("planner", "agt_p")
        out = self.k.submit(c.run["id"], {"kind": "decompose", "rationale": "two parts", "children": [
            {"title": "part A", "intent": "do A"}, {"title": "part B", "intent": "do B"}]})
        out = self.k.approve(out["plan"]["id"])
        self.assertEqual(out["task"]["status"], "waiting_on_subtasks")
        self.assertEqual(len(out["children"]), 2)
        for cid in out["children"]:
            child = self.k.work.get_task(cid)
            self.assertEqual((child["parent_id"], child["project_ref"]), (parent["id"], "proj:big"))
        for _ in out["children"]:
            c = self.k.claim("planner", "agt_p")
            self.assertIn("Parent tasks", (c.brief_dir / "context.md").read_text())
            self.k.approve(self.k.submit(c.run["id"], self.plan_env(writes=[]))["plan"]["id"])
            c = self.k.claim("executor", "agt_e")
            self.k.submit(c.run["id"], self.exec_env())
            self.k.work.verify(c.task["id"], "pass", "ok")
        self.assertEqual(self.k.work.get_task(parent["id"])["status"], "done")


class Robustness(KernelCase):
    def _to_approved(self, **plan_over):
        t = self.add()
        c = self.k.claim("planner", "agt_p")
        plan = self.k.submit(c.run["id"], self.plan_env(**plan_over))["plan"]
        self.k.approve(plan["id"])
        return t, plan

    def test_stale_plan_goes_back_to_planning(self):
        t, plan = self._to_approved()
        self.assertIsNotNone(plan["writes_fingerprint"])
        (self.workdir / "hello.txt").write_text("someone else wrote this first")
        self.assertIsNone(self.k.claim("executor", "agt_e"))
        task = self.k.work.get_task(t["id"])
        self.assertEqual((task["status"], task["blocked_reason"]), ("inbox", "stale_plan"))
        self.assertEqual(self.k.work.get_plan(plan["id"])["status"], "superseded")

    def test_failure_retries_then_fails(self):
        t, _ = self._to_approved(writes=[])
        for expected in ("approved", "failed"):
            c = self.k.claim("executor", "agt_e")
            out = self.k.submit(c.run["id"], self.exec_env(outcome="failure", summary="boom"))
            self.assertEqual(out["task"]["status"], expected)
        self.k.work.retry(t["id"])
        self.assertEqual(self.k.work.get_task(t["id"])["status"], "approved")

    def test_expired_lease_is_recovered_and_late_result_rejected(self):
        t = self.add()
        c = self.k.claim("planner", "agt_p")
        self.k.wconn.execute("UPDATE runs SET lease_until='2000-01-01T00:00:00.000000Z' WHERE id=?", (c.run["id"],))
        swept = self.k.work.sweep_leases()
        self.assertEqual(len(swept), 1)
        self.assertEqual(self.k.work.get_task(t["id"])["status"], "inbox")
        with self.assertRaises(TransitionError):
            self.k.submit(c.run["id"], self.plan_env())

    def test_execute_contract_breach_becomes_partial_for_verifier(self):
        t, _ = self._to_approved(writes=[])
        c = self.k.claim("executor", "agt_e")
        out = self.k.submit(c.run["id"], {"outcome": "great"})
        self.assertTrue(out["breach"])
        self.assertEqual(out["task"]["status"], "verifying")
        self.assertEqual(self.k.work.latest_result(t["id"])["outcome"], "partial")

    def test_plan_contract_breach_returns_task_to_inbox(self):
        t = self.add()
        c = self.k.claim("planner", "agt_p")
        out = self.k.submit(c.run["id"], {"kind": "plan"})
        self.assertTrue(out["breach"])
        self.assertEqual(self.k.work.get_task(t["id"])["status"], "inbox")
        self.assertEqual(self.k.work.get_run(c.run["id"])["status"], "failed")

    def test_illegal_transitions_raise(self):
        t = self.add()
        with self.assertRaises(TransitionError):
            self.k.work.verify(t["id"], "pass", "nope")
        with self.assertRaises(TransitionError):
            self.k.work.retry(t["id"])

    def test_cancel_abandons_running_run(self):
        t = self.add()
        c = self.k.claim("planner", "agt_p")
        self.k.work.cancel(t["id"], "no longer needed")
        self.assertEqual(self.k.work.get_run(c.run["id"])["status"], "abandoned")


if __name__ == "__main__":
    unittest.main()
