"""Playbooks that improve from their own evidence, objectives that steer a pool of work and routines, and the
smoke gate (docs/GENOME.md §12–13)."""

from holon.capabilities import playbooks
from holon.capabilities.decisions import Decider
from holon.integrity import doctor, lint
from tests.fake_decisions import FakeDecisions
from tests.test_lanes import LaneCase

NAME = "routine-inbox-digest"


class Playbooks(LaneCase):
    def setUp(self):
        super().setUp()
        self.fake.choose(lane="fast", external=0.02)

    def routine_on(self, rubric=("names every unread thread", "fits in ten lines")):
        a, plan = self.routine("Inbox digest", rubric=list(rubric))
        self.k.approve(a["id"])
        return plan

    def run_once(self, plan, judge_p=0.95, verify=None):
        self.fake.answer = lambda s, q: {x: {"type": "noul", "noul": judge_p} for x in q}
        w = self.fire(plan)
        out = self.k.dispatch(self.dev1, "execute", w["id"])
        if verify:
            self.k.verify(w["id"], verify, f"verifier says {verify}: the digest missed a thread")
        return w, out

    def refine(self, title, files):
        """Carry a refinement through its pipeline: plan (naming the skill writes), approve, execute, verify."""
        w = next(t for t in self.k.work.list(limit=200) if t["title"] == title)
        approval = self.plan(w, writes=[f"skill:{NAME}/{f}" for f in files])
        self.k.approve(approval["id"])
        c = self.k.claim("execute", self.dev1, task_id=w["id"])
        self.k.submit(c.run["id"], self.exec_env(skill_changes=[
            {"skill": NAME, "file": f, "content": text, "why": "the verifier keeps catching missed threads"}
            for f, text in files.items()]))
        v = self.k.claim("verify", self.hod, task_id=w["id"])
        return self.k.submit(v.run["id"], {"verdict": "pass", "note": "the new rubric checks what failed"})

    def test_an_approved_routine_gets_a_playbook_its_runs_follow_and_are_judged_by(self):
        plan = self.routine_on()
        pb = playbooks.read(self.k.skills, "eng", NAME)
        self.assertEqual((pb["version"], pb["rubric"]), (1, ["names every unread thread", "fits in ten lines"]))
        self.assertEqual(self.k.skills.check("eng"), [])
        w, out = self.run_once(plan)
        ctx = (self.home.run_dir(out["run"]["id"]) / "context.md").read_text()
        self.assertIn(f"## Playbook: {NAME} v1", ctx)
        self.assertEqual((self.k.work.get(w["id"])["playbook"], self.k.work.get(w["id"])["playbook_version"]), (NAME, 1))
        rows = self.k.wconn.execute("SELECT criterion, version, passed FROM judgments WHERE work_id=?", (w["id"],)).fetchall()
        self.assertEqual({(r[0], r[1], r[2]) for r in rows},
                         {("names every unread thread", 1, 1), ("fits in ten lines", 1, 1)})

    def test_a_verifier_failure_opens_refinement_whose_verified_change_is_the_next_version(self):
        plan = self.routine_on()
        self.run_once(plan, judge_p=0.4, verify="fail")
        out = [t for t in self.k.triggers() if t.get("refinement")]
        self.assertEqual([o["playbook"] for o in out], [NAME])
        ref = self.k.work.get(out[0]["refinement"])
        self.assertIn("failed verification", ref["intent"])
        self.assertIn("names every unread thread: mean p=0.40", ref["intent"])
        self.assertIn("the digest missed a thread", ref["intent"])
        self.assertFalse([t for t in self.k.triggers() if t.get("refinement")], "one refinement at a time")
        new_rubric = "# Rubric\n\n- lists every unread thread by sender and subject\n- fits in ten lines\n"
        res = self.refine(ref["title"], {"rubric.md": new_rubric})
        self.assertTrue(res["skill_changes"][0]["applied"], res)
        pb = playbooks.read(self.k.skills, "eng", NAME)
        self.assertEqual((pb["version"], pb["rubric"][0]), (2, "lists every unread thread by sender and subject"))
        w, out = self.run_once(plan)
        ctx = (self.home.run_dir(out["run"]["id"]) / "context.md").read_text()
        self.assertIn(f"## Playbook: {NAME} v2", ctx)
        self.assertNotIn("## Rubric (each result", ctx, "the playbook is the one rubric: the plan's copy is v1")
        self.assertIn("one run of an approved routine", ctx)
        self.assertEqual({r[0] for r in self.k.wconn.execute("SELECT version FROM judgments WHERE work_id=?", (w["id"],))},
                         {2}, "later runs are judged against the refined rubric")

    def test_enough_runs_on_one_version_make_refinement_due_without_failures(self):
        self.configure("[playbooks]\nrefine_after = 2\n")
        plan = self.routine_on()
        self.run_once(plan)
        self.assertFalse([t for t in self.k.triggers() if t.get("refinement")])
        self.run_once(plan)
        self.assertTrue([t for t in self.k.triggers() if t.get("refinement")])

    def test_a_task_class_with_a_track_record_gets_its_first_playbook_written(self):
        self.configure("[playbooks]\nclass_after = 2\n")
        for i in range(2):
            w = self.add(f"greet {i}", dept="eng", task_class="greeting")
            self.plan_and_approve(w)
            c = self.k.claim("execute", self.dev1, task_id=w["id"])
            self.k.submit(c.run["id"], self.exec_env())
            self.k.verify(w["id"], "pass", "ok")
        made = [t for t in self.k.triggers() if t.get("refinement")]
        self.assertEqual([m["playbook"] for m in made], ["class-greeting"])
        self.assertTrue(self.k.work.get(made[0]["refinement"])["title"].startswith("Write playbook"))


