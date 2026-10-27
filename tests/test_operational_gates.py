"""The operationalization gates (2026-10-25 red-team).

Measured on a real imported instance: the Chair-approved ventures import landed 26 routine
proposals in three departments with **zero employees**, and every operationalization seam
accepted the state without a word:

- `schedule enable` lit an unstaffed department's recurring schedule with no resistance (F1);
- `add_routine`'s grade reasons — the only thing the approver reads — said nothing about
  staffing (F2);
- doctor reported nothing at all: no backstop existed, on any surface (F3);
- `hr hire --driver` accepted a driver name nothing configures (F4);
- a `deliver` routine on an instance with no `[notify]` command silently drops results (F5);
- a script routine whose workdir is gone silently ran in the run directory instead (F6);
- forced end to end, the firing's stage sat `ready` in a department where nobody could claim
  it (F7) — the strand every one of the above permits.

An agent building a system inside Holon (a department, its routines, its staffing) must not
be able to create something that cannot operate without the surface it speaks through naming
what is missing. The gates here are fail-closed at every seam, with the Chair's escape where
the gate's class demands one (the ledger's own precedent: `isolation apply --force`).
"""

import json
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import KernelCase, JD
from tests.test_lanes import LaneCase


def staffed_lane_stages(lane: str) -> tuple[str, ...]:
    """The stages a lane's firing needs somebody (besides the kernel) to perform."""
    return {"script": (), "fast": ("execute",), "full": ("plan", "execute", "verify")}[lane]


