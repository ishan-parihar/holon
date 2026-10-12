"""The organization's pipeline end to end, in pull mode (no harness involved)."""

import unittest

from holon.execution.work import TransitionError, Work
from tests.helpers import KernelCase


class DelegationChain(KernelCase):
    def test_department_task_hod_plans_assignee_executes_peer_verifies(self):
        t = self.add(dept="eng", project_ref="proj:alpha", task_class="demo")
        with self.assertRaises(TransitionError):
            self.k.claim("plan", self.dev1)  # no plan duty
        pat = self.k.org.hire(name="Pat", title="Planner", dept="eng", job_description="JD", duties=["plan"],
                              driver="fake", reason="test", status="active")["id"]
        self.assertIsNone(self.k.claim("plan", pat), "a non-head may not plan unassigned department work")
        self.assertIsNone(self.k.claim("plan", self.ceo), "the CEO plans only un-triaged work")
        c = self.k.claim("plan", self.hod)
        self.assertIn("Your role", (c.brief_dir / "context.md").read_text())
        self.assertIn("Head of Engineering", (c.brief_dir / "context.md").read_text())
        out = self.k.submit(c.run["id"], self.plan_env(assignee=self.dev1))
        plan = out["plan"]
        self.assertEqual(plan["approver"], "chair", "novel class + no delegated authority -> Chair")
        self.assertGreaterEqual(plan["grade"], 2)
        self.k.approve(plan["id"], note="keep it to one file")

        self.assertIsNone(self.k.claim("execute", self.dev2), "assigned to dev1, not dev2")
        c = self.k.claim("execute", self.dev1)
        ctx = (c.brief_dir / "context.md").read_text()
        self.assertIn("keep it to one file", ctx)
        out = self.k.submit(c.run["id"], self.exec_env())
        self.assertEqual(out["task"]["status"], "verifying")
        self.assertEqual(out["memory"]["personal_scope"], f"emp:{self.dev1}")
        self.assertTrue(self.k.memory.live_items([f"emp:{self.dev1}"], kinds=["lesson"]))

        c = self.k.claim("verify", self.dev2)
        out = self.k.submit(c.run["id"], {"verdict": "pass", "note": "file present"})
        self.assertEqual(out["task"]["status"], "done")
        self.assertEqual(self.k.work.latest_result(t["id"])["verified_by"], f"emp:{self.dev2}")

        review = self.k.org.review(self.dev1)
        self.assertEqual(review["execution"]["verified_ok"], 1)
        self.assertEqual(self.k.org.review(self.hod)["planning"]["approved_first_try"], 1)

    def test_ceo_triages_and_splits_across_departments(self):
        self.k.org.create_dept("ops", "Operations")
        parent = self.add("Launch v2", dept=None)
        c = self.k.claim("plan", self.ceo)
        self.assertEqual(c.task["id"], parent["id"])
        out = self.k.submit(c.run["id"], {"kind": "decompose", "rationale": "two departments", "children": [
            {"title": "build", "intent": "build v2", "dept": "eng"},
            {"title": "deploy", "intent": "deploy v2", "dept": "ops"}]})
        self.assertGreaterEqual(out["plan"]["grade"], 2)
        out = self.k.approve(out["plan"]["id"])
        self.assertEqual(out["task"]["status"], "waiting_on_subtasks")
        depts = {self.k.work.get_task(c)["dept_id"] for c in out["children"]}
        self.assertEqual(depts, {self.k.org.dept("eng")["id"], self.k.org.dept("ops")["id"]})
        c = self.k.claim("plan", self.hod)
        self.assertEqual(c.task["title"], "build")
        self.assertIn("Parent tasks", (c.brief_dir / "context.md").read_text())

    def test_unknown_department_in_split_is_a_contract_breach(self):
        self.add("x", dept=None)
        c = self.k.claim("plan", self.ceo)
        out = self.k.submit(c.run["id"], {"kind": "decompose", "rationale": "r", "children": [
            {"title": "a", "intent": "a", "dept": "nope"}, {"title": "b", "intent": "b"}]})
        self.assertIn("no department nope", out["breach"][0])

    def test_executor_cannot_verify_own_work(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev2, writes=[])
        c = self.k.claim("execute", self.dev2)
        self.k.submit(c.run["id"], self.exec_env())
        self.assertIsNone(self.k.claim("verify", self.dev2), "the executor is excluded from verifying")
        with self.assertRaises(TransitionError):
            Work(self.k.wconn, actor=f"emp:{self.dev2}").verify(t["id"], "pass", "looks good to me")


