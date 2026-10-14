"""Lanes, routines, the rubric judge and the decision model (docs/GENOME.md §10)."""

import json

from holon.capabilities.decisions import Decider
from holon.execution.service import Kernel
from tests.fake_decisions import FakeDecisions
from tests.helpers import KernelCase

ROUTINE_FREE = "[grading]\nnovelty_min_verified = 0\n[autonomy]\nverify_sample_rate = 0\n"


class LaneCase(KernelCase):
    config = ROUTINE_FREE
    classifier = True

    def setUp(self):
        super().setUp()
        self.fake = FakeDecisions()
        self.addCleanup(self.fake.close)
        self.configure("")

    def configure(self, extra):
        cls = (f'[classifier]\nurl = "{self.fake.url}"\nmodel = "m"\nkey_file = "{self.tmp / "c.key"}"\n'
               if self.classifier else "")
        (self.tmp / "c.key").write_text("sk-test\n")
        self.home.config_file.write_text(self.pipeline + "\n" + self.config + "\n" + cls + extra)
        self.k.close()
        self.k = Kernel(self.home)

    def routine(self, title="Digest", **kw):
        kw.setdefault("dept", "eng")
        kw.setdefault("cron", "0 7 * * *")
        kw.setdefault("workdir", str(self.workdir))
        a = self.k.add_routine(title, kw.pop("intent", "Summarise the inbox"), **kw)
        return a, self.k.work.plan_for_approval(a["id"])

    def fire(self, plan):
        return self.k.work.instantiate_recurring(plan["id"])


class FastLane(LaneCase):
    def test_routine_work_is_planned_and_done_in_one_session_and_closed_by_its_rubric(self):
        w = self.add(risk="low", task_class="c", lane="fast", dept="eng")
        self.assertEqual([s for s, _ in self.pipeline_of(w)], ["plan", "approve", "execute"])
        self.assertEqual(self.k.work.approved_plan(w["id"])["approval"]["decided_by"], "lane:fast")
        out = self.k.dispatch(self.dev1, "execute")
        self.assertTrue(out["verification_skipped"])
        self.assertEqual(self.status(w), "done")
        self.assertEqual(self.k.work.last_execute_output(w["id"])["verified_by"], "rubric")
        self.assertEqual(len(self.k.wconn.execute("SELECT * FROM runs").fetchall()), 1, "one agent session")
        ctx = (self.home.run_dir(out["run"]["id"]) / "context.md").read_text()
        self.assertIn("## Fast lane", ctx)

    def test_work_the_kernel_would_not_grade_g0_takes_the_full_pipeline_whoever_asks(self):
        w = self.add(risk="normal", task_class="c", lane="fast", dept="eng")
        self.assertEqual(self.k.work.get(w["id"])["lane"], "full")
        self.assertEqual(self.pipeline_of(w), [("plan", "ready")])
        self.assertTrue(any("not fast: its grade floor is G1" in (e["note"] or "") for e in self.k.work.events(w["id"])))

    def test_an_executor_who_finds_the_work_is_not_routine_sends_it_to_the_full_pipeline(self):
        w = self.add(risk="low", task_class="c", lane="fast", dept="eng")
        c = self.k.claim("execute", self.dev1, task_id=w["id"])
        self.k.submit(c.run["id"], self.exec_env(outcome="noop", blockers=["needs a schema migration"]))
        self.assertEqual(self.k.work.get(w["id"])["lane"], "full")
        self.assertEqual(self.pipeline_of(w)[-1], ("plan", "ready"))

    def test_a_result_beyond_routine_is_a_lane_breach_the_chair_hears_about(self):
        w = self.add(risk="low", task_class="c", lane="fast", dept="eng")
        c = self.k.claim("execute", self.dev1, task_id=w["id"])
        out = self.k.submit(c.run["id"], self.exec_env(writes=["/elsewhere/x.txt"], external_effects=["emailed a client"]))
        self.assertFalse(out["verification_skipped"])
        self.assertEqual(self.pipeline_of(w)[-1], ("verify", "ready"))
        self.assertTrue(any(line.startswith("lane breach") for line in self.k.pending_for_chair(0)))

    def test_an_agent_that_hands_in_its_own_result_is_still_judged_by_the_kernel(self):
        self.install_fake_driver("fake", mode="self-submit")
        w = self.add(risk="low", task_class="c", lane="fast", dept="eng")
        out = self.k.dispatch(self.dev1, "execute")
        self.assertTrue(out["submitted_by_agent"])
        self.assertTrue(out["verification_skipped"], "the dispatcher judged it after stopping the harness")
        self.assertEqual(self.status(w), "done")

    def test_a_deferred_result_is_held_from_verifiers_until_the_kernel_checks_it(self):
        w = self.add(risk="low", task_class="c", lane="fast", dept="eng")
        c = self.k.claim("execute", self.dev1, task_id=w["id"])
        self.k.submit(c.run["id"], self.exec_env(), defer=True)  # as an agent's own `holon submit`
        self.assertIsNone(self.k.claim("verify", self.dev2), "no verifier session before the checks")
        self.k.wconn.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00.000000Z' WHERE stage='verify'")
        self.k.wconn.commit()
        fired = self.k.tick(foreground=True)["fired"]
        self.assertEqual([f["post_execute"] for f in fired if "post_execute" in f], [w["id"]])
        self.assertEqual(self.status(w), "done", "the heartbeat judged it when no dispatcher did")

    def test_a_result_left_on_disk_by_a_dead_dispatcher_is_handed_in_not_redone(self):
        w = self.add(risk="low", task_class="c", lane="fast", dept="eng")
        c = self.k.claim("execute", self.dev1, task_id=w["id"])
        self.write_result(c.run["id"], self.exec_env())
        self.k.wconn.execute("UPDATE runs SET lease_until='2000-01-01T00:00:00.000000Z'")
        self.k.wconn.commit()
        out = self.k.tick(foreground=True)
        self.assertEqual(out["salvaged"], [c.run["id"]])
        self.assertEqual(out["swept"], [], "not counted as a dead run")
        self.assertEqual(self.k.work.latest(w["id"], "execute")["status"], "done")

    def test_an_unsure_rubric_keeps_the_verifier_and_tells_it_why(self):
        self.fake.answer = lambda s, q: {k: {"type": "noul", "noul": 0.4} for k in q}
        w = self.add(risk="low", task_class="c", lane="fast", dept="eng")
        self.k.dispatch(self.dev1, "execute")
        self.assertEqual(self.pipeline_of(w)[-1], ("verify", "ready"))
        self.assertTrue(any("Rubric judge" in m["body"] for m in self.k.work.messages(w["id"])))

    def test_sampled_results_measure_how_often_a_verifier_agrees_with_the_rubric_judge(self):
        self.configure("[autonomy.dept.eng]\nverify_sample_rate = 1.0\n")
        w = self.add(risk="low", task_class="c", lane="fast", dept="eng")
        self.k.dispatch(self.dev1, "execute")
        self.assertEqual(self.pipeline_of(w)[-1], ("verify", "ready"), "sampled: a verifier still looks")
        self.k.verify(w["id"], "pass", "checked by hand")
        m = self.k.metrics("eng")
        self.assertEqual((m["rubric_agreement"]["value"], m["rubric_agreement"]["n"]), (1.0, 1))
        self.assertEqual(m["work_by_lane"], {"fast": 1})


