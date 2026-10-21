"""The work graph end to end, in pull mode (no harness involved)."""

import json
import sys
import unittest
from pathlib import Path

from holon.execution import policy as RUN_POLICY
from holon.execution.service import Kernel
from holon.execution.work import TransitionError, Work
from tests.helpers import FAKE_AGENT, REPO
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


class CancelledWorkLeavesNoLiveStage(KernelCase):
    def test_a_cancelled_work_stage_is_cancelled_too(self):
        t = self.add(dept="eng", task_class="demo")
        self.k.work._add_stage(self.k.work.get(t["id"]), "retro", [],
                               blocked_reason="kernel: verifier failure opened refinement")
        retro = self.k.work.stages(t["id"], "retro")[0]
        self.assertEqual(retro["status"], "ready")
        self.k.work.cancel(t["id"], "superseded by the next version")
        still = self.k.work.stages(t["id"], "retro")[0]
        self.assertNotEqual(still["status"], "ready",
                           "a stage on cancelled work stays claimable, so a body redoes work that was withdrawn")
        self.assertIsNone(self.k.claim(None, self.hod, task_id=t["id"]),
                           "a cancelled work offers nothing to claim")

    def test_a_repeat_cancel_still_sweeps_the_stage_it_left_behind(self):
        """The kernel's department-retro loop re-opens a root whose retro stage already says
        ready, so the stage is what survives the first cancel and is still claimable. Cancelling
        that root again must clear it: the sweep runs even when the work is already terminal."""
        t = self.add(dept="eng", task_class="demo")
        self.k.work._add_stage(self.k.work.get(t["id"]), "retro", [], blocked_reason="still ready")
        self.assertEqual(self.status(t), "open")
        self.k.work.cancel(t["id"], "the Chair withdrew it")
        self.assertEqual(self.status(t), "cancelled")
        self.assertEqual(self.k.work.stages(t["id"], "retro")[0]["status"], "cancelled")
        # the kernel's retro loop adds a fresh stage while the root stays cancelled
        again_id = self.k.work._add_stage(self.k.work.get(t["id"]), "retro", [],
                                          blocked_reason="re-opened")["id"]
        self.assertEqual(self.k.work.get(again_id)["status"], "ready", "the loop's fresh stage is claimable")
        self.k.work.cancel(t["id"], "and again: its stage is still claimable")
        self.assertEqual(self.k.work.get(again_id)["status"], "cancelled",
                         "a repeat cancel must sweep the stage the loop left behind")
        self.assertIsNone(self.k.claim(None, self.hod, task_id=t["id"]))

    def test_a_cancelled_refinement_queues_nothing_new(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        self.k.submit(c.run["id"], self.exec_env())
        self.k.verify(t["id"], "fail", "the write is missing")
        self.assertEqual(self.pipeline_of(t)[-1], ("execute", "ready"))
        self.k.work.cancel(t["id"], "the Chair made this moot")
        self.assertEqual([s for s in self.k.work.stages(t["id"]) if s["status"] in ("ready", "claimed")], [],
                         "cancelled work holds no live stage, so nothing re-runs it")


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

    def test_an_observe_stage_can_probe_what_it_reads(self):
        """G-d (measured 2026-10-21): an observe body had no shell at all — policy.json carried
        `commands: []`, so an enforced harness refused even `ls` and recorded it as a refusal the
        kernel then read as a blocker. The observe posture must name the read-only commands a
        researcher or planner needs to see what it is planning, and must not inherit the department's
        act commands while doing it."""
        self.configure('[scope]\ncommands = ["make"]\nact_commands = ["deploy"]\n')
        w = self.add(dept="eng")
        claimed = self.k.claim("plan", self.hod, task_id=w["id"])
        policy = json.loads((claimed.brief_dir / "policy.json").read_text())
        self.assertTrue(policy["commands"], "an observer may read what it plans")
        for probe in ("ls", "cat", "stat"):
            self.assertIn(probe, policy["commands"], f"{probe} is a read-only probe, not an act")
        self.assertNotIn("make", policy["commands"], "a build tool is not a read-only probe")
        self.assertNotIn("deploy", policy["commands"], "act commands stay out of the observe posture")

    def test_the_observers_commands_are_the_read_only_vocabulary_and_nothing_else(self):
        """The vocabulary is the filter. A read command the department names but the vocabulary does
        not carry (a search tool, a build tool) is dropped, and so is a mutating one: a department
        cannot widen an observer into a writer, nor into a reader of something the kernel never
        declared, by editing its own scope."""
        self.configure('[scope]\ncommands = ["rg", "make"]\nact_commands = ["deploy"]\n')
        w = self.add(dept="eng")
        claimed = self.k.claim("plan", self.hod, task_id=w["id"])
        policy = json.loads((claimed.brief_dir / "policy.json").read_text())
        self.assertEqual(set(policy["commands"]), set(RUN_POLICY.READ_ONLY_COMMANDS),
                         "the observer's list is exactly the read-only vocabulary")

    def test_a_brief_tells_the_observer_it_may_read(self):
        """The machine-readable policy and the human-readable brief must agree: G-d was a body that
        had neither a command list nor a way to learn one. The reach section is suppressed outright
        when a department declares no scope, so the posture's own commands need rendering there too."""
        w = self.add(dept="eng")
        claimed = self.k.claim("plan", self.hod, task_id=w["id"])
        brief = (claimed.brief_dir / "BRIEF.md").read_text()
        self.assertIn("## Reading what you plan", brief)
        for probe in ("ls", "cat"):
            self.assertIn(f"`{probe}`", brief, "a body is told what it may read with")
        self.assertIn("policy.json", brief,
                      "the brief points at the same document a harness enforces")

    def test_an_act_stage_brief_does_not_promise_read_only_commands(self):
        """The read-only statement is the observe posture's. An executor's reach is the act posture's
        and is rendered where it was; a body about to write must not be told it may only read."""
        w = self.add(dept="eng")
        self.plan_and_approve(w)
        claimed = self.k.claim("execute", self.dev1, task_id=w["id"])
        brief = (claimed.brief_dir / "BRIEF.md").read_text()
        self.assertNotIn("## Reading what you plan", brief)

    def test_a_body_writing_its_own_result_is_not_a_read_only_breach(self):
        """G-a (measured 2026-10-21): the observe posture disabled every file-writing tool while the
        brief named `$result_path` first, so a real planner produced a complete envelope, called
        `write`, was refused, and stopped with the plan in its context — the kernel then recorded a
        contract breach for a run that had done all of its work.

        The write tools are available in observe, and the fence is the kernel's own working-directory
        guard, which excludes the run directory. This test proves that exclusion is real: a run whose
        body writes its result into the run directory is accepted, and one that writes into the
        WORKING directory is still a breach."""
        guard = self.k.config["planner"]
        self.assertTrue(guard["enforce_read_only"], "the working-directory guard is on")
        self.assertFalse(guard["trust_harness_read_only"],
                         "jcode cannot fence observe writes itself, so the kernel's guard must run")
        w = self.add(dept="eng")
        claimed = self.k.claim("plan", self.hod, task_id=w["id"])
        # the run directory is outside the workdir: writing a result there is the body's hand-in
        self.assertFalse(claimed.brief_dir.resolve().is_relative_to(Path(str(self.workdir)).resolve()),
                         "the run directory is not inside the working directory, so the guard's "
                         "snapshot of the workdir cannot see a result.json written there")

    def test_the_driver_keeps_a_write_path_in_the_observe_posture(self):
        """The tool list is not the fence. A driver observe posture that disables every file tool takes
        away the brief's first hand-in path, and a body that cannot hand in produces nothing."""
        from holon.execution import drivers
        drv = drivers.Driver.from_toml((Path(REPO) / "adapters" / "jcode" / "driver.toml").read_text(),
                                        "jcode")
        off = {t.strip() for t in drv.read_only.replace("--disabled-tools", "").split(",") if t.strip()}
        self.assertNotIn("write", off,
                         "the observe posture must not disable the file tools: the brief names result.json")
        for tool in ("edit", "patch", "apply_patch", "replace", "multiedit"):
            self.assertNotIn(tool, off, f"{tool} can write a result: it stays available in observe")
        self.assertIn("selfdev", off, "a harness's own tools are still off")
        self.assertFalse(drv.read_only_enforced,
                         "the harness does not fence writes itself, so the kernel's guard stays on")

    def test_a_read_only_command_list_is_enforced_nothing_else_is_an_act(self):
        """The read-only list is a fixed vocabulary, not a passthrough: an instance that lists a
        mutating command in [scope] commands still cannot make an observe posture able to run it."""
        self.configure('[scope]\ncommands = ["rm", "ls"]\n')
        w = self.add(dept="eng")
        claimed = self.k.claim("plan", self.hod, task_id=w["id"])
        policy = json.loads((claimed.brief_dir / "policy.json").read_text())
        self.assertIn("ls", policy["commands"])
        self.assertNotIn("rm", policy["commands"], "a mutating command is not a read-only probe")

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


class RefusalBlockers(KernelCase):
    """H-03 (integration audit): refusals fold into blockers BEFORE the result is stored and
    judged — an execute that succeeded by shrinking from its plan never verifies clean
    without a decision on what it refused."""

    def test_a_success_with_refusals_carries_blockers_into_the_stored_result(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        out = self.k.submit(c.run["id"], self.exec_env(
            refusals=[{"tool": "terminal", "reason": "rm -rf denied by policy", "rule": "no-destructive"}]))
        self.assertFalse(out.get("breach"))
        body = self.k.work.last_execute_output(t["id"])["body"]
        self.assertIn("refused: terminal under policy (no-destructive)", body.get("blockers", []),
                      "the blockers CONTRACT §10 promises are in the stored result")
        v = self.k.claim("verify", self.dev2)
        ctx = (v.brief_dir / "context.md").read_text()
        self.assertIn("refused: terminal under policy", ctx, "the verifier sees what was refused")
    def test_a_clean_success_has_no_blockers(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        out = self.k.submit(c.run["id"], self.exec_env())
        self.assertFalse(out.get("breach"))
        self.assertEqual(self.k.work.last_execute_output(t["id"])["body"].get("blockers", []), [])

    def test_a_refusal_the_body_wrote_only_to_disk_is_not_lost(self):
        """Measured 2026-10-21: `refusals.jsonl` is written by the harness into the run directory
        and read by nothing in the kernel (grep: zero readers). The contract routes refusals through
        `result.refusals[]`, and H-03 turns them into blockers — so a body that records a refusal
        the harness's own way, and does not mirror it into its envelope, has it silently dropped.
        That is the same silence as the importers' swallowed skip, one layer down: the kernel must
        read what the body actually wrote, wherever it wrote it."""
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        # the harness's own record, in the run dir, with the envelope saying nothing
        (c.brief_dir / "refusals.jsonl").write_text(
            '{"tool": "terminal", "reason": "rm -rf denied by policy", "rule": "no-destructive"}\n')
        out = self.k.submit(c.run["id"], self.exec_env())
        self.assertIn("refusals", out, "the on-disk refusals reach the submit output")
        body = self.k.work.last_execute_output(t["id"])["body"]
        self.assertIn("refused: terminal under policy (no-destructive)", body.get("blockers", []),
                      "a refusal the body wrote to disk is a blocker, not lost")

    def test_the_envelopes_refusals_win_and_nothing_is_doubled(self):
        """The envelope is the body's own hand-in and stays authoritative; the disk file is a
        fallback for a body that recorded it the harness's way. One list, never both."""
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        (c.brief_dir / "refusals.jsonl").write_text(
            '{"tool": "terminal", "reason": "the disk copy", "rule": "r"}\n')
        out = self.k.submit(c.run["id"], self.exec_env(
            refusals=[{"tool": "terminal", "reason": "the envelope copy", "rule": "r"}]))
        self.assertEqual([r["reason"] for r in out["refusals"]], ["the envelope copy"],
                         "the envelope wins; the disk copy is not added alongside it")


class UsageEveryStage(KernelCase):
    """H-04 (integration audit): usage is recorded for every stage, not just execute; what the
    harness measured in usage.json wins over the envelope's claim; a run that names no build
    falls back to its driver's build_cmd."""

    def test_a_plan_run_records_its_usage(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan(t, usage={"tokens_in": 11, "tokens_out": 7, "cost": 0.02})
        plan_node = self.k.work.stages(t["id"], "plan")[0]
        run = self.k.work.get_run(self.k.wconn.execute(
            "SELECT run_id FROM outputs WHERE task_id=?", (plan_node["id"],)).fetchone()[0])
        self.assertEqual((run["tokens_in"], run["tokens_out"], run["cost"]), (11, 7, 0.02),
                         "cost per verified result counts every stage")

    def test_usage_json_beats_the_envelope_and_the_driver_names_the_build(self):
        (self.home.drivers_dir / "fake.toml").write_text(
            f'name = "fake"\ncommand = "{sys.executable} {FAKE_AGENT}"\ntimeout_s = 60\n'
            f'build_cmd = "echo harness-7.1.0"\n[env]\nPYTHONPATH = "{REPO}"\n')
        self.k.close(); self.k = Kernel(self.home)   # reload the drivers
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        (self.home.run_dir(c.run["id"]) / "usage.json").write_text(
            json.dumps({"tokens_in": 100, "tokens_out": 40, "duration_s": 5.0, "build": "measured-9"}))
        self.k.submit(c.run["id"], self.exec_env(usage={"tokens_in": 1, "tokens_out": 1, "cost": 9.9}))
        run = self.k.work.get_run(c.run["id"])
        self.assertEqual((run["tokens_in"], run["tokens_out"]), (100, 40), "the measurement wins")
        self.assertEqual(run["harness_build"], "measured-9")

    def test_a_run_without_a_build_gets_the_drivers(self):
        (self.home.drivers_dir / "fake.toml").write_text(
            f'name = "fake"\ncommand = "{sys.executable} {FAKE_AGENT}"\ntimeout_s = 60\n'
            f'build_cmd = "echo harness-7.1.0"\n[env]\nPYTHONPATH = "{REPO}"\n')
        self.k.close(); self.k = Kernel(self.home)
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        self.k.submit(c.run["id"], self.exec_env(usage={"tokens_in": 5}))
        self.assertEqual(self.k.work.get_run(c.run["id"])["harness_build"], "harness-7.1.0",
                         "pass rates group by the driver's build when nothing else names one")


class DeptSecrets(KernelCase):
    """G8: a department's secrets are 0600 key-file references under [secrets.dept.<slug>],
    resolved into the run's environment at claim. A run sees its own department's secrets and no
    other's; a key file that cannot be read refuses the run — never an empty string; lint flags
    a secret whose value is the secret itself."""

    def _configure(self, extra):
        self.home.config_file.write_text(self.home.config_file.read_text() + "\n" + extra)
        self.k.close()
        self.k = Kernel(self.home)

    def test_a_run_sees_its_own_depts_secrets_and_no_other_depts(self):
        o = self.k.org
        o.create_dept("ops", "Operations", "Run things")
        (self.tmp / "eng.key").write_text("eng-imap-secret\n")
        (self.tmp / "ops.key").write_text("ops-bot-secret\n")
        (self.tmp / "eng.key").chmod(0o600)
        self._configure(f'[secrets.dept.eng]\nIMAP_KEY = "{self.tmp / "eng.key"}"\n'
                        f'[secrets.dept.ops]\nREDDIT_KEY = "{self.tmp / "ops.key"}"\n')
        w = self.add(dept="eng")
        self.plan_and_approve(w, writes=[])
        p = self.k.prepare_dispatch(self.dev1, "execute")
        self.assertEqual(p["env"]["IMAP_KEY"], "eng-imap-secret", "resolved from the key file at claim")
        self.assertNotIn("REDDIT_KEY", p["env"], "another department's secret never reaches this run")

    def test_a_push_mode_body_receives_its_departments_secret(self):
        (self.tmp / "eng.key").write_text("eng-imap-secret\n")
        self._configure(f'[secrets.dept.eng]\nIMAP_KEY = "{self.tmp / "eng.key"}"\n')
        self.install_fake_driver("fake", mode="env-probe")
        w = self.add(dept="eng")
        self.plan_and_approve(w, writes=[])
        self.k.dispatch(self.dev1, "execute")
        doc = self.k.work.last_execute_output(w["id"])["documentation"]
        self.assertIn("probe: IMAP_KEY='eng-imap-secret'", doc, "the body's environment carried the secret")

    def test_an_unreadable_key_file_refuses_the_run_cleanly(self):
        self._configure(f'[secrets.dept.eng]\nIMAP_KEY = "{self.tmp / "missing.key"}"\n')
        w = self.add(dept="eng")
        self.plan_and_approve(w, writes=[])
        self.assertIsNone(self.k.dispatch(self.dev1, "execute"),
                          "refused — the run never starts with an empty secret")
        self.assertIn(("execute", "ready"), self.pipeline_of(w), "the stage returns to ready")
        notes = " ".join(r[0] for r in self.k.wconn.execute(
            "SELECT note FROM events WHERE note LIKE '%secret refused:%'"))
        self.assertIn("IMAP_KEY", notes, "the refusal names the variable")
        self.assertIn("missing.key", notes, "and the file")
