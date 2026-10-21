"""Playbooks that improve from their own evidence, objectives that steer a pool of work and routines, and the
smoke gate (docs/BYLAWS.md §12–13)."""

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
        self.assertIn("hello.txt lives at the workdir root", ref["intent"], "what the run learned is evidence too")
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

    def test_refinement_skips_research_because_its_evidence_is_the_research(self):
        self.pipeline = self.pipeline.replace('research = "never"', 'research = "auto"')
        self.config = "[autonomy]\nverify_sample_rate = 0\n"  # a new task class is novel: research would run
        self.configure("")
        self.k.org.hire(name="Rae", title="Researcher", dept="eng", job_description="Maps terrain.",
                        roles=["researcher"], driver="fake", reason="research", status="active")
        plan = self.routine_on()
        self.run_once(plan, judge_p=0.4, verify="fail")
        ref = [t for t in self.k.triggers() if t.get("refinement")][0]["refinement"]
        self.assertEqual([s["stage"] for s in self.k.work.stages(ref)], ["plan"])

    def test_a_plan_naming_a_skill_file_by_path_is_a_skill_change(self):
        plan = self.routine_on()
        self.run_once(plan, judge_p=0.4, verify="fail")
        ref = [t for t in self.k.triggers() if t.get("refinement")][0]["refinement"]
        path = self.k.skills.instance / "@eng" / NAME / "rubric.md"
        a = self.plan(self.k.work.get(ref), writes=[str(path)])
        body = self.k.work.plan_for_approval(a["id"])["output"]["body"]
        self.assertEqual(body["writes"], [f"skill:{NAME}/rubric.md"])
        self.assertIn("changes how the department works", self.k.work.plan_for_approval(a["id"])["grade_reasons"])

    def test_an_executor_that_edits_a_playbook_directly_has_the_edit_held_until_verified(self):
        plan = self.routine_on()
        self.run_once(plan, judge_p=0.4, verify="fail")
        ref = self.k.work.get([t for t in self.k.triggers() if t.get("refinement")][0]["refinement"])
        self.k.approve(self.plan(ref, writes=[f"skill:{NAME}/rubric.md"])["id"])
        rubric = self.k.skills.instance / "@eng" / NAME / "rubric.md"
        before = rubric.read_text()
        c = self.k.claim("execute", self.dev1, task_id=ref["id"])
        rubric.write_text(before + "- a criterion nobody verified\n")  # the bypass: no skill_changes
        (self.k.skills.instance / "@eng" / NAME / "SKILL.md").write_text("vandalised")  # not in the plan
        self.k.submit(c.run["id"], self.exec_env())
        self.assertEqual(rubric.read_text(), before, "taken back until verified")
        self.assertNotEqual((self.k.skills.instance / "@eng" / NAME / "SKILL.md").read_text(), "vandalised")
        self.assertTrue(any("lane breach" in (e["note"] or "") for e in self.k.work.events(ref["id"])))
        v = self.k.claim("verify", self.hod, task_id=ref["id"])
        self.k.submit(v.run["id"], {"verdict": "pass", "note": "the new criterion is right"})
        self.assertIn("a criterion nobody verified", rubric.read_text(), "applied once verified")
        self.assertEqual(playbooks.read(self.k.skills, "eng", NAME)["version"], 2)

    def test_a_failed_execution_does_not_leave_a_direct_skill_edit_behind(self):
        self.routine_on()
        rubric = self.k.skills.instance / "@eng" / NAME / "rubric.md"
        before = rubric.read_text()
        w = self.add(dept="eng", task_class="x")
        self.plan_and_approve(w)
        c = self.k.claim("execute", self.dev1, task_id=w["id"])
        rubric.write_text("- hijacked\n")
        self.k.submit(c.run["id"], self.exec_env(outcome="failure"))
        self.assertEqual(rubric.read_text(), before)

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