class Routines(LaneCase):
    def test_a_deterministic_job_must_be_a_script_and_the_kernel_runs_it_without_an_agent(self):
        self.fake.choose(lane="script", external=0.02)
        with self.assertRaisesRegex(ValueError, "deterministic"):
            self.routine("Rotate logs", intent="Move logs older than 7 days into archive/")
        a, plan = self.routine("Rotate logs", intent="Move old logs", command="echo rotated > rotated.txt",
                               rubric=["rotated.txt exists"])
        self.assertEqual((a["approver"], a["grade"]), ("chair", 2), "a routine runs unattended: G2")
        self.k.approve(a["id"])
        self.assertEqual([(r["lane"], r["enabled"]) for r in self.k.routines()], [("script", True)])
        w = self.fire(plan)
        self.assertEqual(w["lane"], "script")
        self.assertIsNone(self.k.claim("execute", self.dev1), "no agent claims a script stage")
        self.k.tick(foreground=True)
        self.assertEqual(self.status(w), "done")
        self.assertTrue((self.workdir / "rotated.txt").exists())
        self.assertEqual(self.k.work.last_execute_output(w["id"])["verified_by"], "rubric")

    def test_a_failing_script_is_retried_then_fails(self):
        self.fake.choose(lane="script", external=0.02)
        a, plan = self.routine("Check disk", command="exit 3")
        self.k.approve(a["id"])
        w = self.fire(plan)
        self.k.tick(foreground=True)
        self.k.tick(foreground=True)
        self.assertEqual(self.status(w), "failed")

    def test_without_a_decision_model_a_script_closes_on_its_exit_code_and_checks(self):
        self.classifier = False
        self.configure("")
        a, plan = self.routine("Touch", command="touch t.txt", lane="script")
        self.k.approve(a["id"])
        w = self.fire(plan)
        self.k.tick(foreground=True)
        self.assertEqual((self.status(w), self.k.work.last_execute_output(w["id"])["verified_by"]), ("done", "checks"))

    def test_the_lane_is_enforced_cheapest_first(self):
        self.fake.choose(lane="full", external=0.02)
        _, plan = self.routine("Weekly review", lane="fast")
        self.assertEqual(plan["output"]["body"]["lane"], "full", "a cheaper lane than the job needs is raised")
        self.fake.choose(lane="fast", external=0.02)
        with self.assertRaisesRegex(ValueError, "say --why"):
            self.routine("Inbox digest", lane="full")
        a, plan = self.routine("Inbox digest", lane="full", why="the Chair wants every digest verified")
        self.assertIn("the Chair wants", plan["grade_reasons"])
        self.fake.choose(lane="fast", external=0.9)
        a, _ = self.routine("Post the digest")
        self.assertEqual(a["grade"], 3, "reaching outside the organization: G3")

    def test_fast_routines_converge_on_their_gold_standard(self):
        self.fake.choose(lane="fast", external=0.02)
        a, plan = self.routine(rubric=["names every unread thread"])
        self.k.approve(a["id"])
        self.fake.answer = lambda s, q: {k: {"type": "noul", "noul": 0.3} for k in q}
        first = self.fire(plan)
        self.k.dispatch(self.dev1, "execute")
        self.k.verify(first["id"], "pass", "good digest")
        self.fake.answer = lambda s, q: {k: {"type": "noul", "noul": 0.97} for k in q}
        second = self.fire(plan)
        out = self.k.dispatch(self.dev1, "execute")
        ctx = (self.home.run_dir(out["run"]["id"]) / "context.md").read_text()
        self.assertIn(f"Gold standard: the last verified result of this job ({first['id']})", ctx)
        self.assertIn("names every unread thread", ctx)
        self.assertIn("gold", self.fake.requests[-1]["questions"], "the judge compares against the gold standard")
        self.assertEqual(self.status(second), "done")

    def test_a_full_routine_plans_each_firing_afresh(self):
        self.fake.choose(lane="full", external=0.02)
        a, plan = self.routine("Growth experiment")
        self.k.approve(a["id"])
        w = self.fire(plan)
        self.assertEqual((w["lane"], self.pipeline_of(w)), ("full", [("plan", "ready")]))