class GradedApproval(KernelCase):
    config = "[grading]\nnovelty_min_verified = 0\n[authority]\naudit_sample_permille = 1000\npromote_after = 2\n"

    def test_minor_work_routes_to_ceo_major_work_to_chair(self):
        minor = self.add("tidy", dept="eng", task_class="ops", risk="low")
        c = self.k.claim("plan", self.hod, task_id=minor["id"])
        p = self.k.submit(c.run["id"], self.plan_env(grade="G1", writes=[]))["plan"]
        self.assertEqual((p["grade"], p["approver"]), (1, f"emp:{self.ceo}"))
        self.assertEqual(self.k.work.awaiting_approval("chair"), [])

        major = self.add("migrate prod", dept="eng", task_class="ops", risk="high")
        c = self.k.claim("plan", self.hod, task_id=major["id"])
        p2 = self.k.submit(c.run["id"], self.plan_env(grade="G1", writes=[]))["plan"]
        self.assertEqual((p2["grade"], p2["approver"]), (3, "chair"), "risk=high floors at G3; planner can't lower it")

        effects = self.add("email client", dept="eng", task_class="ops", risk="low")
        c = self.k.claim("plan", self.hod, task_id=effects["id"])
        p3 = self.k.submit(c.run["id"], self.plan_env(grade="G0", writes=[], external_effects=["send email"]))["plan"]
        self.assertEqual(p3["grade"], 2)

    def test_ceo_review_duty_decides_and_is_audited(self):
        t = self.add("tidy", dept="eng", task_class="ops", risk="low")
        c = self.k.claim("plan", self.hod)
        p = self.k.submit(c.run["id"], self.plan_env(grade="G1", writes=[]))["plan"]
        self.assertEqual(self.k.work.awaiting_approval("chair"), [], "minor work does not reach the Chair")
        with self.assertRaises(TransitionError):
            Work(self.k.wconn, actor=f"emp:{self.dev1}").approve(p["id"])  # not the routed approver
        c = self.k.claim("review", self.ceo)
        self.assertIn("Plan to review", (c.brief_dir / "context.md").read_text())
        out = self.k.submit(c.run["id"], {"decision": "approve", "note": "small and reversible"})
        self.assertEqual(out["decider"], f"emp:{self.ceo}")
        self.assertTrue(out.get("audit_sampled"))
        self.assertEqual(self.k.work.get_task(t["id"])["status"], "approved")
        judged = self.k.judge_sample(p["id"], "disagree", "this needed my eyes")
        self.assertEqual((judged["authority_change"]["from"], judged["authority_change"]["to"]), (1, 0))

    def test_nobody_approves_their_own_plan(self):
        self.k.org.set_authority(f"emp:{self.hod}", "*", 2, "test")
        self.add("tidy", dept="eng", task_class="ops", risk="low")
        c = self.k.claim("plan", self.hod)
        p = self.k.submit(c.run["id"], self.plan_env(grade="G1", writes=[]))["plan"]
        self.assertEqual(p["approver"], f"emp:{self.ceo}", "the HOD authored it, so it skips to the CEO")

    def _clean_streak(self):
        for i in range(2):
            t = self.add(f"tidy {i}", dept="eng", task_class="ops", risk="low")
            c = self.k.claim("plan", self.hod, task_id=t["id"])
            p = self.k.submit(c.run["id"], self.plan_env(grade="G1", writes=[], assignee=self.dev1))["plan"]
            c = self.k.claim("review", self.ceo)
            self.k.submit(c.run["id"], {"decision": "approve", "note": "fine"})
            c = self.k.claim("execute", self.dev1)
            self.k.submit(c.run["id"], self.exec_env())
            c = self.k.claim("verify", self.dev2)
            out = self.k.submit(c.run["id"], {"verdict": "pass", "note": "ok"})
            self.assertEqual(p["approver"], f"emp:{self.ceo}")
        return out

    def test_earned_promotion_needs_the_chair_by_default(self):
        out = self._clean_streak()
        change = out["authority_change"]
        self.assertEqual((change["to"], change["applied"], change["approver"]), (2, False, "chair"))
        self.assertEqual(self.k.org.authority_of(f"emp:{self.ceo}", "ops"), 1, "not widened until approved")
        proposal = self.k.work.awaiting_approval("chair")[0]
        self.assertEqual(proposal["id"], change["proposal"])
        self.k.approve(proposal["id"], "earned")
        self.assertEqual(self.k.org.authority_of(f"emp:{self.ceo}", "ops"), 2)
        self.assertEqual(self.k.work.get_task(proposal["task_id"])["status"], "done")

    def test_chair_regrade_reroutes(self):
        self.add("tidy", dept="eng", task_class="ops", risk="low")
        c = self.k.claim("plan", self.hod)
        p = self.k.submit(c.run["id"], self.plan_env(grade="G1", writes=[]))["plan"]
        p = self.k.regrade(p["id"], 3, "touches billing")
        self.assertEqual((p["grade"], p["approver"]), (3, "chair"))


