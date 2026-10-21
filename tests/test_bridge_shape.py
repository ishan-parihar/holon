"""Tests for the shape gap found on 2026-10-20 — the one that kept both harnesses at 1/5.

**What was measured.** A real body produced a complete, well-formed plan and submitted
it through the MCP server. The client transmitted every list wrapped in `{"item": [...]}`
— it had no field types to coerce *to*, because `holon_submit` published `result` as a
free-form object with no `properties`. The kernel rejected the plan with seven field
complaints, and the body's work was lost.

Two repairs, each guarded here:
- **B. The server accepts the wrapper.** A list that arrives as `{"item": [...]}` is
  unwrapped, so an hour of agent work is not discarded over a shape detail.
- **A. The tool publishes the contract.** `holon_submit` publishes the stage's real
  envelope schema, so a coercing client has the types it needs and does not guess.
"""

import json

from holon.core import contract
from holon.mcp import tools
from tests.helpers import KernelCase


class UntypedListIsUnwrapped(KernelCase):
    """Repair B. The kernel does not void correct work over a wrapper."""

    def envelope(self):
        return {"kind": "plan", "findings": "the workdir is empty",
                "steps": ["1. write ok.txt"], "writes": ["/tmp/ok.txt"],
                "external_effects": [], "grade": "G0", "risks": "none",
                "rollback": "rm ok.txt", "estimate": "small: <5 min",
                "learnings": [], "proposals": []}

    def test_a_wrapped_list_is_accepted_where_the_contract_says_list(self):
        """The exact shape the client transmitted: seven list fields wrapped. The body's
        own envelope is valid; the wrapped one must be too."""
        env = self.envelope()
        wrapped = {k: ({"item": v} if isinstance(v, list) else v) for k, v in env.items()}
        self.assertEqual(contract.validate("plan", env)["steps"], ["1. write ok.txt"])
        got = contract.validate("plan", wrapped)
        self.assertEqual(got["steps"], ["1. write ok.txt"], "the steps survived the wrapper")
        self.assertEqual(got["writes"], ["/tmp/ok.txt"])
        self.assertEqual(got["learnings"], [])

    def test_the_repair_never_turns_a_real_object_into_a_list(self):
        """The repair is narrow: only a dict whose ONLY key is `item` and whose value is
        a list. A genuine object is untouched — nothing that decides an outcome is
        relaxed."""
        env = {"kind": "plan", "findings": "f", "steps": ["1. x"], "writes": ["/tmp/a"],
               "external_effects": [], "grade": "G0", "risks": "r", "rollback": "rm",
               "estimate": "small", "learnings": [], "proposals": [],
               "decision_note": {"item": "not a list"}}
        got = contract.validate("plan", env)
        self.assertEqual(got["decision_note"], {"item": "not a list"},
                         "a dict that is not a wrapper is left alone")

    def test_a_wrapped_field_that_is_not_a_list_is_still_rejected(self):
        """The repair does not become a loophole: a field the contract wants as a list
        and which arrives as a wrapper around a non-list is still wrong."""
        env = self.envelope()
        env["writes"] = {"item": "not a list"}
        with self.assertRaises(contract.EnvelopeError):
            contract.validate("plan", env)

    def test_the_repair_does_not_relax_a_required_field(self):
        """A missing required field is still missing. The repair is a shape, not a
        waiver."""
        env = self.envelope()
        del env["findings"]
        with self.assertRaises(contract.EnvelopeError):
            contract.validate("plan", env)


class SubmitToolPublishesTheContract(KernelCase):
    """Repair A. The tool tells the client what a list is."""

    def test_the_submit_tool_publishes_the_result_envelope_not_a_free_form_object(self):
        """A free-form `result` is what let the client guess. The tool must publish the
        stage's field types — the same ones the brief carries.

        The test sets a run token so the server knows the stage: without one there is no
        stage to publish, and the generic fallback is the correct behaviour (the other
        guard below says so). So this asserts the *typed* form, not that a typed form
        happens to exist."""
        import os
        # a run the server can resolve: the tool reads the stage from the run token, and
        # the instance from HOLON_HOME
        work = self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        token = c.token
        old = {k: os.environ.get(k) for k in ("HOLON_RUN_TOKEN", "HOLON_HOME")}
        os.environ["HOLON_RUN_TOKEN"] = token
        os.environ["HOLON_HOME"] = str(self.home.root)
        try:
            tool = next(t for t in tools() if t["name"] == "holon_submit")
            result = tool["inputSchema"]["properties"]["result"]
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertTrue(result.get("properties"),
                        "the tool must publish the stage's envelope fields, not a free-form object")
        for key in ("steps", "writes", "external_effects", "learnings"):
            if key in result["properties"]:
                self.assertIn("array", json.dumps(result["properties"][key]),
                              f"{key} must be published as a list")

    def test_the_generic_fallback_still_says_what_result_is(self):
        """Without a run there is no stage to publish, so the tool falls back to the
        generic object — and the description must point the body at the brief."""
        tool = next(t for t in tools() if t["name"] == "holon_submit")
        result = tool["inputSchema"]["properties"]["result"]
        self.assertIn("brief", result.get("description", "").lower(),
                      "the fallback must point the body at the brief's Output section")