class Delivery(LaneCase):
    def test_a_delivering_routine_sends_each_result_to_the_chair_once(self):
        sent = self.tmp / "sent.txt"
        self.configure(f'[notify]\ncommand = "echo {{message}} >> {sent}"\n')
        self.fake.choose(lane="fast", external=0.02)
        a, plan = self.routine("Inbox digest", deliver=True)
        self.k.approve(a["id"])
        self.fake.answer = lambda s, q: {x: {"type": "noul", "noul": 0.97} for x in q}
        w = self.fire(plan)
        self.k.dispatch(self.dev1, "execute", w["id"])
        self.assertEqual(self.k._deliver(), [w["id"]])
        self.assertEqual(self.k._deliver(), [], "once")
        self.assertIn("wrote hello.txt", sent.read_text())


class Objectives(LaneCase):
    def setUp(self):
        super().setUp()
        self.fake.choose(lane="fast", external=0.02)
        self.o = self.k.add_objective("Inbox zero for the Chair", "Nothing waits in the Chair's inbox unseen",
                                      dept="eng", key_results=["every thread triaged within a day"])

    def decompose(self, children):
        c = self.k.claim("plan", self.hod, task_id=self.o["id"])
        self.assertIn("## Objective dashboard", (c.brief_dir / "context.md").read_text())
        a = self.k.submit(c.run["id"], {"kind": "decompose", "rationale": "a pool", "children": children})["approval"]
        return self.k.approve(a["id"])

    def test_an_objective_becomes_a_pool_of_work_and_routines_that_its_review_steers(self):
        out = self.decompose([
            {"title": "Set up labels", "intent": "create triage labels"},
            {"title": "Daily triage", "intent": "label every new thread", "cron": "0 7 * * *",
             "rubric": ["every new thread has a label"]}])
        self.assertEqual([r.get("approved") for r in out["routines"]], [True], "the decider approves within its grade")
        child = self.k.work.get(out["children"][0])
        self.assertEqual(child["objective_id"], self.o["id"])
        routine = self.k.routines()[0]
        self.assertEqual(self.k.work.get(self.k.work.get(routine["plan"])["parent_id"])["objective_id"], self.o["id"])
        firing = self.k.work.instantiate_recurring(routine["plan"])
        self.assertEqual(firing["objective_id"], self.o["id"], "every firing serves the objective")
        self.k.work.cancel(child["id"], "done by hand")
        self.assertEqual(self.status(self.o), "waiting", "an objective is not closed by its children")
        self.assertIn("Daily triage", self.k.objective_dashboard(self.o["id"]))
        reviews = [t for t in self.k.triggers() if t.get("objective_review")]
        self.assertEqual([r["objective"] for r in reviews], [self.o["id"]], "its pool finished a cycle")
        steward = self.k.org.hire(name="Sam", title="Steward", dept="eng", job_description="Runs retros.",
                                  roles=["steward"], driver="fake", reason="reviews", status="active")["id"]
        c = self.k.claim("retro", steward, task_id=self.o["id"])
        self.assertIn("Daily triage", (c.brief_dir / "context.md").read_text())
        res = self.k.submit(c.run["id"], {"summary": "triage runs; labels missing", "cause": "no label set",
                                          "actions": [{"title": "Create labels", "intent": "make the labels"}]})
        self.assertEqual(self.k.work.get(res["created"][0])["parent_id"], self.o["id"])
        self.assertEqual(self.status(self.o), "waiting")
        self.k.work.cancel(res["created"][0], "x")
        self.k.triggers()
        c = self.k.claim("retro", steward, task_id=self.o["id"])
        self.k.submit(c.run["id"], {"summary": "every thread triaged within a day for a week", "cause": "-",
                                    "objective_met": True})
        self.assertEqual(self.status(self.o), "done")

    def test_a_deterministic_routine_a_planner_proposes_becomes_work_to_write_its_script(self):
        self.fake.choose(lane="script", external=0.02)
        out = self.decompose([{"title": "Archive old mail", "intent": "move mail older than 30 days",
                               "cron": "0 3 * * *"}, {"title": "x", "intent": "y"}])
        self.assertTrue(out["routines"][0].get("script_work"))
        self.assertEqual(self.k.work.get(out["routines"][0]["script_work"])["objective_id"], self.o["id"])