class LegacyImport(LaneCase):
    def test_a_legacy_cron_protocol_becomes_a_routine_proposal(self):
        from holon.cli import legacy_cron
        pool = self.tmp / "acme"
        (pool / "cron-jobs").mkdir(parents=True)
        (pool / "scripts").mkdir()
        (pool / "scripts" / "digest.py").write_text("print('ok')\n")
        f = pool / "cron-jobs" / "daily-digest.md"
        f.write_text('---\nname: "daily-digest"\ndescription: "One digest a day:\n  the actionable items."\n'
                     'schedule: "0 11 * * *"\nstatus: "active"\nno_agent: true\ndeliver: "telegram:1:2"\n'
                     'script: digest.py\nskills:\n- digest\n---\n\n# daily-digest\n\nRuns the digest script.\n')
        r = legacy_cron(f)
        self.assertEqual((r["title"], r["cron"], r["deliver"], r["paused"]), ("daily-digest", "0 11 * * *", True, False))
        self.assertEqual(r["command"], f"python3 {pool / 'scripts' / 'digest.py'}")
        self.assertIn("the actionable items", r["intent"])
        self.assertIn("Runs the digest script", r["intent"])
        a = self.k.add_routine(r["title"], r["intent"], dept="eng", cron=r["cron"], command=r["command"],
                               workdir=r["workdir"], deliver=r["deliver"], strict=False)
        body = self.k.work.plan_for_approval(a["id"])["output"]["body"]
        self.assertEqual((body["lane"], body["deliver"]), ("script", True))

    def test_a_protocol_that_declares_no_status_is_not_silently_dropped(self):
        """Measured 2026-10-21 on the real corpus: every protocol in the only importable pool
        (`pioneer-qf/cron-jobs/*.md`) carries `job`, `schedule`, `runtime` and `owner` — and **no**
        `status` key. The importer read `paused: meta.get("status") != "active"`, so all four were
        treated as paused, and the apply's `except …: continue` skipped them with no record:
        `holon venture import --apply` reported `routines: 0` beside `protocols: 4`.

        Absence is not a declaration of pause. A protocol that says nothing about its status is
        active, because the operator is asking the kernel to import it; and a protocol that says
        `status: paused` is honoured, because that is a declaration. The whole migration foundation
        rests on this: an importer that silently drops what it was given is not an importer."""
        from holon.cli import legacy_cron
        pool = self.tmp / "acme"
        (pool / "cron-jobs").mkdir(parents=True)
        f = pool / "cron-jobs" / "daily-live-monitor.md"
        f.write_text('---\njob: "daily-live-monitor"\nschedule: "0 18 * * *"\nruntime: vps-systemd\n'
                     'owner: observability\n---\n\n# Daily live monitor\n\nPull the summary.\n')
        r = legacy_cron(f)
        self.assertEqual((r["title"], r["cron"]), ("daily-live-monitor", "0 18 * * *"))
        self.assertFalse(r["paused"], "no `status` key means not paused: silence is not a pause")
        # and it must be importable, not skipped. `LaneCase` wires a fake classifier that answers the
        # first criterion at 95% — which is `script` — so name what the classifier says: this is an
        # agent's job, and the routine lands in the agent lane.
        self.fake.answer = lambda state, questions: {
            qid: ({"type": "choice", "choice": "agent", "confidence": 0.9,
                   "probabilities": {c: (0.9 if c == "agent" else 0.1 / 3) for c in q["criteria"]}}
                  if qid == "lane" else {"type": "noul", "noul": 0.0})
            for qid, q in questions.items()}
        made = self.k.add_routine(r["title"], r["intent"], dept="eng", cron=r["cron"],
                                  workdir=r["workdir"], strict=False)
        self.assertTrue(made["id"], "a protocol with no status is imported, not dropped")
        self.assertEqual(self.k.work.get(made["id"])["lane"], "full",
                         "an agent-legible protocol is an agent's job, not a dropped one")

    def test_a_protocol_that_declares_paused_is_still_dropped(self):
        """The other half: an explicit pause is a declaration and is honoured, so the fix is a
        default and not a removal of the pause gate. The lifecycle words the corpus actually
        uses are all honoured — `decommissioned` and `retired` were already asserted by M1's own
        guard, and they are the words a protocol that is *done* is written with."""
        from holon.cli import legacy_cron
        pool = self.tmp / "acme"
        (pool / "cron-jobs").mkdir(parents=True)
        for word in ("paused", "decommissioned", "retired", "archived", "disabled"):
            f = pool / "cron-jobs" / f"{word}.md"
            f.write_text(f'---\njob: "{word}"\nschedule: "0 18 * * *"\nstatus: {word}\n---\n\n# {word}\n')
            self.assertTrue(legacy_cron(f)["paused"], f"status: {word} is a declaration of pause")
        # and the absence of the word is not
        f = pool / "cron-jobs" / "live.md"
        f.write_text('---\njob: "live"\nschedule: "0 18 * * *"\n---\n\n# Live\n')
        self.assertFalse(legacy_cron(f)["paused"])

    def test_the_venture_apply_reports_what_it_skipped(self):
        """A drop must be visible. `except …: continue` inside the import's apply made every
        failure invisible, so a reader could not tell a clean import from one that lost every
        protocol — which is exactly how `--apply` came to report `routines: 0` beside
        `protocols: 4` with no warning at all. Both runs name their skips, with a reason."""
        from holon.execution import ventures
        pool = self.tmp / "acme"
        (pool / "cron-jobs").mkdir(parents=True)
        (pool / "cron-jobs" / "a.md").write_text(
            '---\njob: "a"\nschedule: "0 18 * * *"\nstatus: paused\n---\n\n# A\n')
        (pool / "cron-jobs" / "b.md").write_text(
            '---\njob: "b"\nschedule: "0 19 * * *"\n---\n\n# B\n')
        out = ventures.venture_import(self.k, pool, "acme", dry_run=True)
        self.assertEqual([s["title"] for s in out["skipped"]], ["a"],
                         "the paused protocol is named, not silently omitted")
        self.assertEqual(out["protocols"], 2)


class Objectives(LaneCase):
    def setUp(self):
        super().setUp()
        self.fake.choose(lane="fast", external=0.02)
        self.o = self.k.add_objective("Inbox zero for the Chair", "Nothing waits in the Chair's inbox unseen",
                                      dept="eng", key_results=["every thread triaged within a day"])

    def decompose(self, children):
        c = self.k.claim("plan", self.hod, task_id=self.o["id"])
        self.assertIn("## Objective dashboard", (c.brief_dir / "context.md").read_text())
        self.assertIn("- `executive`:", (c.brief_dir / "context.md").read_text(), "planners see who else to ask")
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