class OperationalGates(LaneCase):
    """One gate per seam. The fixture's `eng` department starts staffed; the tests fire each
    gate by hiring nothing into a fresh department."""

    def _unstaffed_routine(self, lane: str, title="Ghost digest", intent_extra=None, **kw):
        """A routine in a department with no employees, in the given lane."""
        self.k.org.create_dept("ghost", "Ghost", "A department nobody staffs")
        with mock.patch.object(self.k, "classify", return_value={"lane": lane, "confidence": 0.99,
                                                                "external_p": 0.10}):
            a = self.k.add_routine(title, intent_extra or "summarize the ghost pool", dept="ghost",
                                   cron="0 9 * * *", workdir=str(self.workdir), **kw)
        return a

    def _approval_id(self, a: dict) -> str:
        return self.k.work.get(a["id"])["id"] and a["id"]

    # ── F1/F7: the enable seam — an unstaffed schedule must not light ───────────────
    def _parked_import(self, intent="summarize the ghost pool"):
        """An imported, outside-reach routine in the unstaffed dept: parks OFF on approval, so the
        enable gate is the seam its schedule reaches (a hand-written one is refused at approval)."""
        with mock.patch.object(self.k, "_external_by_name", return_value=(True, "send")):
            b = self._unstaffed_routine("fast", title="Ghost outreach", intent_extra=intent,
                                        imported=True, why="agent-shaped")
        self.k.approve(b["id"])
        plan = self.k.work.plan_for_approval(b["id"])
        return self.k.wconn.execute("SELECT id FROM schedules WHERE template_id=?", (plan["id"],)).fetchone()[0]

    def test_schedule_enable_refuses_an_unstaffed_lane(self):
        sid = self._parked_import()
        with self.assertRaises(ValueError) as cm:
            self.k.schedules.set_enabled(sid, True)
        self.assertIn("no employee in ghost performs execute", str(cm.exception))
        self.assertIn("holon hr hire", str(cm.exception),
                      "the refusal names the act that fixes it")
        self.assertFalse(self.k.schedules.get(sid)["enabled"], "the refusal left it OFF")

    def test_the_enable_gate_passes_a_staffed_lane(self):
        self.k.org.create_dept("ghost", "Ghost", "A department nobody staffs")
        self.k.org.hire(name="Gee", title="maker", dept="ghost", job_description=JD, slot="maker",
                        driver="fake", reason="staffing", status="active")
        with mock.patch.object(self.k, "classify", return_value={"lane": "fast", "confidence": 0.99,
                                                                "external_p": 0.10}):
            a = self.k.add_routine("Ghost digest", "summarize the ghost pool", dept="ghost",
                                   cron="0 9 * * *", workdir=str(self.workdir))
        self.k.approve(a["id"])
        plan = self.k.work.plan_for_approval(a["id"])
        sid = self.k.wconn.execute("SELECT id FROM schedules WHERE template_id=?", (plan["id"],)).fetchone()[0]
        s = self.k.schedules.set_enabled(sid, True)   # staffed: no refusal
        self.assertTrue(s["enabled"])

    def test_the_enable_gate_ignores_script_lanes(self):
        """The kernel is the script lane's runner: no employee is ever needed."""
        a = self._unstaffed_routine("script", title="Ghost script", command="true")
        self.k.approve(a["id"])
        plan = self.k.work.plan_for_approval(a["id"])
        sid = self.k.wconn.execute("SELECT id FROM schedules WHERE template_id=?", (plan["id"],)).fetchone()[0]
        self.assertTrue(self.k.schedules.get(sid)["enabled"],
                        "a script routine in an unstaffed dept starts on approval, as before")
        s = self.k.schedules.set_enabled(sid, True)   # and re-enable is fine too
        self.assertTrue(s["enabled"])

    def test_no_consumer_names_an_escape_it_does_not_offer(self):
        """The note's `--force` clause must be true of the surface printing it.

        Found 2026-10-27 by validating the shared message against each of its four consumers.
        The clause is true at the enable gate (where `--force` exists and is recorded in the
        event log) and FALSE where the note appears as a proposal's grade reason, an
        approval's refusal and doctor's backstop -- `plan approve` carries no `--force` at
        all, so the approval's refusal told the Chair to run a flag that does not exist.
        That is the remediation class again, one layer deeper: the gate refuses, names the
        fix, and the fix's escape does not exist.

        The rule the guard holds: a note's escape clause is named by the SURFACE that prints
        it, not by the shared string.
        """
        from holon import cli
        from holon.execution.schedules import staffing_note
        forces = {' '.join(c.path) for c in cli.REGISTRY
                  if "--force" in [a[0] for a, _ in (c.args or [])]}
        # the enable gate offers it; the approval command does not
        self.assertIn("schedule enable", forces, "the Chair's escape at the enable gate")
        self.assertNotIn("plan approve", forces,
                         "the approval transaction carries no --force: any note naming one there "
                         "sends the Chair to a flag that does not exist")
        # and the note, printed by each consumer, must not promise a flag that consumer lacks
        note = staffing_note(self.k.wconn, None, "fast", self.k.home)
        self.assertNotIn("--force", note,
                         "the shared note names no flag: the escape belongs to the surface that "
                         f"owns it (the enable gate), and each surface states its own: {note}")

    def test_the_grade_reason_does_not_promise_the_chair_a_flag_that_does_not_exist(self):
        """The proposal's reason is read BEFORE the enable gate exists as a decision.

        A grade reason is informational: the approver reads it, then decides. It must not tell
        them a flag they can pass on a command that has none. Where the note names a force
        escape at all, it names the gate that owns it -- the enable -- and says so.
        """
        a = self._unstaffed_routine("fast")
        plan = self.k.work.plan_for_approval(a["id"])
        reasons = json.loads(self.k.work.get(plan["id"])["grade_reasons"] or "[]")
        for r in reasons:
            if "no employee in ghost performs" not in r:
                continue
            self.assertNotRegex(r, r"--force\b(?!.*schedule enable)",
                                 "a proposal's reason must not name --force without naming the "
                                 f"gate that owns it: {r}")

    def test_the_chair_may_force_an_unstaffed_enable(self):
        """The gate's escape: the Chair's explicit word, recorded in the event log."""
        sid = self._parked_import()
        s = self.k.schedules.set_enabled(sid, True, force=True)
        self.assertTrue(s["enabled"], "the Chair's --force lights it, named in the event log")
        ev = self.k.wconn.execute("SELECT note FROM events WHERE entity='schedule' AND entity_id=? "
                                  "ORDER BY seq DESC LIMIT 1", (sid,)).fetchone()
        self.assertIn("unstaffed", (ev[0] or ""), "the forced enable says why it needed forcing")

    # ── F2: the proposal seam — the approver reads the staffing note ──────────────
    def test_the_grade_reasons_name_the_missing_staff(self):
        a = self._unstaffed_routine("fast")
        plan = self.k.work.plan_for_approval(a["id"])
        reasons = json.loads(self.k.work.get(plan["id"])["grade_reasons"] or "[]")
        self.assertTrue(any("no employee in ghost performs execute" in r for r in reasons),
                        f"the approver reads: {reasons}")
        self.assertTrue(any("holon hr hire" in r for r in reasons),
                        "the note names the act that fixes it")

    def test_the_staffed_proposal_carries_no_staffing_note(self):
        self.k.org.create_dept("ghost", "Ghost", "A department nobody staffs")
        self.k.org.hire(name="Gee", title="maker", dept="ghost", job_description=JD, slot="maker",
                        driver="fake", reason="staffing", status="active")
        with mock.patch.object(self.k, "classify", return_value={"lane": "fast", "confidence": 0.99,
                                                                "external_p": 0.10}):
            a = self.k.add_routine("Ghost digest", "summarize the ghost pool", dept="ghost",
                                   cron="0 9 * * *", workdir=str(self.workdir))
        plan = self.k.work.get(a["id"])
        reasons = json.loads(plan["grade_reasons"] or "[]")
        self.assertFalse(any("performs" in r and "no employee" in r for r in reasons),
                        f"a staffed department carries no false note: {reasons}")

    # ── the approval seam: a starts-on-approval routine in an unstaffed dept ───────
    def test_an_unstaffed_starts_on_approval_routine_refuses_to_apply(self):
        """A hand-written (not imported) internal routine starts on approval. Approving one in an
        unstaffed department must refuse the apply — the approval transaction aborts, the work is
        not finished — rather than light a schedule nothing can run."""
        a = self._unstaffed_routine("fast")     # not imported: no starts_disabled
        with self.assertRaises(ValueError) as cm:
            self.k.approve(a["id"])
        self.assertIn("no employee in ghost performs execute", str(cm.exception))
        self.assertEqual(self.k.work.get(a["id"])["status"], "ready",
                         "the refused apply leaves the proposal pending, not half-applied")

    def test_the_imports_off_parking_is_not_gated(self):
        """An imported routine lands OFF (I1): the parking state is the design, so approving it
        must succeed — the gate lives at enable, not at the import's parking approval."""
        sid = self._parked_import()
        self.assertFalse(self.k.schedules.get(sid)["enabled"],
                         "the imported unstaffed routine parks OFF, approved — enable is the gate")

    # ── F3: the backstop — doctor reports an enabled unstaffed schedule ───────────
    def test_doctor_reports_an_enabled_unstaffed_schedule(self):
        from holon.integrity import doctor
        sid = self._parked_import()
        self.k.schedules.set_enabled(sid, True, force=True)
        found = {f["check"]: f for f in doctor.run(self.k)}
        self.assertIn("schedule.unstaffed", found, f"doctor must report it: {sorted(found)}")
        self.assertEqual(found["schedule.unstaffed"]["severity"], "error",
                         "the heartbeat turns an error into an incident")
        self.assertIn("hr hire", found["schedule.unstaffed"]["message"])

    # ── F4: the hire seam — a driver nothing configures must refuse at hire ────────
    def test_hire_refuses_a_driver_nothing_configures(self):
        from holon.execution import drivers
        self.assertNotIn("nonexistent-driver", drivers.available(self.k.home))
        with self.assertRaises(Exception) as cm:
            self.k.org.hire(name="Hex", title="maker", dept="eng", job_description=JD, slot="maker",
                            driver="nonexistent-driver", reason="test")
        self.assertIn("nonexistent-driver", str(cm.exception))

    # ── F5: the delivery seam — deliver with no channel is named at add time ──────
    def test_a_deliver_routine_names_its_missing_channel(self):
        self.configure("")        # no [notify] block: nothing configured
        self.k.org.create_dept("ghost", "Ghost", "A department nobody staffs")
        with mock.patch.object(self.k, "classify", return_value={"lane": "fast", "confidence": 0.99,
                                                                "external_p": 0.10}):
            a = self.k.add_routine("Ghost digest", "summarize the ghost pool", dept="ghost",
                                   cron="0 9 * * *", workdir=str(self.workdir), deliver=True)
        plan = self.k.work.plan_for_approval(a["id"])
        reasons = json.loads(self.k.work.get(plan["id"])["grade_reasons"] or "[]")
        self.assertTrue(any("[notify]" in r and "deliver" in r for r in reasons),
                        f"the approver reads that results will reach nobody: {reasons}")

    # ── F6: the script seam — an enabled script schedule with a dead workdir ───────
    def test_doctor_reports_an_enabled_script_schedule_with_a_dead_workdir(self):
        from holon.integrity import doctor
        a = self._unstaffed_routine("script", title="Ghost script", command="true")
        self.k.approve(a["id"])
        import shutil
        shutil.rmtree(self.workdir)
        found = {f["check"]: f for f in doctor.run(self.k)}
        self.assertIn("schedule.workdir", found,
                      "an enabled script schedule whose workdir is gone is visible before it runs wrong")

    # ── the predicate itself: what each lane needs ────────────────────────────────
    def test_the_lane_stages_predicate(self):
        self.assertEqual(staffed_lane_stages("script"), ())
        self.assertEqual(staffed_lane_stages("fast"), ("execute",))
        self.assertEqual(staffed_lane_stages("full"), ("plan", "execute", "verify"))