class Smoke(LaneCase):
    def test_a_failed_critical_check_blocks_the_department_until_it_passes(self):
        flag = self.tmp / "db-up"
        self.configure(f'[[smoke.dept.eng.checks]]\nname = "db"\ncmd = "test -f {flag}"\ncritical = true\n'
                       'reason = "the department reads it"\n')
        self.add(dept="eng")
        self.assertEqual([f["check"] for f in doctor.run(self.k) if f["check"].startswith("smoke")], ["smoke.critical"])
        self.assertIsNone(self.k.claim("plan", self.hod), "its agents wait")
        flag.write_text("")
        self.k.run_smoke()
        self.assertIsNotNone(self.k.claim("plan", self.hod))

    def test_a_check_for_everyone_spares_the_incident_department(self):
        self.k.org.create_dept("it", "IT", "fix things")
        self.configure('[[smoke.checks]]\nname = "gateway"\ncmd = "false"\ncritical = true\n')
        self.k.run_smoke()
        blocked = {self.k.org.dept(d)["slug"] for d in self.k.smoke_blocked()}
        self.assertIn("eng", blocked)
        self.assertNotIn("it", blocked)

    def test_lint_checks_what_the_instance_will_run(self):
        self.configure('[checks]\ncommands = ["no-such-tool --flag", "test -f /no/such/file"]\n'
                       '[scope.dept.eng]\nroots = ["/tmp"]\n')
        found = {(f["rule"], f["where"]) for f in lint.run(self.k, ["ref"])}
        self.assertIn(("ref.command", "[checks]"), found)
        self.assertIn(("ref.path", "[checks]"), found)
        self.assertIn(("ref.smoke", "dept:eng"), found)
        exes, paths = lint.command_refs("cd ~/x && FOO=1 python3 /a/b.py --x > /out/log | grep -v y")
        self.assertEqual((exes, paths), (["python3", "grep"], ["~/x", "/a/b.py"]), "a cd target must exist too")


class Fallback(LaneCase):
    def test_a_slow_second_endpoint_answers_only_decisions_that_can_wait(self):
        slow = FakeDecisions()
        self.addCleanup(slow.close)
        self.fake.status = 429
        cfg = {**self.k.config["classifier"], "fallback_url": slow.url, "fallback_model": "clef"}
        d = Decider(cfg)
        q = {"a": {"type": "noul", "instructions": "?"}}
        self.assertIsNone(d.ask("s", q), "an impatient caller takes its safe default")
        self.assertEqual(d.ask("s", q, patient=True)["a"]["noul"], 0.95)
        self.assertEqual(slow.requests[-1]["model"], "clef")
