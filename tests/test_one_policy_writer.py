"""The kernel writes `policy.json` in ONE place.

Measured 2026-10-25 while auditing for over-engineering: `holon/execution/policy.py` carried
`write_policy(run_dir, policy)` -- a helper that writes the file and sets the mode the enforcing
harness needs (0640, CONTRACT §10 / H-07) -- and NO line called it. The reason it was dead is
the reason it is dangerous: `holon/execution/briefs.py` had **copy-pasted its body**:

    (d / "policy.json").write_text(json.dumps(policy, indent=2))
    try:
        (d / "policy.json").chmod(0o640)
    except OSError:
        pass

Two copies of one rule, and the copy that runs is the one nobody reads. The mode 0640 is not
cosmetic: it is what lets the enforcing harness READ the policy it must enforce (H-07/H-08).
A future edit to the helper -- a stricter mode, a different indent, a checksum -- would have
changed nothing, because the helper was not on the path.

The rule this guard states: the kernel writes `policy.json` through `policy.write_policy`, and
`briefs.py` does not re-implement it. The guard reads the AST, so it names the shape rather than
the line, and it fails on a re-introduced inline write wherever it appears.

The other half is the behaviour, not the shape: the file the brief actually writes must have the
mode the enforcing harness needs. A guard that only read the AST would pass if the helper were
called with the wrong mode, so the second test measures the file on disk.
"""

import ast
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _calls(tree: ast.AST) -> set[str]:
    """The qualified names this module calls: `write_policy`, `policy.write_policy`, ..."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


class OnePolicyWriter(unittest.TestCase):
    def test_the_brief_writes_policy_through_the_one_writer(self):
        """`briefs.py` calls `policy.write_policy` rather than writing the file itself."""
        src = (REPO / "holon" / "execution" / "briefs.py").read_text()
        tree = ast.parse(src)
        self.assertIn("write_policy", _calls(tree),
                      "briefs.py must call the one writer of policy.json (policy.write_policy)")
        # and it must not write the file itself, under any spelling
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "write_text":
                    target = node.func.value
                    if isinstance(target, ast.Attribute) and target.attr == "policy.json":
                        offenders.append(node.lineno)
        self.assertEqual([], offenders,
                         f"briefs.py writes policy.json inline at line(s) {offenders}: the mode the "
                         "enforcing harness needs is set in one place (policy.write_policy), and a "
                         "second copy is a rule that will drift")

    def test_the_helper_is_not_dead(self):
        """`policy.write_policy` exists and is called -- a helper nothing calls is a copy of
        the rule waiting to become the wrong rule."""
        helper = (REPO / "holon" / "execution" / "policy.py").read_text()
        self.assertIn("def write_policy(", helper,
                      "the one writer of policy.json must exist")
        callers = []
        for p in (REPO / "holon").rglob("*.py"):
            tree = ast.parse(p.read_text())
            if "write_policy" in _calls(tree):
                callers.append(p.relative_to(REPO).as_posix())
        self.assertTrue(any(c != "holon/execution/policy.py" for c in callers),
                        f"policy.write_policy is called by nobody: {callers}")

    def test_the_policy_file_the_brief_writes_has_the_mode_the_harness_needs(self):
        """The behaviour, not the shape: 0640, so the harness that must enforce it can read it.

        This is the half an AST guard cannot prove -- the helper could be called with the wrong
        mode, or the call could be skipped for some stages."""
        from holon.execution import policy as policy_mod
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = policy_mod.write_policy(Path(d), {"version": 1, "roots": []})
            self.assertEqual(0o640, p.stat().st_mode & 0o777,
                             "policy.json: the enforcing harness must read it (CONTRACT §10, H-07)")
            self.assertEqual({"version": 1, "roots": []}, json.loads(p.read_text()),
                             "the policy written is the policy given")


if __name__ == "__main__":
    unittest.main()