class StaffingReach(KernelCase):
    """The staffing predicate must mirror the claim's reach, or it gates the wrong things. The
    claim's pool branch (`work.claim`): a claimant may pick up work in their OWN department, and
    an executive claimant additionally reaches no-dept work and verify stages of any department —
    but NOT another department's plan or execute stages. So a full-lane routine in an unstaffed
    department is missing plan too, even with a CEO in executive (who reaches only verify)."""

    def test_executive_reach_covers_only_what_the_claim_covers(self):
        self.k.org.create_dept("ghost", "Ghost", "A department nobody staffs")
        with mock.patch.object(self.k, "classify", return_value={"lane": None, "confidence": 0.0,
                                                                "external_p": 0.10}):
            a = self.k.add_routine("Ghost full", "the whole pipeline", dept="ghost", cron="0 9 * * *",
                                   workdir=str(self.workdir), why="needs the pipeline")
        plan = self.k.work.plan_for_approval(a["id"])
        reasons = json.loads(self.k.work.get(plan["id"])["grade_reasons"] or "[]")
        note = next(r for r in reasons if "unstaffed for its lane" in r)
        for stage in ("execute", "plan"):
            self.assertIn(stage, note, f"{stage} is not the executive's to claim for another department")
        self.assertNotIn("verify", note, "the CEO reaches verify stages anywhere (executive reach)")

    def test_a_dept_employee_covers_their_own_lanes(self):
        self.k.org.create_dept("ghost", "Ghost", "A department nobody staffs")
        self.k.org.hire(name="Gee", title="maker", dept="ghost", job_description=JD, slot="maker",
                        driver="fake", reason="staffing", status="active")
        with mock.patch.object(self.k, "classify", return_value={"lane": "fast", "confidence": 0.99,
                                                                "external_p": 0.10}):
            a = self.k.add_routine("Ghost digest", "summarize", dept="ghost", cron="0 9 * * *",
                                   workdir=str(self.workdir))
        plan = self.k.work.plan_for_approval(a["id"])
        reasons = json.loads(self.k.work.get(plan["id"])["grade_reasons"] or "[]")
        self.assertFalse(any("unstaffed for its lane" in r for r in reasons),
                        f"a maker in the dept covers fast: {reasons}")


