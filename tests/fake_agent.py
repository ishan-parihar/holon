"""A stand-in harness that honors the brief contract. Behavior via FAKE_MODE:
  ok (default)    every stage answers sensibly; execute writes hello.txt
  dirty-planner   observe stages write into the workdir (must be rejected by the read-only guard)
  garbage         prints junk, writes no result.json
  stdout          prints the envelope on stdout instead of writing result.json
  fail            executor reports outcome=failure
  self-submit     the agent submits its own result with its run token (pull-in-push)
  cron-writes     something else writes data/log.jsonl in the workdir while the agent observes
  linger          self-submits, then keeps running for a long time (a harness writing a closing reply)
  wrapped         prints a harness summary {"text": <reply>, "usage": {...}} (jcode, Claude Code style)
  env-probe       execute's documentation appends probe: IMAP_KEY=… (G8: prove a dept secret reached
                  the body's environment, push lane and broker lane)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

brief = json.loads((Path(os.environ["HOLON_BRIEF_DIR"]) / "brief.json").read_text())
mode = os.environ.get("FAKE_MODE", "ok")
modes = set(mode.split("+"))  # e.g. "dirty-planner+self-submit"
stage = brief["stage"]
workdir = Path(brief["workdir"])
assert Path(brief["context_md"]).read_text().startswith("# Context"), "context bundle missing"
assert os.environ.get("HOLON_RUN_TOKEN"), "push mode must export the run token"
assert os.environ["HOLON_STAGE"] == stage and os.environ["HOLON_POSTURE"] in ("observe", "act")
assert "HOLON_CHAIR_KEY" not in os.environ, "the Chair key must never reach an agent"

if mode == "garbage":
    print("I did some things, trust me")
    sys.exit(0)
if "dirty-planner" in modes and os.environ["HOLON_POSTURE"] == "observe":
    (workdir / "scribble.txt").write_text("an observer should not write")
if "cron-writes" in modes:  # another process sharing the tree, not the agent
    (workdir / "data").mkdir(exist_ok=True)
    (workdir / "data" / "log.jsonl").write_text('{"tick": 1}\n')
if "pycache" in modes:  # running the tests while observing leaves a cache behind
    (workdir / "__pycache__").mkdir(exist_ok=True)
    (workdir / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"cache")

if stage == "research":
    env = {"summary": "an empty directory", "terrain": [{"content": f"{workdir} starts empty", "source": "ls"}]}
elif stage == "shape":
    env = {"decision": "accept", "note": "clear enough", "acceptance": "hello.txt exists"}
elif stage == "plan":
    env = {"kind": "plan", "findings": f"{workdir} has no hello.txt", "steps": ["1. write hello.txt"],
           "writes": ["hello.txt"], "risks": "none", "rollback": "rm hello.txt", "estimate": "small"}
elif stage == "approve":
    env = {"decision": "approve", "note": "steps meet the acceptance criteria"}
elif stage == "execute":
    if mode == "fail":
        env = {"outcome": "failure", "summary": "could not write", "documentation": "disk said no"}
    else:
        target = "conformance.txt" if "conformance.txt" in Path(brief["context_md"]).read_text() else "hello.txt"
        (workdir / target).write_text("ok\n" if target == "conformance.txt" else "hi\n")
        env = {"outcome": "success", "summary": "wrote hello.txt",
               "documentation": "# Done\n\nCreated `hello.txt` containing `hi`.",
               "artifacts": [str(workdir / "hello.txt")],
               "learnings": ["hello.txt lives at the workdir root", {"content": "greetings are lowercase",
                                                                      "kind": "constraint", "confidence": "high"}],
               "decisions": ["use plain text, no markdown"], "next_intent": "translate the greeting",
               "proposals": ["add a greeting style guide"]}
elif stage == "verify":
    ok = (workdir / "hello.txt").exists() or (workdir / "conformance.txt").exists()
    env = {"verdict": "pass" if ok else "fail", "note": "the file " + ("exists" if ok else "is missing")}
else:  # retro
    env = {"summary": "a write failed", "cause": "the disk was full", "learnings": ["check disk space first"],
           "actions": [{"title": "Add a disk check", "intent": "fail fast when the disk is full"}]}

if "env-probe" in modes and stage == "execute":   # G8: prove a dept secret reached the body
    env["documentation"] += f"\n\nprobe: IMAP_KEY={os.environ.get('IMAP_KEY')!r}"

if mode == "wrapped":
    print(json.dumps({"text": "Done.\n" + json.dumps(env), "total_cost_usd": 0.01,
                      "usage": {"input_tokens": 100, "cache_read_input_tokens": 20, "output_tokens": 7}}))
elif mode == "stdout":
    print("some chatter before the answer")
    print(json.dumps(env))
elif "linger" in modes:
    print("[trace] token_usage input=900 output=40", file=sys.stderr, flush=True)
    print("[trace] token_usage input=1100 output=60", file=sys.stderr, flush=True)
    Path(brief["result_path"]).write_text(json.dumps(env))
    subprocess.run([sys.executable, "-m", "holon", "--home", os.environ["HOLON_HOME"], "submit", brief["run_id"]],
                   check=True, capture_output=True)
    import time
    time.sleep(60)
elif "self-submit" in modes:
    Path(brief["result_path"]).write_text(json.dumps(env))
    subprocess.run([sys.executable, "-m", "holon", "--home", os.environ["HOLON_HOME"], "submit", brief["run_id"]],
                   check=True, capture_output=True)
else:
    Path(brief["result_path"]).write_text(json.dumps(env))
