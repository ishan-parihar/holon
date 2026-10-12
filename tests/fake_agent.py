"""A stand-in harness that honors the brief contract. Behavior via FAKE_MODE:
  ok (default)    plan -> plan writing hello.txt; execute -> writes it; verify -> checks it; review -> approves
  dirty-planner   planner writes into the workdir (must be rejected by the read-only guard)
  garbage         prints junk, writes no result.json
  stdout          prints the envelope on stdout instead of writing result.json
  fail            executor reports outcome=failure
"""

import json
import os
import sys
from pathlib import Path

brief = json.loads((Path(os.environ["HOLON_BRIEF_DIR"]) / "brief.json").read_text())
mode = os.environ.get("FAKE_MODE", "ok")
kind = brief["kind"]
workdir = Path(brief["workdir"])
assert (Path(brief["context_md"])).read_text().startswith("# Context"), "context bundle missing"

if mode == "garbage":
    print("I did some things, trust me")
    sys.exit(0)

if kind == "plan":
    if mode == "dirty-planner":
        (workdir / "scribble.txt").write_text("planner should not write")
    env = {"kind": "plan", "findings": f"{workdir} has no hello.txt", "steps": ["1. write hello.txt"],
           "writes": ["hello.txt"], "risks": "none", "rollback": "rm hello.txt", "estimate": "small"}
elif kind == "execute":
    if mode == "fail":
        env = {"outcome": "failure", "summary": "could not write", "documentation": "disk said no"}
    else:
        (workdir / "hello.txt").write_text("hi\n")
        env = {"outcome": "success", "summary": "wrote hello.txt",
               "documentation": "# Done\n\nCreated `hello.txt` containing `hi`.",
               "artifacts": [str(workdir / "hello.txt")],
               "learnings": ["hello.txt lives at the workdir root", {"content": "greetings are lowercase",
                                                                      "kind": "constraint", "confidence": "high"}],
               "decisions": ["use plain text, no markdown"], "next_intent": "translate the greeting"}
elif kind == "review":
    env = {"decision": "approve", "note": "steps meet the acceptance criteria"}
else:
    ok = (workdir / "hello.txt").exists()
    env = {"verdict": "pass" if ok else "fail", "note": "hello.txt " + ("exists" if ok else "missing")}

if mode == "stdout":
    print("some chatter before the answer")
    print(json.dumps(env))
else:
    Path(brief["result_path"]).write_text(json.dumps(env))
