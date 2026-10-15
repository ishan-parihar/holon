"""`holon adapter test supervisor '<cmd>'` (H-10): does a supervisor honor the supervisor
protocol (docs/CONTRACT.md §11)?

The supervisor is an external command. The suite builds a scratch organization whose bodies are
a scripted fixture (no model calls): it claims stages with `prepare_dispatch`, feeds the
prepared JSON to the supervisor on stdin, and checks what a compliant supervisor must have
done from the outside — the environment the body saw, the hand-in of its result, the pickup of
its trajectory/usage/refusals, lease renewal on long work, and the timeout kill.

Clauses:
  prepare    the prepared JSON is the §11 contract (run dir names, token, result path; no key
             ever in it; isolation fields only under isolation)
  env        the body saw the prepared environment — and nothing of the supervisor's own
             (a canary planted in the supervisor's environment, and its Chair key, never
             reached the body)
  finish     the result was handed in and judged: run succeeded, trajectory stored, usage
             recorded (the fixture's measurement, not an envelope claim), refusals are blockers
  lease      a body that outlives its lease completes (the supervisor renewed it), and the
             run shows the renewal
  timeout    a body that hangs is killed by the run's timeout and finishes failed — promptly

A supervisor that does its own `holon run finish` and one whose body submits via
`holon_submit` both pass the finish clause; only the outcome is judged.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CLAUSES = ("prepare", "env", "finish", "lease", "timeout")

FIXTURE = r'''
import json, os, time
from pathlib import Path

stage = os.environ.get("HOLON_STAGE", "")
rd = Path(os.environ.get("HOLON_RUN_DIR", "."))
wd = Path.cwd()

def result(env):
    (rd / "result.json").write_text(json.dumps(env))

if stage == "plan":
    result({"kind": "plan", "findings": "fixture", "steps": ["write ok"], "writes": ["ok"]})
elif stage == "approve":
    result({"decision": "approve", "note": "fixture"})
elif stage == "verify":
    result({"verdict": "pass", "note": "fixture"})
elif stage == "execute":
    (wd / "fx-env.json").write_text(json.dumps(dict(os.environ)))
    mode = (wd / "fx-mode").read_text().strip()
    if mode == "ok":
        (rd / "trajectory.json").write_text(json.dumps({"steps": [
            {"thought": "work", "tool": "file_write", "args": "ok", "outcome": "written"}]}))
        (rd / "usage.json").write_text(json.dumps(
            {"tokens_in": 100, "tokens_out": 40, "build": "fixture-9"}))
        result({"outcome": "success", "summary": "fixture ok", "documentation": "fixture",
                "refusals": [{"tool": "terminal", "reason": "fixture policy", "rule": "fx-1"}]})
    elif mode == "long":
        time.sleep(12)            # past the clause's short lease: the supervisor must renew it
        result({"outcome": "success", "summary": "done at last", "documentation": "fixture"})
    else:                          # hang: writes nothing, never exits
        time.sleep(300)
'''

JD = ("Conformance body: a scripted fixture. Do exactly what the brief asks; the result is "
      "checked by the supervisor conformance suite.")


def run_supervisor(k, supervisor_cmd: str, clauses: list[str] | None = None) -> dict:
    from holon.core import vocab
    from holon.core.paths import Home
    from holon.execution.service import Kernel

    wanted = clauses or list(CLAUSES)
    bad = set(wanted) - set(CLAUSES)
    if bad:
        raise ValueError(f"unknown clauses {sorted(bad)}; clauses: {CLAUSES}")
    results: list[dict] = []
    scratch = Path(tempfile.mkdtemp(prefix="holon-supervisor-conformance-"))
    home = Home(scratch / "home")
    (home.root).mkdir(parents=True)
    home.drivers_dir.mkdir(parents=True)
    fixture = scratch / "fixture.py"
    fixture.write_text(FIXTURE)
    (home.drivers_dir / "supervisor-fixture.toml").write_text(
        f'name = "supervisor-fixture"\ncommand = "{sys.executable} {fixture}"\n'
        f'cwd = "{{workdir}}"\ntimeout_s = 8\nresult = "file"\n')
    home.config_file.write_text('[pipeline]\nresearch = "never"\nshape = "never"\nretro = "never"\n'
                                "[grading]\nnovelty_min_verified = 0\n")
    s = Kernel(home)
    old_lease = dict(vocab.LEASE_S)

    def clause(name, fn) -> None:
        if name not in wanted:
            return
        try:
            ok, detail = fn()
        except Exception as e:  # a clause that crashes is a failed clause, not a crashed suite
            ok, detail = False, f"{type(e).__name__}: {e}"
        results.append({"clause": name, "ok": ok, "detail": detail})

    def supervise(prepared: dict, timeout: float = 120) -> subprocess.CompletedProcess:
        """Feed the prepared JSON to the external supervisor, as a runtime host would. The
        supervisor's own environment carries what a host legitimately holds — the instance home,
        its Chair key file, a canary — none of which a compliant supervisor may pass to a body.
        The scratch Chair key lives inside the scratch directory (H-14: conformance never
        litters ~/.config/holon/keys)."""
        key = scratch / "chair.key"
        if not key.exists():
            key.write_text("hck_" + secrets.token_urlsafe(32))
            key.chmod(0o600)
        env = {k: v for k, v in os.environ.items() if k.startswith("PYTHONPATH") or k in ("PATH", "HOME", "LANG")}
        pkg = str(Path(__file__).resolve().parents[2])   # the repo root: `python -m holon` from any cwd
        env["PYTHONPATH"] = os.pathsep.join(p for p in (pkg, env.get("PYTHONPATH", "")) if p)
        env.update({"HOLON_HOME": str(home.root), "HOLON_CHAIR_KEY_FILE": str(key),
                    "SUITE_CANARY": "supervisor-secret"})
        return subprocess.run(supervisor_cmd, shell=True, cwd=scratch, env=env,
                              input=json.dumps(prepared), capture_output=True, text=True, timeout=timeout)

    try:
        o = s.org
        ceo = o.hire(name="S CEO", title="CEO", dept=vocab.EXECUTIVE, job_description=JD, slot="ceo",
                     driver="supervisor-fixture", reason="conformance", status="active")["id"]
        o.create_dept("conformance", "Conformance", "Check a supervisor against CONTRACT §11")
        lead = o.hire(name="S Lead", title="Head", dept="conformance", job_description=JD,
                      roles=["head", "planner", "verifier"], driver="supervisor-fixture",
                      reason="conformance", status="active")["id"]
        maker = o.hire(name="S Maker", title="Executor", dept="conformance", job_description=JD,
                       roles=["executor"], driver="supervisor-fixture", reason="conformance",
                       status="active")["id"]
        o.set_authority(f"emp:{ceo}", "*", 4, "conformance: the CEO approves everything here")

        def fresh_work(mode: str) -> tuple[dict, Path]:
            work = scratch / f"work-{mode}-{len(results)}"
            work.mkdir(parents=True, exist_ok=True)
            (work / "fx-mode").write_text(mode)
            w = s.work.add_work(f"Fixture {mode}", f"Create ok in {work}.", dept="conformance",
                                task_class="conformance", risk="low", workdir=str(work),
                                acceptance="ok exists", assignee=None)
            return w, work

        def run_through_supervisor(mode: str, timeout: float = 120) -> tuple[dict, Path, dict]:
            w, work = fresh_work(mode)
            if s.dispatch(lead, "plan") is None or s.dispatch(ceo, "approve") is None:
                raise RuntimeError("fixture pipeline stalled before execute")
            prepared = s.prepare_dispatch(maker, "execute")
            if prepared is None:
                raise RuntimeError("no execute stage was claimable after the approval")
            proc = supervise(prepared, timeout)
            return w, work, {"prepared": prepared, "proc": proc}

        def check_prepare():
            w, work = fresh_work("ok")
            if s.dispatch(lead, "plan") is None or s.dispatch(ceo, "approve") is None:
                return False, "the fixture pipeline stalled"
            p = s.prepare_dispatch(maker, "execute")
            if p is None:
                return False, "no execute stage was claimable after the approval"
            env = p["env"]
            if env.get("HOLON_RUN_DIR") != env.get("HOLON_BRIEF_DIR"):
                return False, "HOLON_RUN_DIR and its alias disagree"
            for absent in ("HOLON_KEY", "HOLON_CHAIR_KEY", "HOLON_CHAIR_KEY_FILE"):
                if absent in env:
                    return False, f"the prepared environment carries {absent}"
            for present in ("HOLON_RUN_TOKEN", "HOLON_RESULT_PATH", "HOLON_RUN_ID", "HOLON_POSTURE"):
                if not env.get(present):
                    return False, f"the prepared environment lacks {present}"
            if "isolation" in p:
                return False, "an unisolated instance reports isolation fields"
            s.submit(p["run"], {"outcome": "success", "summary": "prepare clause", "documentation": "d"})
            return True, f"run dir, token and result path prepared; nothing a key could ride on"

        def check_env():
            w, work, r = run_through_supervisor("ok")
            dump = json.loads((work / "fx-env.json").read_text()) if (work / "fx-env.json").exists() else {}
            if "HOLON_RUN_DIR" not in dump:
                return False, "the body never saw HOLON_RUN_DIR"
            if dump.get("SUITE_CANARY"):
                return False, "the body saw the supervisor's canary: the supervisor passed its own environment"
            if "HOLON_CHAIR_KEY_FILE" in dump or "HOLON_CHAIR_KEY" in dump:
                return False, "the body saw the Chair key"
            return True, "the body saw the prepared environment and none of the supervisor's"

        def check_finish():
            w, work, r = run_through_supervisor("ok")
            ex = s.work.latest(w["id"], "execute")
            run = s.work.get_run(r["prepared"]["run"])
            if run["status"] != "succeeded":
                return False, f"the run ended '{run['status']}'; the supervisor never handed the body in"
            body = (s.work.output(ex["id"]) or {"body": {}})["body"]
            if not body.get("_trajectory"):
                return False, "the fixture's trajectory.json was never picked up"
            if (run["tokens_in"], run["tokens_out"], run["harness_build"]) != (100, 40, "fixture-9"):
                return False, f"usage on the run: {(run['tokens_in'], run['tokens_out'], run['harness_build'])}"
            if "refused: terminal under policy (fx-1)" not in (body.get("blockers") or []):
                return False, "the fixture's refusal is not a blocker in the stored result"
            v = s.work.latest(w["id"], "verify", "ready")
            if v:
                s.dispatch(lead, "verify", v["id"])   # the judge follows the hand-in in the kernel's process
            if s.work.get(w["id"])["status"] != "done":
                return False, f"work status {s.work.get(w['id'])['status']}: the hand-in never reached the judge"
            return True, "handed in, judged, trajectory stored, usage recorded, refusal blocked"

        def check_lease():
            vocab.LEASE_S["execute"] = 6
            w, work, r = run_through_supervisor("long", timeout=90)
            vocab.LEASE_S.update(old_lease)
            run = s.work.get_run(r["prepared"]["run"])
            if run["status"] != "succeeded":
                return False, f"a 12s body against a 6s lease ended '{run['status']}' — nobody renewed the lease"
            if not run.get("heartbeat_at"):
                return False, "the run completed but no heartbeat was ever recorded"
            return True, "long work outlived its lease: the supervisor renewed it"

        def check_timeout():
            w, work, r = run_through_supervisor("hang", timeout=60)
            run = s.work.get_run(r["prepared"]["run"])
            ex = s.work.latest(w["id"], "execute")
            body = (s.work.output(ex["id"]) or {"body": {}})["body"]
            if run["status"] not in ("failed", "timeout") and body.get("outcome") != "partial":
                return False, (f"a hanging body ended '{run['status']}' with outcome {body.get('outcome')}: "
                               "the timeout never killed it")
            return True, f"a hanging body was stopped: run '{run['status']}', outcome {body.get('outcome')}"

        clause("prepare", check_prepare)
        clause("env", check_env)
        clause("finish", check_finish)
        clause("lease", check_lease)
        clause("timeout", check_timeout)
    finally:
        vocab.LEASE_S.update(old_lease)
        s.close()
        shutil.rmtree(scratch, ignore_errors=True)
    return {"driver": "supervisor", "supervisor": supervisor_cmd, "clauses": results,
            "passed": sum(1 for r in results if r["ok"])}
