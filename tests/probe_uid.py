"""Helper for `test_the_runner_refuses_a_real_uid_the_comparator_cannot_see`.

Every other test in the suite measures the runner's uid decision with a uid that
does not exist: it passes `os.getuid() + 1`, a number, never a process. The
comparator fires either way, so nothing in the suite can tell a working boundary
from a broken one -- and the isolation system is one sentence: a process the
kernel's user does not own may not start processes here.

This helper exists so one test can prove that sentence against a uid that
actually exists. It re-execs itself inside an unprivileged user namespace with
`--map-user`, so the connecting process is genuinely a different uid as far as
`SO_PEERCRED` -- the mechanism `peer_uid` reads, and the only one that answers
from outside the process -- is concerned. The kernel decides what that uid is,
not the test. Root is not required, which is why the guard can run anywhere.

Usage: `python3 tests/probe_uid.py <abstract-socket-address>`
It prints the runner's reply (refusal, or the run's exit code) to stdout.
"""

import os
import socket
import sys

UID = 99  # arbitrary; the point is that it is NOT the caller's uid


def connect_as_a_different_uid(address: str) -> None:
    """Connect to the runner and ask it to run `id -u`, reporting what it says."""
    # The namespaced child is exec'd fresh, so nothing it inherits about the
    # caller's cwd or PYTHONPATH can be relied on: it finds the distribution
    # from its own location, the same way the kernel's launcher does.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from holon.execution import isolation

    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect("\0" + address[1:] if address.startswith("@") else address)
    with conn:
        msg = (b'{"cmd": "id -u", "cwd": "/", "env": {}, "timeout_s": 30}\n')
        conn.sendall(msg)
        print(repr(isolation._recv(conn.makefile("rb"))), flush=True)


def main() -> int:
    if os.environ.get("PROBE_UID_REEXECED") != "1":
        # First pass: re-exec inside a user namespace whose single mapping is a
        # different uid. unshare is a util-linux binary, not a Python module, so
        # the child is a real process with a real (namespaced) uid.
        os.environ["PROBE_UID_REEXECED"] = "1"
        os.execvp("unshare", ["unshare", "-U", f"--map-user={UID}",
                              sys.executable, os.path.abspath(__file__), *sys.argv[1:]])
    connect_as_a_different_uid(sys.argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
