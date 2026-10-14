"""The work graph end to end, in pull mode (no harness involved)."""

import json

import unittest

from holon.execution.service import Kernel
from holon.execution.work import TransitionError, Work
from tests.helpers import JD, KernelCase


class DelegationChain(KernelCase):
    def test_planner_plans_assignee_executes_peer_verifies(self):
        t = self.add(dept="eng", project_ref="proj:alpha", task_class="demo")
        self.assertEqual(self.pipeline_of(t), [("plan", "ready")])
        with self.assertRaises(TransitionError):
            self.k.claim("plan", self.dev1)  # executors have no planner role
        self.assertIsNone(self.k.claim("plan", self.ceo), "department work belongs to the department")
        c = self.k.claim("plan", self.hod)
        ctx = (c.brief_dir / "context.md").read_text()
        self.assertIn("Your role", ctx)
        self.assertIn("Head of Engineering", ctx)
        self.assertTrue((c.brief_dir / "identity.md").read_text().startswith("# You are Bo"))
        approval = self.k.submit(c.run["id"], self.plan_env(assignee=self.dev1))["approval"]
        self.assertEqual(approval["approver"], "chair", "novel class + no delegated authority -> Chair")
        self.assertGreaterEqual(approval["grade"], 2)
        self.k.approve(approval["id"], note="keep it to one file")

        self.assertIsNone(self.k.claim("execute", self.dev2), "assigned to dev1, not dev2")
        c = self.k.claim("execute", self.dev1)
        self.assertIn("keep it to one file", (c.brief_dir / "context.md").read_text())
        out = self.k.submit(c.run["id"], self.exec_env())
        self.assertEqual(self.pipeline_of(t)[-1], ("verify", "ready"))
        self.assertEqual(out["memory"]["personal_scope"], f"emp:{self.dev1}")
        self.assertTrue(self.k.memory.live_items([f"emp:{self.dev1}"], kinds=["lesson"]))

        c = self.k.claim("verify", self.dev2)
        out = self.k.submit(c.run["id"], {"verdict": "pass", "note": "file present"})
        self.assertEqual(out["work"]["status"], "done")
        self.assertEqual(self.k.work.last_execute_output(t["id"])["verified_by"], f"emp:{self.dev2}")
        self.assertEqual(self.k.org.review(self.dev1)["execution"]["verified_ok"], 1)
        self.assertEqual(self.k.org.review(self.hod)["planning"]["approved_first_try"], 1)

    def test_any_planner_in_the_department_may_plan_unassigned_work(self):
        pat = self.k.org.hire(name="Pat", title="Planner", dept="eng", job_description=JD, roles=["planner"],
                              driver="fake", reason="test", status="active")["id"]
        self.add(dept="eng")
        self.assertIsNotNone(self.k.claim("plan", pat))

    def test_ceo_splits_across_departments_with_order(self):
        self.k.org.create_dept("ops", "Operations")
        parent = self.add("Launch v2", dept=None)
        c = self.k.claim("plan", self.ceo)
        self.assertEqual(c.node["parent_id"], parent["id"])
        out = self.k.submit(c.run["id"], {"kind": "decompose", "rationale": "two departments", "children": [
            {"title": "build", "intent": "build v2", "dept": "eng"},
            {"title": "deploy", "intent": "deploy v2", "dept": "ops", "after": [0]}]})
        self.assertGreaterEqual(out["approval"]["grade"], 2)
        out = self.k.approve(out["approval"]["id"])
        self.assertEqual(out["work"]["status"], "waiting")
        build, deploy = (self.k.work.get(i) for i in out["children"])
        self.assertEqual((build["status"], deploy["status"]), ("open", "blocked"))
        self.assertEqual(build["root_id"], parent["id"])
        self.assertEqual(build["depth"], 1)
        c = self.k.claim("plan", self.hod)
        self.assertEqual(c.node["parent_id"], build["id"])
        self.assertIn("Parent work", (c.brief_dir / "context.md").read_text())
        self.k.work.cancel(build["id"], "no longer needed")
        self.assertEqual(self.status(deploy), "cancelled", "work waiting on a cancelled dependency is cancelled")
        self.assertEqual(self.status(parent), "cancelled")

    def test_dependency_releases_blocked_work(self):
        first = self.add("first", dept="eng")
        second = self.add("second", dept="eng", needs=[first["id"]])
        self.assertEqual((self.status(second), self.pipeline_of(second)), ("blocked", []))
        self.plan_and_approve(first, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        self.k.submit(c.run["id"], self.exec_env())
        self.k.verify(first["id"], "pass", "ok")
        self.assertEqual((self.status(second), self.pipeline_of(second)), ("open", [("plan", "ready")]))

    def test_unknown_department_in_split_is_a_contract_breach(self):
        self.add("x", dept=None)
        c = self.k.claim("plan", self.ceo)
        out = self.k.submit(c.run["id"], {"kind": "decompose", "rationale": "r", "children": [
            {"title": "a", "intent": "a", "dept": "nope"}, {"title": "b", "intent": "b"}]})
        self.assertIn("no department nope", out["breach"][0])
        self.assertEqual(out["node"]["status"], "ready", "the stage returns to the board")

    def test_executor_cannot_verify_own_work(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev2, writes=[])
        c = self.k.claim("execute", self.dev2)
        self.k.submit(c.run["id"], self.exec_env())
        self.assertIsNone(self.k.claim("verify", self.dev2), "the executor is excluded from verifying")
        with self.assertRaises(TransitionError):
            Work(self.k.wconn, actor=f"emp:{self.dev2}").verify(t["id"], "pass", "looks good to me")
        self.assertIsNotNone(self.k.claim("verify", self.hod))


class Rework(KernelCase):
    def _executed(self, **plan_over):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, **plan_over)
        c = self.k.claim("execute", self.dev1)
        self.k.submit(c.run["id"], self.exec_env())
        return t

    def test_failed_verification_adds_an_attempt_then_fails(self):
        t = self._executed(writes=[])
        self.k.verify(t["id"], "fail", "file is empty")
        self.assertEqual(self.pipeline_of(t)[-1], ("execute", "ready"))
        c = self.k.claim("execute", self.dev1)
        self.assertEqual(c.node["attempt"], 2)
        self.assertIn("file is empty", (c.brief_dir / "context.md").read_text())
        self.k.submit(c.run["id"], self.exec_env())
        self.k.verify(t["id"], "fail", "still empty")
        self.assertEqual(self.status(t), "failed")
        self.assertEqual(self.k.work.retry(t["id"])["status"], "open")
        self.assertEqual(self.pipeline_of(t)[-1], ("execute", "ready"))

    def test_rejection_queues_a_new_plan_with_the_note(self):
        t = self.add(dept="eng", task_class="demo")
        approval = self.plan(t)
        self.k.reject(approval["id"], "use the existing template instead")
        self.assertTrue(any("existing template" in i["content"] for i in self.k.memory.live_items([f"emp:{self.hod}"])))
        self.assertEqual(self.pipeline_of(t)[-1], ("plan", "ready"))
        c = self.k.claim("plan", self.hod, task_id=t["id"])
        self.assertIn("existing template", (c.brief_dir / "context.md").read_text())

    def test_stale_plan_is_replanned(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t)
        (self.workdir / "hello.txt").write_text("someone else wrote this first")
        self.assertIsNone(self.k.claim("execute", self.dev1))
        last = self.k.work.stages(t["id"])[-1]
        self.assertEqual((last["stage"], last["status"], last["blocked_reason"]), ("plan", "ready", "stale_plan"))

    def test_executor_failure_retries(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        for expected in ("open", "failed"):
            c = self.k.claim("execute", self.dev1)
            out = self.k.submit(c.run["id"], self.exec_env(outcome="failure", summary="boom"))
            self.assertEqual(out["work"]["status"], expected)

    def test_expired_lease_is_recovered_and_late_result_rejected(self):
        t = self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        self.k.wconn.execute("UPDATE runs SET lease_until='2000-01-01T00:00:00.000000Z' WHERE id=?", (c.run["id"],))
        self.assertEqual(len(self.k.work.sweep_leases()), 1)
        self.assertEqual(self.pipeline_of(t), [("plan", "ready")])
        with self.assertRaises(TransitionError):
            self.k.submit(c.run["id"], self.plan_env())

    def test_execute_contract_breach_becomes_partial_for_the_verifier(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        out = self.k.submit(c.run["id"], {"outcome": "great"})
        self.assertTrue(out["breach"])
        self.assertEqual(self.k.work.last_execute_output(t["id"])["body"]["outcome"], "partial")
        self.assertEqual(self.pipeline_of(t)[-1], ("verify", "ready"))

    def test_needs_input_round_trip(self):
        t = self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        self.k.submit(c.run["id"], {"kind": "needs_input", "question": "English or Hindi?"})
        self.assertEqual(self.status(t), "needs_input")
        self.k.answer(t["id"], "Hindi")
        c = self.k.claim("plan", self.hod)
        self.assertIn("Hindi", (c.brief_dir / "context.md").read_text())

    def test_roles_and_status_enforced(self):
        self.add(dept="eng")
        with self.assertRaises(TransitionError):
            self.k.claim("approve", self.dev1)
        self.k.org.set_status(self.hod, "paused", "vacation")
        with self.assertRaises(TransitionError):
            self.k.claim("plan", self.hod)

    def test_fire_reassigns_open_work_and_approvals(self):
        t = self.add(dept="eng", assignee=self.dev1)
        self.k.org.set_authority(f"emp:{self.ceo}", "*", 4, "test")
        approval = self.plan(self.add("other", dept="eng"))
        self.assertEqual(approval["approver"], f"emp:{self.ceo}")
        heir = self.k.org.hire(name="Fa", title="CEO", dept="executive", job_description=JD, roles=["planner"],
                               driver="fake", reason="new CEO", status="active")["id"]
        self.k.org.fire(self.ceo, "left", successor=heir)
        self.assertEqual(self.k.work.get(approval["id"])["approver"], f"emp:{heir}")
        self.k.org.fire(self.dev1, "left", successor=self.dev2)
        self.assertEqual(self.k.work.get(t["id"])["assignee"], self.dev2)


class OptionalStages(KernelCase):
    pipeline = '[pipeline]\nretro = "never"\n[healing]\ndoctor_every_min = 0\n'

    def setUp(self):
        super().setUp()
        o = self.k.org
        self.k.org.create_dept("ops", "Operations", "Run things")
        self.lead = o.hire(name="Lu", title="Head of Ops", dept="ops", job_description=JD, slot="lead",
                           driver="fake", reason="r", status="active")["id"]
        self.maker = o.hire(name="Mo", title="Ops engineer", dept="ops", job_description=JD, slot="maker",
                            driver="fake", reason="r", status="active")["id"]
        self.checker = o.hire(name="Ce", title="Ops checker", dept="ops", job_description=JD, slot="checker",
                              driver="fake", reason="r", status="active")["id"]

    def test_novel_work_is_researched_then_shaped_then_planned(self):
        t = self.add("Rotate keys", dept="ops", task_class="key-rotation")
        self.assertEqual(self.pipeline_of(t), [("research", "ready")])
        c = self.k.claim(None, self.checker)
        self.assertEqual(c.node["stage"], "research")
        self.k.submit(c.run["id"], {"summary": "keys live in vault", "terrain": [
            {"content": "Keys are stored in the vault at /kv/app", "source": "vault ls"}]})
        self.assertTrue(any(i["provenance"].startswith("terrain:") for i in self.k.memory.live_items(["dept:ops"])))
        c = self.k.claim(None, self.lead)
        self.assertEqual(c.node["stage"], "shape")
        self.assertIn("Keys are stored in the vault", (c.brief_dir / "context.md").read_text())
        self.k.submit(c.run["id"], {"decision": "accept", "note": "ok", "acceptance": "old keys revoked",
                                    "assignee": self.maker})
        self.assertEqual(self.k.work.get(t["id"])["acceptance"], "old keys revoked")
        c = self.k.claim(None, self.maker)
        self.assertEqual(c.node["stage"], "plan")
        self.assertIn("From the shape stage", (c.brief_dir / "context.md").read_text())

    def test_shape_redirects_and_cancels(self):
        t = self.add("Wrong place", dept="ops", task_class="known", risk="low")
        self.k.wconn.execute("UPDATE tasks SET status='cancelled' WHERE parent_id=? AND stage='research'", (t["id"],))
        self.k.work._add_stage(self.k.work.get(t["id"]), "shape", [])
        c = self.k.claim("shape", self.lead)
        self.k.submit(c.run["id"], {"decision": "redirect", "note": "engineering owns this", "dept": "eng"})
        self.assertEqual(self.k.work.get(t["id"])["dept_id"], self.k.org.dept("eng")["id"])
        c = self.k.claim("shape", self.hod)
        self.k.submit(c.run["id"], {"decision": "cancel", "note": "duplicate of an existing task"})
        self.assertEqual(self.status(t), "cancelled")


class CollectiveMemory(KernelCase):
    def _run_lesson(self, executor, lesson):
        t = self.add(f"job for {executor}", dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=executor, writes=[])
        c = self.k.claim("execute", executor)
        return self.k.submit(c.run["id"], self.exec_env(learnings=[lesson]))

    def test_lesson_recurring_across_employees_is_promoted_to_department(self):
        out = self._run_lesson(self.dev1, "Always run the migration dry-run before applying it")
        self.assertEqual(out["memory"]["promotions"], [])
        out = self._run_lesson(self.dev2, "always run the migration dry run before applying")
        self.assertEqual(out["memory"]["promotions"][0]["to"], "dept:eng")

    def test_planner_and_verifier_learn_too(self):
        t = self.add(dept="eng", task_class="demo")
        c = self.k.claim("plan", self.hod, task_id=t["id"])
        self.k.submit(c.run["id"], self.plan_env(learnings=["plans here need a rollback line"]))
        self.assertTrue(any("rollback line" in i["content"] for i in self.k.memory.live_items([f"emp:{self.hod}"])))

    def test_successor_inherits_memory(self):
        self.k.memory.add_item(f"emp:{self.dev1}", "lesson", "The staging DB password rotates on Mondays", "test")
        heir = self.k.org.hire(name="Ed", title="Engineer", dept="eng", job_description=JD, roles=["executor"],
                               driver="fake", reason="replacement", status="active")["id"]
        self.k.org.fire(self.dev1, "role change", successor=heir)
        t = self.add("rotate", dept="eng", task_class="demo", assignee=heir)
        self.plan_and_approve(t, writes=[])
        c = self.k.claim("execute", heir)
        ctx = (c.brief_dir / "context.md").read_text()
        self.assertIn("inherited the role and memory", ctx)
        self.assertIn("staging DB password", ctx)


class Communication(KernelCase):
    def test_request_creates_work_in_other_department(self):
        self.k.org.create_dept("marketing", "Marketing")
        t = self.k.request(f"emp:{self.dev1}", "marketing", "Need launch copy", "Three tweets for v2")
        self.assertEqual(t["dept_id"], self.k.org.dept("marketing")["id"])
        self.assertEqual(t["requested_by"], f"emp:{self.dev1}")
        self.assertEqual(self.k.work.messages(task_id=t["id"])[0]["kind"], "request")

    def test_inbox_includes_department_channel(self):
        self.k.work.post_message("chair", "dept:eng", "standup moved", kind="channel")
        self.k.work.post_message(f"emp:{self.dev2}", f"emp:{self.dev1}", "can you review?")
        self.assertEqual({m["body"] for m in self.k.inbox(f"emp:{self.dev1}")}, {"standup moved", "can you review?"})


if __name__ == "__main__":
    unittest.main()


class RunPolicy(KernelCase):
    """H3+H5: policy.json is compiled at claim, carries the run's reach and budget,
    and refusals a harness reports are blockers, never breaches."""

    def configure(self, toml):
        self.home.config_file.write_text(self.pipeline + "\n" + self.config + "\n" + toml)
        self.k.close()
        self.k = Kernel(self.home)

    def test_a_claim_writes_policy_json_with_reach_and_budget(self):
        self.configure("[budget]\ntokens_per_run = 8000\n")
        w = self.add(dept="eng")
        claimed = self.k.claim("plan", self.hod, task_id=w["id"])
        policy = json.loads((claimed.brief_dir / "policy.json").read_text())
        self.assertEqual(policy["version"], 1)
        self.assertEqual((policy["stage"], policy["posture"]), ("plan", "observe"))
        self.assertEqual(policy["budget"]["tokens"], 8000)
        self.assertEqual(policy["budget"]["cost"], None)
        self.assertIn(str(self.workdir), policy["roots"], "the workdir is always a root")
        self.assertEqual(policy["writes"], [], "an observer has no declared writes")
        brief = json.loads((claimed.brief_dir / "brief.json").read_text())
        self.assertEqual(brief["policy"], str(claimed.brief_dir / "policy.json"))

    def test_budget_overrides_compile_in_order_org_dept_employee(self):
        self.configure("[budget]\ntokens_per_run = 100\n[budget.dept.eng]\ntokens_per_run = 200\n"
                       f"[budget.employee.{self.dev1}]\ntokens_per_run = 300\n")
        from holon.execution import policy as run_policy
        cfg = self.k.config
        self.assertEqual(run_policy.per_run_budget(cfg, "eng", self.dev1),
                         {"tokens": 300, "cost": None}, "employee beats department beats org")
        self.assertEqual(run_policy.per_run_budget(cfg, "eng", None)["tokens"], 200)
        self.assertEqual(run_policy.per_run_budget(cfg, None, None)["tokens"], 100)
        self.assertEqual(run_policy.per_run_budget({}, "eng", None), {"tokens": None, "cost": None},
                         "absent budget keys mean no cap")

    def test_execute_policy_carries_the_approved_plans_writes_and_act_commands(self):
        self.configure('[scope]\nroots = ["/srv/shared"]\ncommands = ["ls"]\nact_commands = ["git"]\n')
        w = self.add(dept="eng")
        self.plan_and_approve(w)
        claimed = self.k.claim("execute", self.dev1)
        policy = json.loads((claimed.brief_dir / "policy.json").read_text())
        self.assertEqual(policy["posture"], "act")
        self.assertIn("hello.txt", policy["writes"], "the approver saw these writes")
        self.assertIn("/srv/shared", policy["roots"])
        self.assertIn("git", policy["commands"], "act commands reach the act posture")
        self.assertIn("ls", policy["commands"])

    def test_observe_stages_never_see_act_commands(self):
        self.configure('[scope]\nact_commands = ["git"]\n')
        w = self.add(dept="eng")
        claimed = self.k.claim("plan", self.hod, task_id=w["id"])
        policy = json.loads((claimed.brief_dir / "policy.json").read_text())
        self.assertNotIn("git", policy["commands"])

    def test_reported_refusals_are_blockers_not_breaches(self):
        w = self.add(dept="eng")
        self.plan_and_approve(w)
        claimed = self.k.claim("execute", self.dev1)
        out = self.k.submit(claimed.run["id"], self.exec_env(
            refusals=[{"tool": "terminal", "reason": "rm -rf outside roots", "policy": "writes"}]))
        self.assertIn("refusals", out)
        events = [e["note"] for e in self.k.wconn.execute(
            "SELECT note FROM events WHERE entity='run' AND entity_id=?", (claimed.run["id"],))]
        self.assertTrue(any(n and "policy refusal: terminal" in n for n in events), events)
        self.assertEqual(self.status(w), "open",
                     "the refusal was recorded; the work proceeds to verification, never a breach")


class RunUsage(KernelCase):
    """H4 (phase D): a result may carry `usage` (recorded on the run; a usage the driver
    measured wins), and a harness may write trajectory.json beside result.json. The last
    verified trajectory becomes the gold standard the next run's brief and the judge are
    held to, compacted. Absent or malformed, it is dropped, never fatal."""

    def _executed(self, trajectory=None, **over):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        if trajectory is not None:
            (self.home.run_dir(c.run["id"]) / "trajectory.json").write_text(
                trajectory if isinstance(trajectory, str) else json.dumps(trajectory))
        return t, c, self.k.submit(c.run["id"], self.exec_env(**over))

    def test_usage_in_the_result_is_recorded_on_the_run(self):
        t, c, out = self._executed(usage={"tokens_in": 100, "tokens_out": 50, "cost": 0.25,
                                          "tool_calls": 7, "duration_s": 12.5})
        self.assertFalse(out.get("breach"))
        run = self.k.work.get_run(c.run["id"])
        self.assertEqual((run["tokens_in"], run["tokens_out"], run["cost"]), (100, 50, 0.25))
        self.assertEqual((run["tool_calls"], run["duration_s"]), (7, 12.5))

    def test_the_verified_trajectory_becomes_the_gold_standard(self):
        t, c, out = self._executed(trajectory={"steps": [
            {"thought": "read it", "tool": "file_read", "args": "hello.txt", "outcome": "empty"},
            {"tool": "file_write", "args": "hello.txt", "outcome": "written"}]})
        v = self.k.claim("verify", self.dev2)
        self.k.submit(v.run["id"], {"verdict": "pass", "note": "criteria met"})
        again = self.add("again", dept="eng", task_class="demo")
        gold = self.k.work.gold_standard(again)
        self.assertIn("file_write", gold["trajectory_text"], "the trajectory the judge is held to")
        self.plan_and_approve(again, assignee=self.dev1, writes=[])
        c2 = self.k.claim("execute", self.dev1)
        ctx = (c2.brief_dir / "context.md").read_text()
        self.assertIn("Gold standard trajectory", ctx)
        self.assertIn("file_write", ctx)

    def test_a_long_trajectory_is_compacted(self):
        t, c, out = self._executed(trajectory={"steps": [
            {"tool": f"step{i}", "outcome": "ok"} for i in range(30)]})
        body = self.k.work.last_execute_output(t["id"])["body"]
        self.assertEqual(len(body["_trajectory"]["steps"]), 12)
        self.assertEqual(body["_trajectory"]["more"], 18)

    def test_a_bad_trajectory_is_dropped_not_fatal(self):
        for bad in ('{"steps": "not a list"}', 'nope', '{"steps": [42, "x"]}'):
            t, c, out = self._executed(trajectory=bad)
            self.assertFalse(out.get("breach"), bad)
            self.assertNotIn("_trajectory", self.k.work.last_execute_output(t["id"])["body"])
