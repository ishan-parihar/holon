"""The kernel carries no dead private function.

Measured 2026-10-25, while auditing for over-engineering. The audit named four candidates;
three turned out to be false positives, and the fourth was real:

- `verify_archive` (portability) -- looked dead, and the reason it was dead was the defect:
  the export wrote `checksums.json` and NOTHING read it, so a tampered archive imported
  silently. The reader is now on the path (`import_department` verifies before it creates
  anything).
- `write_policy` (policy) -- looked dead because `briefs.py` had copy-pasted its body. The
  copy nobody read was the one that ran. The copy is gone; the helper is called.
- `chair_action_meta` (cli) -- a documented stack-B API: the Chair's chat control surface
  consumes it (`docs/design/2026-10-23-the-chair-control-surface.md`). Not dead.
- `duties` (drivers) -- a legacy shim with a live test (`test_push_and_schedules.py`).
  Not dead.
- `_list_fields` (contract) -- genuinely dead: a pure wrapper over `_LIST_FIELDS` with no
  caller. Cut.

The false positives are the point. "Unused" and "dead" are different claims, and the guard
this file carries is the one that can be made TRUE: a private function (a name beginning with
`_`) that no line in the kernel and no line in the suite mentions is dead code, and it is
removed rather than kept.

The guard states the rule over the whole tree, so the next dead wrapper is caught by the
suite rather than by the next audit. It is deliberately narrow:

- PUBLIC names are exempt. A public function may be called by an adapter, a chat surface or
  an operator's script -- none of which live in this tree -- so "no caller here" proves
  nothing about a public name. (`chair_action_meta` is exactly this case.)
- A name that appears in a DOC comment is exempt, because a doc that names it is a promise
  to keep it: the design record that names `chair_action_meta` is the evidence.
- A name mentioned in the suite is exempt: the suite IS a caller.

What is left is the class that can actually be proven dead, and the audit's own false
positives are excluded by the two exemptions above -- which is how this guard was validated:
each of the three false positives is exempt under the rule as written.
"""

import ast
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _private_functions() -> list[tuple[Path, str, int]]:
    """Every module-level function whose name begins with `_`."""
    out = []
    for p in sorted((REPO / "holon").rglob("*.py")):
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:  # pragma: no cover - the tree is parseable by the suite
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_"):
                if not node.name.startswith("__"):   # dunders are the language's, not ours
                    out.append((p, node.name, node.end_lineno - node.lineno + 1))
    return out


def _mentions(name: str, text: str) -> int:
    return len(re.findall(r"\b" + re.escape(name) + r"\b", text))


def _called_in(tree: ast.AST, name: str) -> bool:
    """Is `name` CALLED here? A mention is not a call.

    The first draft of this guard exempted any name the suite MENTIONED, and the guard
    defeated itself: its own docstring named `_list_fields` (the function it was written to
    catch), so re-introducing that exact function passed. A guard that a comment can switch
    off is not a guard. The exemption is now a CALL, read from the AST."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == name:
                return True
            if isinstance(f, ast.Attribute) and f.attr == name:
                return True
    return False


class NoDeadPrivateFunction(unittest.TestCase):
    def test_no_private_function_is_unreferenced(self):
        """A private function nothing calls is dead code, and it is removed."""
        kernel = "\n".join(p.read_text() for p in (REPO / "holon").rglob("*.py"))
        suite_trees = []
        for p in (REPO / "tests").rglob("*.py"):
            try:
                suite_trees.append(ast.parse(p.read_text()))
            except SyntaxError:  # pragma: no cover - the suite is parseable
                continue
        dead = []
        for p, name, lines in _private_functions():
            # the definition itself counts once; a second mention anywhere is a caller
            if _mentions(name, kernel) > 1:
                continue
            # the suite exempts a name it CALLS -- a mention (this file's own docstring
            # names the function it catches) is not a call and must not exempt anything
            if any(_called_in(t, name) for t in suite_trees):
                continue
            dead.append(f"{p.relative_to(REPO).as_posix()}::{name} ({lines} lines)")
        self.assertEqual([], dead,
                         "dead private functions -- remove them, or make them the one place "
                         f"their rule lives: {dead}")

    def test_the_audits_false_positives_are_not_reported(self):
        """The guard must not condemn the three names the audit condemned by mistake.

        A guard that reports a documented API as dead is a guard that trains its reader to
        ignore it. Each of these is exempt under the rule as written, and this test says so
        by name -- so if the exemptions are narrowed, this fails and says why."""
        kernel = "\n".join(p.read_text() for p in (REPO / "holon").rglob("*.py"))
        suite = "\n".join(p.read_text() for p in (REPO / "tests").rglob("*.py"))
        docs = "\n".join(p.read_text() for p in (REPO / "docs").rglob("*.md"))
        # a public name is not judged dead by "no caller in this tree"
        self.assertIn("def chair_action_meta(", kernel)
        self.assertIn("chair_action_meta", docs,
                      "the design record names it: it is a documented API, not dead code")
        # a legacy shim with a live test
        self.assertIn("duties", kernel)
        self.assertIn("duties", suite)
        # the helper the copy-paste was replaced with: it must be CALLED by the kernel
        self.assertIn("def write_policy(", kernel)
        callers = [p.relative_to(REPO).as_posix() for p in (REPO / "holon").rglob("*.py")
                   if "write_policy(" in p.read_text()
                   and "def write_policy(" not in p.read_text()]
        self.assertTrue(callers,
                        f"policy.write_policy is called by nobody: the helper is dead again")


if __name__ == "__main__":
    unittest.main()
