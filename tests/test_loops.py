"""R5: the feedback loops. Every loop has a sensor, a comparator, and an actuator that is work."""

from tests.helpers import JD, KernelCase


class Loops(KernelCase):
    pipeline = '[pipeline]\nresearch = "never"\nshape = "never"\n[healing]\ndoctor_every_min = 0\n'   # retro stays auto

    def setUp(self):
        super().setUp()
        o = self.k.org
        o.create_dept("ops", "Operations", "Run things")
        self.lead = o.hire(name="Lu", title="Head of Ops", dept="ops", job_description=JD, slot="lead",
                           driver="fake", reason="r", status="active")["id"]
        self.maker = o.hire(name="Mo", title="Ops engineer", dept="ops", job_description=JD, slot="maker",
                            driver="fake", reason="r", status="active")["id"]
        self.checker = o.hire(name="Ce", title="Ops checker", dept="ops", job_description=JD, slot="checker",
                              driver="fake", reason="r", status="active")["id"]

    def _executed(self, title="Restart the queue"):
        t = self.add(title, dept="ops", task_class="restart")
        self.plan_and_approve(t, planner=self.maker, assignee=self.maker, writes=[])
        c = self.k.claim("execute", self.maker)
        self.k.submit(c.run["id"], self.exec_env())
        return t

    def test_failed_verification_triggers_a_retro_whose_actions_become_work(self):
        t = self._executed()
        self.k.verify(t["id"], "fail", "queue still stuck")
        self.assertIn(("retro", "ready"), self.pipeline_of(t))
        c = self.k.claim("retro", self.lead)
        ctx = (c.brief_dir / "context.md").read_text()
        self.assertIn("History of this work", ctx)
        self.assertIn("queue still stuck", ctx)
        out = self.k.submit(c.run["id"], {"summary": "restart missed the worker", "cause": "plan skipped workers",
                                          "learnings": ["restart workers with the queue"],
                                          "actions": [{"title": "Add workers to the restart runbook",
                                                       "intent": "so restarts include workers"}]})
        new = self.k.work.get(out["created"][0])
        self.assertEqual((new["dept_id"], new["source"], new["task_class"]),
                         (self.k.org.dept("ops")["id"], "audit", "improvement"))
        self.assertTrue(any("restart workers" in i["content"] for i in self.k.memory.live_items([f"emp:{self.lead}"])))

    def test_one_rejection_is_feedback_two_are_a_pattern(self):
        t = self.add("Tune alerts", dept="ops", task_class="alerts")
        a = self.plan(t, planner=self.maker)
        self.k.reject(a["id"], "too broad")
        self.assertNotIn("retro", [s for s, _ in self.pipeline_of(t)])
        a = self.plan(t, planner=self.maker)
        self.k.reject(a["id"], "still too broad")
        self.assertIn(("retro", "ready"), self.pipeline_of(t))

    def test_departments_without_a_steward_get_no_retro(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        self.k.submit(c.run["id"], self.exec_env())
        self.k.verify(t["id"], "fail", "nope")
        self.assertNotIn("retro", [s for s, _ in self.pipeline_of(t)])

    def test_proposals_reach_the_steward_through_a_department_retro(self):
        t = self.add("Restart", dept="ops", task_class="restart")
        self.plan_and_approve(t, planner=self.maker, assignee=self.maker, writes=[])
        c = self.k.claim("execute", self.maker)
        self.k.submit(c.run["id"], self.exec_env(proposals=["automate restarts"]))
        out = self.k.tick()
        retro = [x for x in out["triggered"] if "dept_retro" in x]
        self.assertEqual((retro[0]["dept"], retro[0]["proposals"]), ("ops", 1))
        self.assertEqual(self.k.tick()["triggered"], [], "one department retro at a time")
        c = self.k.claim("retro", self.lead)
        self.assertIn("automate restarts", (c.brief_dir / "context.md").read_text())
        self.k.submit(c.run["id"], {"summary": "s", "cause": "c"})
        self.assertEqual(self.status(retro[0]["dept_retro"]), "done")
        self.assertEqual(self.k.work.messages(to_refs=["dept:ops"], kind="proposal", unacked=True), [])

    def test_a_rate_past_its_bound_makes_a_department_retro_due(self):
        self.k.config["metrics"]["min_samples"] = 2
        t = self.add("Tune alerts", dept="ops", task_class="alerts")
        self.k.reject(self.plan(t, planner=self.maker)["id"], "too broad")
        self.assertFalse([x for x in self.k.tick()["triggered"] if "dept_retro" in x], "one sample is not a rate")
        self.k.reject(self.plan(t, planner=self.maker)["id"], "still too broad")
        retro = [x for x in self.k.tick()["triggered"] if "dept_retro" in x]
        self.assertEqual((retro[0]["dept"], retro[0]["proposals"]), ("ops", 0), "no proposal was needed")
        self.assertIn("rejection_rate 1.0 is above 0.5 over 2", self.k.work.get(retro[0]["dept_retro"])["intent"])

    def test_runs_that_keep_dying_open_an_incident(self):
        t = self.add("Flaky", dept="ops", task_class="restart")
        for _ in range(2):
            c = self.k.claim("plan", self.maker, task_id=t["id"])
            self.k.wconn.execute("UPDATE runs SET lease_until='2000-01-01T00:00:00.000000Z' WHERE id=?", (c.run["id"],))
            out = self.k.tick()
        incident = [x for x in out["triggered"] if "incident" in x]
        self.assertEqual(len(incident), 1)
        work = self.k.work.get(incident[0]["incident"])
        self.assertEqual((work["task_class"], work["dept_id"]), ("incident", self.k.org.dept("executive")["id"]))

    def test_a_stage_whose_runs_keep_failing_opens_an_incident(self):
        self.install_fake_driver("fake", mode="garbage")  # e.g. a harness binary that rejects the driver's flags
        t = self.add("Doomed", dept="ops", task_class="restart")
        for _ in range(3):
            self.assertTrue(self.k.dispatch(self.maker, "plan", t["id"])["breach"])
        incident = [x for x in self.k.tick()["triggered"] if "incident" in x]
        self.assertEqual(len(incident), 1)
        self.assertIn("keep failing", incident[0]["title"])
        self.assertFalse([x for x in self.k.tick()["triggered"] if "incident" in x], "one incident, not one per tick")
        self.assertIsNone(self.k.dispatch(self.maker, "plan", t["id"]), "it waits before the next try")
        self.k.wconn.execute("UPDATE runs SET ended_at='2000-01-01T00:00:00.000000Z'")
        self.assertIsNotNone(self.k.dispatch(self.maker, "plan", t["id"]), "and is tried again after its back-off")

    def test_probation_ends_with_a_decision_by_the_head(self):
        newbie = self.k.org.hire(name="Nu", title="Ops engineer", dept="ops", job_description=JD, roles=["executor"],
                                 driver="fake", reason="growth")["id"]
        self.k.wconn.execute("UPDATE employees SET hired_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (newbie,))
        review = [x for x in self.k.tick()["triggered"] if "probation_review" in x][0]
        approval = self.k.work.get(review["probation_review"])
        self.assertEqual(approval["approver"], f"emp:{self.lead}")
        self.k.approve(approval["id"], "good start")
        self.assertEqual(self.k.org.employee(newbie)["status"], "active")
        self.assertFalse([x for x in self.k.tick()["triggered"] if "probation_review" in x])

    def test_metrics_pair_every_sensor_with_a_counter(self):
        t = self._executed()
        self.k.verify(t["id"], "fail", "x")
        c = self.k.claim("execute", self.maker)
        self.k.submit(c.run["id"], self.exec_env())
        self.k.verify(t["id"], "pass", "ok")
        m = self.k.metrics("ops")
        self.assertEqual(m["rework_rate"]["value"], 1.0)
        self.assertEqual(m["first_pass_rate"]["value"], 0.0)
        self.assertEqual(m["verify_pass_rate"]["value"], 0.5)
        for v in m.values():
            if isinstance(v, dict) and "value" in v:
                self.assertIn(v["counter"], m)


class Notifications(KernelCase):
    def test_the_chair_hears_once_about_what_newly_needs_them(self):
        out = self.tmp / "sent.txt"
        self.k.config["notify"] = {"command": f"printf '%s\\n---\\n' {{message}} >> {out}"}
        self.assertIsNone(self.k.tick()["notified"], "nothing new yet")
        self.plan(self.add("Rotate keys", dept="eng"))
        n = self.k.tick()["notified"]
        self.assertEqual((n["items"], n["sent"]), (1, True))
        self.assertIn("approve? Rotate keys", out.read_text())
        self.assertIsNone(self.k.tick()["notified"], "each item is announced once")
        self.k.config["notify"] = {"command": "exit 3"}
        self.plan(self.add("Second", dept="eng"))
        self.assertFalse(self.k.tick()["notified"]["sent"])
        self.assertTrue(any(e["entity"] == "notify" and e["to_status"] == "failed" for e in self.k.work.events()))


class DoctorOnTheHeartbeat(KernelCase):
    pipeline = '[pipeline]\nresearch = "never"\nshape = "never"\nretro = "never"\n[healing]\ndoctor_every_min = 30\n'

    def test_a_doctor_error_becomes_one_incident_and_the_check_waits_its_interval(self):
        self.k.auth.init_chair_key()
        self.k.wconn.execute("UPDATE events SET note='tampered' WHERE seq=(SELECT min(seq) FROM events)")
        first = [x for x in self.k.tick()["triggered"] if "incident" in x]
        self.assertEqual([x["title"] for x in first], ["Incident: doctor reports audit.chain"])
        self.assertFalse([x for x in self.k.tick()["triggered"] if "incident" in x], "doctor waits doctor_every_min")
