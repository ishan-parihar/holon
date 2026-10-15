"""A reference supervisor (CONTRACT §12): reads the prepared JSON on stdin, runs the body with
exactly the prepared environment (plus the OS baseline, never its own secrets), renews the
lease while the body runs (at most lease/3), enforces the run's timeout by killing the process
group, and hands the body in with `holon run finish`. Used by the supervisor conformance suite
(tests/test_adapters.py) as the known-good supervisor; a runtime with its own supervisor runs
the same suite with its command.
"""

import json
import os
import subprocess
import sys
import threading
import time

p = json.load(sys.stdin)
BASE = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "PYTHONPATH")}


def kernel(args: str, extra_env: dict | None = None) -> None:
    p = subprocess.run(f"{p_env['HOLON']} {args}", shell=True, capture_output=True, text=True,
                       env={**BASE, "HOLON_HOME": os.environ.get("HOLON_HOME", ""),
                            "HOLON_CHAIR_KEY_FILE": os.environ.get("HOLON_CHAIR_KEY_FILE", ""),
                            **(extra_env or {})})
    if p.returncode:
        print(f"supervisor: `holon {args}` exited {p.returncode}: {(p.stderr or p.stdout).strip()[-300:]}",
              file=sys.stderr)


p_env = p["env"]


proc = subprocess.Popen(p["command"], shell=True, cwd=p["cwd"],
                        env={**BASE, **p["env"]}, start_new_session=True)


def renew():  # lease renewal, at most every lease/3 (the suite runs a 6s lease: every 2s here)
    while proc.poll() is None:
        time.sleep(2)
        kernel(f"run heartbeat {p['run']}", {"HOLON_RUN_TOKEN": p["env"]["HOLON_RUN_TOKEN"]})


threading.Thread(target=renew, daemon=True).start()
try:
    proc.wait(timeout=p["timeout_s"])
    kernel(f"run finish {p['run']}")
except subprocess.TimeoutExpired:
    os.killpg(proc.pid, 9)   # the whole process group: the timeout is the supervisor's to enforce
    kernel(f"run finish {p['run']} --exit 124")