class CollectiveMemory(KernelCase):
    def _run_lesson(self, executor, lesson, dept="eng"):
        t = self.add(f"job for {executor}", dept=dept, task_class="demo")
        self.plan_and_approve(t, assignee=executor, writes=[])
        c = self.k.claim("execute", executor)
        return self.k.submit(c.run["id"], self.exec_env(learnings=[lesson]))

    def test_lesson_recurring_across_employees_is_promoted_to_department(self):
        out = self._run_lesson(self.dev1, "Always run the migration dry-run before applying it")
        self.assertEqual(out["memory"]["promotions"], [])
        out = self._run_lesson(self.dev2, "always run the migration dry run before applying")
        self.assertEqual(out["memory"]["promotions"][0]["to"], "dept:eng")
        self.assertTrue(self.k.memory.live_items(["dept:eng"], kinds=["lesson"]))

    def test_successor_inherits_memory(self):
        self.k.memory.add_item(f"emp:{self.dev1}", "lesson", "The staging DB password rotates on Mondays", "test")
        heir = self.k.org.hire(name="Ed", title="Engineer", dept="eng", job_description="JD", duties=["execute"],
                               driver="fake", reason="replacement", status="active")["id"]
        self.k.org.fire(self.dev1, "role change", successor=heir)
        t = self.add("rotate", dept="eng", task_class="demo", assignee=heir)
        self.plan_and_approve(t, writes=[])
        c = self.k.claim("execute", heir)
        ctx = (c.brief_dir / "context.md").read_text()
        self.assertIn("inherited the role and memory", ctx)
        self.assertIn("staging DB password", ctx)

    def test_review_notes_reach_the_author(self):
        t = self.add(dept="eng", task_class="demo")
        c = self.k.claim("plan", self.hod)
        p = self.k.submit(c.run["id"], self.plan_env())["plan"]
        self.k.reject(p["id"], "use the existing template instead")
        self.assertTrue(any("existing template" in i["content"]
                            for i in self.k.memory.live_items([f"emp:{self.hod}"])))
        c = self.k.claim("plan", self.hod, task_id=t["id"])
        self.assertIn("existing template", (c.brief_dir / "context.md").read_text())


