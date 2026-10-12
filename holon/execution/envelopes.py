"""Result envelopes: the only thing an agent must produce, whatever harness runs it.

An agent writes `result.json` into its brief dir. Four shapes, by duty:

planner  -> {"kind": "plan", "findings", "steps", "writes"?, "external_effects"?, "assignee"?,
             "grade"?, "risks"?, "rollback"?, "estimate"?, "context_refs"?, "acceptance"?}
         |  {"kind": "decompose", "rationale", "grade"?,
             "children": [{"title", "intent", "dept"?, "assignee"?, ...}, ...]}
         |  {"kind": "needs_input", "question"}
executor -> {"outcome", "summary", "documentation", "artifacts"?, "learnings"?, "decisions"?,
             "blockers"?, "proposals"?, "next_intent"?}
verifier -> {"verdict": "pass" | "fail", "note"}
reviewer -> {"decision": "approve" | "reject", "note"}   (note required to reject)

JSON Schemas mirroring these rules ship in schemas/ for harness authors; the kernel
validates by hand so it needs no schema library.
"""

from __future__ import annotations

import json
from typing import Any

OUTCOMES = ("success", "partial", "failure", "noop")
MEMORY_KINDS = ("fact", "decision", "constraint", "preference", "lesson", "open_question", "glossary", "artifact")
CONFIDENCE = ("low", "medium", "high")
MAX_DOC_CHARS = 200_000


class EnvelopeError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _text(env: dict, key: str, problems: list[str], required: bool = True) -> None:
    v = env.get(key)
    if v is None:
        if required:
            problems.append(f"missing '{key}'")
        return
    if isinstance(v, list) and key == "steps":
        if not v or not all(isinstance(s, (str, dict)) for s in v):
            problems.append("'steps' list must contain strings or objects")
        return
    if not isinstance(v, str) or (required and not v.strip()):
        problems.append(f"'{key}' must be a non-empty string")


def _list(env: dict, key: str, problems: list[str]) -> None:
    v = env.get(key)
    if v is not None and not isinstance(v, list):
        problems.append(f"'{key}' must be a list")


def _memory_entries(env: dict, key: str, problems: list[str]) -> None:
    for i, e in enumerate(env.get(key) or []):
        if isinstance(e, str):
            if not e.strip():
                problems.append(f"'{key}[{i}]' is empty")
        elif isinstance(e, dict):
            if not isinstance(e.get("content"), str) or not e["content"].strip():
                problems.append(f"'{key}[{i}].content' must be a non-empty string")
            if "kind" in e and e["kind"] not in MEMORY_KINDS:
                problems.append(f"'{key}[{i}].kind' must be one of {MEMORY_KINDS}")
            if "confidence" in e and e["confidence"] not in CONFIDENCE:
                problems.append(f"'{key}[{i}].confidence' must be one of {CONFIDENCE}")
        else:
            problems.append(f"'{key}[{i}]' must be a string or object")


def validate_planner(env: Any) -> dict:
    if not isinstance(env, dict):
        raise EnvelopeError(["envelope must be a JSON object"])
    problems: list[str] = []
    kind = env.get("kind")
    if kind == "plan":
        _text(env, "findings", problems)
        _text(env, "steps", problems)
        for k in ("risks", "rollback", "estimate", "acceptance"):
            _text(env, k, problems, required=False)
        for k in ("writes", "context_refs", "external_effects"):
            _list(env, k, problems)
        _text(env, "assignee", problems, required=False)
    elif kind == "decompose":
        _text(env, "rationale", problems)
        children = env.get("children")
        if not isinstance(children, list) or len(children) < 2:
            problems.append("'children' must list at least 2 sub-tasks")
        else:
            for i, c in enumerate(children):
                if not isinstance(c, dict) or not str(c.get("title", "")).strip() or not str(c.get("intent", "")).strip():
                    problems.append(f"'children[{i}]' needs non-empty 'title' and 'intent'")
                elif "priority" in c and not isinstance(c["priority"], int):
                    problems.append(f"'children[{i}].priority' must be an integer")
                elif any(k in c and not isinstance(c[k], str) for k in ("dept", "assignee", "task_class")):
                    problems.append(f"'children[{i}]' dept/assignee/task_class must be strings")
    elif kind == "needs_input":
        _text(env, "question", problems)
    else:
        problems.append("'kind' must be one of: plan, decompose, needs_input")
    if kind in ("plan", "decompose") and "grade" in env and _grade(env["grade"]) is None:
        problems.append("'grade' must be 0-4 or 'G0'..'G4'")
    if problems:
        raise EnvelopeError(problems)
    return env


def validate_executor(env: Any) -> dict:
    if not isinstance(env, dict):
        raise EnvelopeError(["envelope must be a JSON object"])
    problems: list[str] = []
    if env.get("outcome") not in OUTCOMES:
        problems.append(f"'outcome' must be one of {OUTCOMES}")
    _text(env, "summary", problems)
    _text(env, "documentation", problems)
    if isinstance(env.get("documentation"), str) and len(env["documentation"]) > MAX_DOC_CHARS:
        problems.append(f"'documentation' exceeds {MAX_DOC_CHARS} chars")
    for k in ("artifacts", "learnings", "decisions", "blockers", "proposals"):
        _list(env, k, problems)
    _memory_entries(env, "learnings", problems)
    _memory_entries(env, "decisions", problems)
    _text(env, "next_intent", problems, required=False)
    if problems:
        raise EnvelopeError(problems)
    return env


def validate_verifier(env: Any) -> dict:
    if not isinstance(env, dict):
        raise EnvelopeError(["envelope must be a JSON object"])
    problems: list[str] = []
    if env.get("verdict") not in ("pass", "fail"):
        problems.append("'verdict' must be 'pass' or 'fail'")
    _text(env, "note", problems)
    if problems:
        raise EnvelopeError(problems)
    return env


def validate_reviewer(env: Any) -> dict:
    if not isinstance(env, dict):
        raise EnvelopeError(["envelope must be a JSON object"])
    problems: list[str] = []
    if env.get("decision") not in ("approve", "reject"):
        problems.append("'decision' must be 'approve' or 'reject'")
    _text(env, "note", problems, required=env.get("decision") == "reject")
    if problems:
        raise EnvelopeError(problems)
    return env


def _grade(v: Any) -> int | None:
    if isinstance(v, str):
        v = v.strip().upper().lstrip("G")
    try:
        g = int(v)
    except (TypeError, ValueError):
        return None
    return g if 0 <= g <= 4 else None


VALIDATORS = {"plan": validate_planner, "execute": validate_executor, "verify": validate_verifier,
              "review": validate_reviewer}


def extract_last_json(text: str) -> dict | None:
    """Best effort for harnesses that print instead of writing result.json:
    return the last top-level JSON object found in the text."""
    decoder = json.JSONDecoder()
    found = None
    i = 0
    while True:
        start = text.find("{", i)
        if start < 0:
            return found
        try:
            obj, end = decoder.raw_decode(text, start)
            if isinstance(obj, dict):
                found = obj
            i = end
        except json.JSONDecodeError:
            i = start + 1