class Classifier(LaneCase):
    def test_intake_picks_the_lane_and_reaching_outside_means_full(self):
        self.fake.choose(lane="fast", external=0.02)
        self.assertEqual(self.k.intake_lane("t", "summarise notes")[0], "fast")
        self.fake.choose(lane="fast", external=0.9)
        self.assertEqual(self.k.intake_lane("t", "email the summary")[0], "full")
        self.fake.status = 500
        lane, cls = self.k.intake_lane("t", "x")
        self.assertEqual((lane, cls["source"]), ("full", "failed"))
        self.assertIn("HTTP 500", cls["error"])

    def test_a_decision_model_that_is_down_is_never_silent(self):
        self.fake.status = 429
        w = self.add(risk="low", task_class="c", lane="fast", dept="eng")
        self.k.dispatch(self.dev1, "execute")
        self.assertEqual(self.pipeline_of(w)[-1], ("verify", "ready"), "no judge: a verifier looks")
        line = [x for x in self.k.pending_for_chair(0) if x.startswith("decision model unavailable")]
        self.assertEqual(len(line), 1)
        self.assertIn("HTTP 429", line[0])

    def test_reports_to_the_chair_do_not_count_as_reaching_outside(self):
        self.k.classify("Inbox digest", "send the Chair a digest on Telegram")
        q = self.fake.requests[-1]["questions"]["external"]
        self.assertIn("Reports, digests and notifications to the Chair", q["instructions"])

    def test_the_key_is_sent_as_a_bearer_token_and_never_reaches_a_run(self):
        self.k.classify("t", "x")
        self.assertEqual(self.fake.requests[-1]["auth"], "Bearer sk-test")
        self.assertFalse(any("sk-test" in v for v in __import__("os").environ.values()))

    def test_answers_that_do_not_match_the_questions_are_no_opinion(self):
        self.fake.answer = lambda s, q: {"other": {"type": "noul", "noul": 1.0}}
        d = Decider(self.k.config["classifier"])
        self.assertIsNone(d.ask("s", {"a": {"type": "noul", "instructions": "?"}}))
        self.assertIn("do not match", d.error)

    def test_new_work_is_routed_to_the_department_whose_mission_fits(self):
        self.k.org.create_dept("ops", "Operations", "Keep machines and services running")
        self.fake.choose(dept="ops")
        self.assertEqual(self.k.route_dept("Disk full", "the VPS disk is at 95%")["dept"], "ops")
        self.fake.answer = lambda s, q: {"dept": {"type": "choice", "choice": "ops", "confidence": 0.5}}
        self.assertIsNone(self.k.route_dept("Disk full", "x")["dept"], "unsure: the executive shapes it")

    def test_an_incident_carries_the_decision_models_reading_of_the_failure(self):
        self.install_fake_driver("fake", mode="garbage")
        self.add(dept="eng")
        for _ in range(3):
            self.k.dispatch(self.hod, "plan")
        self.fake.choose(cause="brief")
        inc = [t for t in self.k.triggers() if t.get("incident")]
        intent = self.k.work.get(inc[0]["incident"])["intent"]
        self.assertIn("Likely cause (decision model, 95%): brief", intent)

    def test_classify_command_reports_the_lane(self):
        from holon import cli
        import contextlib
        import io
        self.fake.choose(lane="script", external=0.02)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["--home", str(self.home.root), "--json", "classify", "Rotate logs", "--intent", "move old logs"])
        self.assertEqual(json.loads(buf.getvalue())["lane"], "script")