class Communication(KernelCase):
    def test_request_creates_task_in_other_department(self):
        self.k.org.create_dept("marketing", "Marketing")
        t = self.k.request(f"emp:{self.dev1}", "marketing", "Need launch copy", "Three tweets for v2")
        self.assertEqual(t["dept_id"], self.k.org.dept("marketing")["id"])
        self.assertEqual(t["requested_by"], f"emp:{self.dev1}")
        self.assertEqual(self.k.work.messages(task_id=t["id"])[0]["kind"], "request")

    def test_inbox_includes_department_channel(self):
        self.k.work.post_message("chair", "dept:eng", "standup moved", kind="channel")
        self.k.work.post_message(f"emp:{self.dev2}", f"emp:{self.dev1}", "can you review?")
        bodies = {m["body"] for m in self.k.inbox(f"emp:{self.dev1}")}
        self.assertEqual(bodies, {"standup moved", "can you review?"})


class Robustness(KernelCase):
    def _approved(self, **plan_over):
        t = self.add(dept="eng", task_class="demo")
        plan = self.plan_and_approve(t, **plan_over)
        return t, plan

    def test_stale_plan_goes_back_to_planning(self):
        t, plan = self._approved()
        self.assertIsNotNone(plan["writes_fingerprint"])
        (self.workdir / "hello.txt").write_text("someone else wrote this first")
        self.assertIsNone(self.k.claim("execute", self.dev1))
        task = self.k.work.get_task(t["id"])
        self.assertEqual((task["status"], task["blocked_reason"]), ("inbox", "stale_plan"))

    def test_failure_retries_then_fails_then_chair_retry(self):
        t, _ = self._approved(writes=[])
        for expected in ("approved", "failed"):
            c = self.k.claim("execute", self.dev1)
            self.assertEqual(self.k.submit(c.run["id"], self.exec_env(outcome="failure", summary="boom"))["task"]["status"],
                             expected)
        self.assertEqual(self.k.work.retry(t["id"])["status"], "approved")

    def test_expired_lease_is_recovered_and_late_result_rejected(self):
        t = self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        self.k.wconn.execute("UPDATE runs SET lease_until='2000-01-01T00:00:00.000000Z' WHERE id=?", (c.run["id"],))
        self.assertEqual(len(self.k.work.sweep_leases()), 1)
        self.assertEqual(self.k.work.get_task(t["id"])["status"], "inbox")
        with self.assertRaises(TransitionError):
            self.k.submit(c.run["id"], self.plan_env())

    def test_execute_contract_breach_becomes_partial(self):
        t, _ = self._approved(writes=[])
        c = self.k.claim("execute", self.dev1)
        out = self.k.submit(c.run["id"], {"outcome": "great"})
        self.assertTrue(out["breach"])
        self.assertEqual(self.k.work.latest_result(t["id"])["outcome"], "partial")

    def test_needs_input_round_trip(self):
        t = self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        self.k.submit(c.run["id"], {"kind": "needs_input", "question": "English or Hindi?"})
        self.k.answer(t["id"], "Hindi")
        c = self.k.claim("plan", self.hod)
        self.assertIn("Hindi", (c.brief_dir / "context.md").read_text())

    def test_duty_and_status_enforced(self):
        self.add(dept="eng")
        with self.assertRaises(TransitionError):
            self.k.claim("review", self.dev1)  # dev1 has no review duty
        self.k.org.set_status(self.hod, "paused", "vacation")
        with self.assertRaises(TransitionError):
            self.k.claim("plan", self.hod)

    def test_fire_reassigns_open_work_and_head(self):
        t = self.add(dept="eng", assignee=self.dev1)
        heir = self.k.org.hire(name="Fa", title="HOD", dept="eng", job_description="JD", duties=["plan", "review"],
                               driver="fake", reason="new head", status="active")["id"]
        self.k.org.fire(self.hod, "left", successor=heir)
        self.assertEqual(self.k.org.dept("eng")["head_id"], heir)
        self.k.org.fire(self.dev1, "left", successor=self.dev2)
        self.assertEqual(self.k.work.get_task(t["id"])["assignee"], self.dev2)
        self.assertEqual(self.k.org.employee(self.dev1)["status"], "retired")


if __name__ == "__main__":
    unittest.main()