class TheNamedRemediationWorks(OperationalGates):
    """The gate's refusal must name a hire command that **runs** (2026-10-26 audit, G1).

    Measured 2026-10-26: the shared refusal message ended with
    `holon hr hire --dept <slug> --slot maker --driver <driver> …`, and `maker` is a slot of
    the **light** profile only. The live Executive Office is on the **executive** profile
    (slots ceo/strategy/audit/charter), so there the gate refused a schedule and then named
    a hire command that refused too:

        OrgError: department execdept (profile executive) has no slot 'maker';
        slots: ['ceo', 'strategy', 'audit', 'charter']

    This is the ledger's own class -- *a message that names the fix must name the fix that
    works* -- and the guard is written to the class, not to the wording: it **runs** the
    command the note names, on a scratch instance of each profile, and asserts the hire
    succeeds. A guard that asserted the note contains the word `executor` would pass while
    the command still refused, which is the failure being fixed.
    """

    def _note_for(self, profile_name: str, slug: str = "dept", lane: str = "full") -> str:
        """The refusal message for an unstaffed department on the given profile."""
        from holon.execution import bylaws as B
        from holon.execution import schedules as S
        d = self.k.org.create_dept(slug, "Dept", "A department", profile=B.load_profile(profile_name))
        return S.staffing_note(self.k.wconn, d["id"], lane)

    def test_the_named_hire_runs_as_printed_with_no_placeholder_filled(self):
        """The command in the note must run EXACTLY as printed -- found by running it.

        The first draft of this guard parsed the command and SUBSTITUTED the placeholders it
        found (`<driver>` -> `fake`, `<slug>` -> the real slug), so it measured the command's
        skeleton and never noticed that the printed command is unrunnable: `--driver` is
        `required=True`, and `holon drivers` names the drivers the instance actually has. A
        guard that fills the placeholder proves the command would work IF the reader guessed
        the right value -- which is the remediation class again (the operator's command
        refuses with a usage dump, and nothing tells them what to put there).

        This guard therefore only substitutes what is a VALUE of the instance (the department
        slug) and refuses any remaining `<placeholder>` the note left for the reader to guess.
        """
        for profile, slug in (("light", "pprof"), ("standard", "ssprof"), ("executive", "eprof")):
            with self.subTest(profile=profile):
                note = self._note_for(profile, slug, "fast")
                import re, shlex
                m = re.search(r"holon hr hire ([^\n`]+)", note)
                self.assertIsNotNone(m, f"the note names no hire command: {note!r}")
                # the command as printed, its subcommand words included: a guard that drops
                # `hr hire` measures a different command from the one the reader would type.
                argv = ["hr", "hire"] + shlex.split(m.group(1))
                left = [a for a in argv if "<" in a or "..." in a or a == "..."]
                self.assertEqual([], left,
                                 f"{profile}: the note leaves a placeholder the operator must "
                                 f"guess: {left} in {note!r}")
                self.assertTrue(any(a.startswith("--driver=") or a for a in argv),
                                "the note must name a driver")
                # and the named driver is one the instance actually configures
                from holon.execution import drivers as D
                self.assertIn(argv[argv.index("--driver") + 1], D.available(self.k.home),
                              f"the driver the note names is not one this instance has")

    def test_the_named_hire_runs_unchanged_on_the_real_cli(self):
        """The strongest half: the printed command, character for character, on the CLI.

        Runs it as a subprocess exactly as printed (only the values a reader can read from the
        note's own context are already concrete), and asserts it hires. The previous guard
        substituted placeholders, so it could pass on a command the CLI refuses -- which is
        what it found on 2026-10-27 when it was tightened.
        """
        for profile, slug in (("light", "rprof"), ("standard", "rsprof"), ("executive", "reprof")):
            with self.subTest(profile=profile):
                note = self._note_for(profile, slug, "fast")
                import re, shlex, subprocess, sys, os
                m = re.search(r"holon hr hire ([^\n`]+)", note)
                argv = ["hr", "hire"] + shlex.split(m.group(1))
                env = dict(os.environ)
                env["HOME"] = str(Path(self.k.home.root).parent)
                r = subprocess.run([sys.executable, "-m", "holon", "--home", str(self.k.home.root)]
                                   + argv + ["--name", "Probe", "--title", "Worker",
                                             "--jd", "validate the named command",
                                             "--reason", "validate the named command"],
                                   capture_output=True, text=True, env=env)
                self.assertEqual(0, r.returncode,
                                 f"the printed command must run as-is: {note!r}\n"
                                 f"stderr: {r.stderr.strip()[:300]}")

    def _run_named_hire(self, note: str, slug: str) -> dict:
        """Run the hire command the note names, with its placeholders filled.

        The command is parsed out of the message rather than rebuilt from the code, so the
        guard measures what a reader of the message would actually type.
        """
        import re
        import shlex
        m = re.search(r"holon hr hire ([^\n`]+)", note)
        self.assertIsNotNone(m, f"the note names no hire command: {note!r}")
        argv = shlex.split(m.group(1).replace("…", "").replace("<driver>", "fake")
                           .replace("<slug>", slug).replace("--dept " + slug, "--dept " + slug))
        # every placeholder filled; a leftover angle bracket means the note named something
        # the guard cannot run, which is itself a defect.
        self.assertNotIn("<", " ".join(argv), f"the note names an unfillable placeholder: {argv}")
        dept = argv[argv.index("--dept") + 1]
        kwargs = {}
        if "--slot" in argv:
            kwargs["slot"] = argv[argv.index("--slot") + 1]
        if "--roles" in argv:
            kwargs["roles"] = argv[argv.index("--roles") + 1].split(",")
        driver = argv[argv.index("--driver") + 1]
        return self.k.org.hire(name="Rex", title="Hire", dept=dept, job_description=JD,
                               driver=driver, reason="the gate named this hire", **kwargs)

    def test_the_named_hire_works_on_every_profile(self):
        """One guard per profile AND lane: the command the refusal names must hire, and it
        must leave **nothing** the note complained about unstaffed.

        The "nothing left" half matters: a command that hires an employee covering only part of
        the missing stages would pass a guard that checks one stage, and the firing would still
        wait with nobody to claim it -- the exact condition the gate exists to prevent.
        """
        for profile, slug in (("light", "lprof"), ("standard", "sprof"), ("executive", "eprof")):
            for lane in ("fast", "full"):
                with self.subTest(profile=profile, lane=lane):
                    note = self._note_for(profile, f"{slug}-{lane}", lane)
                    e = self._run_named_hire(note, f"{slug}-{lane}")
                    self.assertIn("executor", e["roles"],
                                  f"the hire the note names must cover the execute stage: {e}")
                    from holon.execution import schedules as S
                    d = self.k.org.dept(f"{slug}-{lane}")
                    self.assertEqual(set(), S.unstaffed_stages(self.k.wconn, d["id"], lane),
                                     f"after the hire the note names, {lane} is still unstaffed in {slug}")

    def test_the_note_does_not_name_a_slot_the_department_does_not_have(self):
        """The narrow half of the same defect: the note's slot must exist in the charter."""
        from holon.execution import bylaws as B
        from holon.execution import schedules as S
        d = self.k.org.create_dept("eprof", "Dept", "A department", profile=B.load_profile("executive"))
        note = S.staffing_note(self.k.wconn, d["id"], "full")
        slots = {s["name"] for s in B.load_profile("executive")["slots"]}
        tokens = note.split()
        for i, token in enumerate(tokens):
            if token == "--slot":
                named = tokens[i + 1]
                self.assertIn(named, slots,
                              f"the note names slot {named!r}, which the executive profile does not "
                              f"have (slots: {sorted(slots)}): {note!r}")