class EventTriggers(LaneCase):
    """H7 (phase F): a routine may fire on an event pattern instead of a cron. The runtime
    that sees a matching event fires it (`holon routine fire`), once per cooldown, with the
    payload on the work; api_triggers (api_version 2) publishes it with trigger_kind 'event'."""

    def _routine(self):
        a = self.k.add_routine("Watch invoices", "Summarise each invoice", dept="eng",
                               event="event:webhook:invoice.*", lane="fast", why="each needs judgement",
                               workdir=str(self.workdir))
        approval = self.k.work.plan_for_approval(a["id"])
        self.k.approve(approval["id"])
        return next(r for r in self.k.schedules.list() if r["event"])

    def test_an_event_routine_is_approved_into_a_schedule_that_time_never_fires(self):
        s = self._routine()
        self.assertEqual((s["kind"], s["cron"]), ("recurring", ""))
        self.assertIsNone(s["next_run_at"])
        self.assertEqual(self.k.schedules.take_due(), [], "an event trigger has no clock")

    def test_fire_creates_work_carries_the_payload_and_cools_down(self):
        s = self._routine()
        out = self.k.fire_routine(s["id"], payload="invoice 42: $10")
        wid = out["recurring_work"]
        self.assertIn("invoice 42", self.k.work.messages(task_id=wid)[0]["body"])
        c = self.k.claim("execute", self.dev1)
        self.assertIn("invoice 42", (c.brief_dir / "context.md").read_text())
        self.assertTrue(self.k.fire_routine(s["id"]).get("skipped"), "within the cooldown")
        self.k.wconn.execute("UPDATE schedules SET next_run_at=NULL WHERE id=?", (s["id"],))
        self.assertTrue(self.k.fire_routine(s["id"]).get("skipped"), "the last firing's work is open")

    def test_api_triggers_v2_publishes_event_rows(self):
        s = self._routine()
        row = next(dict(r) for r in self.k.wconn.execute("SELECT * FROM api_triggers WHERE id=?", (s["id"],)))
        self.assertEqual((row["trigger_kind"], row["pattern"]), ("event", "event:webhook:invoice.*"))
        self.assertEqual(self.k.wconn.execute(
            "SELECT value FROM api_meta WHERE key='api_version'").fetchone()[0], "2")

    def test_a_routine_needs_a_cron_or_an_event(self):
        with self.assertRaises(ValueError):
            self.k.add_routine("No clock", "Nothing schedules it", dept="eng", workdir=str(self.workdir))


class BuildJudgments(LaneCase):
    """H10 (phase H): the build in result.usage is recorded on the run, and the rubric judge's
    rows carry the build they judged against — so pass rates group by build."""

    def test_judgments_carry_the_build_that_ran_the_result(self):
        w = self.add("Digest again", dept="eng", task_class="c", lane="fast", risk="low")
        c = self.k.claim("execute", self.dev1)
        self.k.submit(c.run["id"], self.exec_env(usage={"build": "harness-9"}))
        run = self.k.work.get_run(c.run["id"])
        self.assertEqual(run["harness_build"], "harness-9", "the envelope's build, recorded on the run")
        rows = self.k.wconn.execute("SELECT DISTINCT build FROM judgments WHERE work_id=?", (w["id"],)).fetchall()
        self.assertEqual([r[0] for r in rows], ["harness-9"], "the judge groups by that build")