class TheWakeHole(OperationalGates):
    """An employee nothing wakes is an employee who cannot work (2026-10-26 audit, G2).

    Measured 2026-10-26 on the live instance: **7 active employees and 0 wake schedules**.
    A wake schedule is the mechanism that makes an employee run at all (`vocab.SCHEDULE_KINDS`
    carries `wake` beside `recurring`), and the staffing backstop built on 2026-10-25 filters
    `kind='recurring'` -- so the condition the backstop exists to surface ("a system that
    cannot operate, with no surface naming it") was itself invisible to it. The word `wake`
    appeared in no file under `holon/integrity/`.

    Three guards, because "no wake schedule" and "a wake schedule that cannot fire" are
    different states and only the first is obvious:

    - an instance where **no active employee** has a live wake schedule is an **error**
      (no clock can wake anyone: nothing schedules work into the organization), and the
      message names the fix;
    - an employee with a **dead** wake schedule is an **error naming that employee** while a
      colleague who IS woken stays silent -- the drift case (`hr reassign` changes roles
      without revalidating schedules, so a wake schedule becomes dead after the fact);
    - an employee with a live wake schedule is **silent** (a healthy instance must not cry
      wolf -- the failure mode the ledger records most often).

    Why the predicate is narrower than "no wake schedule": the kernel claims ready stages
    itself (`holon tick` dispatches a stage for an employee), so a body with no clock is not
    by itself unfireable -- the whole test suite is that pattern. An instance where NO body has
    a clock is the state that cannot operate without a surface naming it, and it is provable
    without guessing what other dispatch paths exist.
    """

    def _employee(self, name="Waker", roles=None):
        e = self.k.org.hire(name=name, title="Worker", dept="eng", job_description=JD,
                            roles=roles or ["executor"], driver="fake", reason="test", status="active")
        return e

    def _wake(self, emp_id, stage=None, cron="*/15 * * * *"):
        return self.k.schedules.add("wake", cron, employee_id=emp_id, stage=stage)

    def test_an_employee_no_wake_schedule_can_reach_is_an_error(self):
        """The systemic hole: no active employee has a live wake schedule.

        The instance carries a recurring schedule (so it is clocked, not pre-clock: an instance
        with no schedules at all is the Chair's act to arm, not the heartbeat's to escalate),
        and no employee has a wake schedule -- so nothing wakes anyone. The finding names the
        instance, and the message names the command that fixes it.
        """
        from holon.integrity import doctor
        # an instance that IS clocked: a disabled wake schedule proves the clock mechanism was
        # armed. (A recurring schedule needs an approved recurring plan, which is not what this
        # guard is about.) Its employee is retired, so no ACTIVE body carries a dead schedule
        # -- which is what keeps this the instance-level finding rather than a drift finding.
        gone = self._employee("Retired")
        w = self._wake(gone["id"])
        self.k.wconn.execute("UPDATE schedules SET enabled=0 WHERE id=?", (w["id"],))
        self.k.org.fire(gone["id"], "test: retire so no active body carries a dead schedule")
        self._employee("Wanderer")
        found = {f["check"]: f for f in doctor.run(self.k)}
        self.assertIn("schedule.nowake", found,
                      f"an instance where no employee can be woken must be reported: {sorted(found)}")
        self.assertEqual(found["schedule.nowake"]["severity"], "error",
                         "the heartbeat turns an error into an incident; a warning would be a "
                         "note nobody reads")
        self.assertIn("no employee has a live wake schedule", found["schedule.nowake"]["message"],
                      "the finding must name the systemic condition, not one unlucky body")
        self.assertIn("schedule add", found["schedule.nowake"]["message"],
                      "the message must name the command that fixes it")

    def test_a_pre_clock_instance_is_not_the_systemic_hole(self):
        """The other side of the same boundary: an instance with no schedules at all.

        Arming the first schedule is the Chair's act; the heartbeat escalating it as an error
        on every fresh instance (init creates employees and no clocks) would cry wolf on the
        state the distribution ships in. Pre-clock is not broken-clock.
        """
        from holon.integrity import doctor
        self._employee("Wanderer")
        self.assertNotIn("schedule.nowake",
                         [f["check"] for f in doctor.run(self.k)],
                         "an instance that has never armed a clock is pre-clock, not defective")

    def test_one_woken_employee_is_not_folded_into_the_instance_finding(self):
        """The predicate's boundary: a colleague with a live wake schedule is not named.

        Without this, the systemic finding would over-report every employee on an instance
        where one body is woken and the rest are not -- which is the healthy pull-mode shape
        the whole suite runs on (the kernel claims ready stages by hand).
        """
        from holon.integrity import doctor
        woken = self._employee("Woken")
        self._wake(woken["id"])
        found = {f["check"]: f for f in doctor.run(self.k)}
        if "schedule.nowake" in found:
            self.assertNotIn(woken["id"], found["schedule.nowake"]["message"],
                             f"a woken employee must not be named by the finding: {found}")

    def test_an_employee_with_a_live_wake_schedule_is_not_reported(self):
        """The scheduled employee is not named -- which is the check's promise.

        The promise is per-employee, not per-instance: an instance has other bodies (the
        fixture's own staff), and each of *those* without a wake path is correctly reported.
        Asserting "no findings at all" would pass only on an instance where every employee
        happens to be scheduled, and would fail for the wrong reason on any other -- so the
        guard names the employee it scheduled and asserts the finding does not carry its id.
        """
        from holon.integrity import doctor
        e = self._employee("Waker")
        self._wake(e["id"])
        found = doctor.run(self.k)
        mine = [f for f in found if f["check"] == "schedule.nowake" and e["id"] in f["message"]]
        self.assertEqual([], mine,
                         f"an employee with a live wake schedule must not be named: {mine}")

    def test_a_pull_mode_instance_with_one_woken_employee_is_silent(self):
        """The case the first draft of this check failed (the suite's own healthy fixture).

        A healthy instance whose employees are dispatched by claim (the kernel claims ready
        stages itself) has employees with no wake schedule and is NOT defective -- reporting it
        would cry wolf on every healthy suite run, and a check nobody trusts is a check nobody
        reads. The guard is that one live wake path silences the instance-level finding even
        though the instance's other bodies have no clock of their own.
        """
        from holon.integrity import doctor
        e = self._employee("Waker")
        self._wake(e["id"])
        self.assertNotIn("schedule.nowake",
                         [f["check"] for f in doctor.run(self.k)],
                         "a healthy instance with at least one live wake schedule is silent")

    def test_a_wake_schedule_the_employee_can_no_longer_perform_is_an_error(self):
        """The drift case, end to end through the real `hr reassign` that causes it.

        `hr reassign` changes roles without revalidating schedules, so a wake schedule can name
        a stage the employee no longer performs. A woken colleague is present because the
        per-employee branch is only reached when SOME employee is woken -- without one, the
        same state is the systemic hole (reported, but at the instance, not by name).
        """
        from holon.integrity import doctor
        colleague = self._employee("Woken", roles=["executor"])
        self._wake(colleague["id"])
        e = self._employee("Drifter", roles=["planner", "executor"])
        self._wake(e["id"], stage="plan")
        self.k.org.reassign(e["id"], "narrowed", roles=["executor"])
        found = {f["check"]: f for f in doctor.run(self.k)}
        self.assertIn("schedule.nowake", found,
                      "a wake schedule for a stage the employee can no longer perform is dead: "
                      f"{sorted(found)}")
        self.assertIn(e["id"], found["schedule.nowake"]["message"])
        self.assertNotIn(colleague["id"], found["schedule.nowake"]["message"],
                         "the woken colleague must not be named by the drifted finding")


if __name__ == "__main__":
    unittest.main()
