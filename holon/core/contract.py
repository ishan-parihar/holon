"""The result contract: what an agent must return for each stage, defined once.

`validate(stage, envelope)` checks an agent's output; `json_schema(stage)` publishes the same rules
as JSON Schema for harness authors (`holon contract schemas` writes schemas/; `holon lint` fails if
the published files drift from this module). No schema library: stdlib only.

Every envelope may also carry the common fields: `learnings` and `decisions` (become memory),
`proposals` (improvements, routed to the department's steward), `refusals` (policy refusals),
`usage` (what the run consumed) and `guard` (an incident fix's regression check).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from holon.core import vocab

MAX_DOC_CHARS = 200_000


@dataclass(frozen=True)
class F:
    type: str                      # text | str | int | num | bool | grade | list | object | text_or_list
    required: bool = False
    enum: tuple = ()
    items: "F | None" = None
    fields: dict = field(default_factory=dict)
    min_items: int = 0
    max_chars: int | None = None
    required_if: tuple | None = None   # (other_field, value): required when other_field == value
    lo: int | None = None
    hi: int | None = None
    doc: str = ""


TEXT = F("text")
STR_LIST = F("list", items=F("str"))
MEMORY_ENTRY = F("object", fields={"content": F("text", required=True), "kind": F("str", enum=vocab.MEMORY_KINDS),
                                   "confidence": F("str", enum=vocab.CONFIDENCE)})
PROPOSAL = F("object", fields={"title": F("text", required=True), "intent": F("text", required=True)})
COMMON = {
    "learnings": F("list", items=F("entry", items=MEMORY_ENTRY), doc="lessons (your memory) and facts (the project's)"),
    "decisions": F("list", items=F("entry", items=MEMORY_ENTRY), doc="decisions future work must respect"),
    "proposals": F("list", items=F("entry", items=PROPOSAL), doc="improvements, routed to the steward"),
    "refusals": F("list", items=F("object", fields={
        "tool": F("str", required=True), "reason": F("text", required=True),
        "policy": F("text", doc="the policy.json rule that refused it (roots, writes, commands, budget)"),
    }), doc="calls your harness refused under policy.json (H3): tool + reason + rule. The kernel treats "
            "them as blockers (rework or escalation), never breaches"),
    "usage": F("object", fields={
        "tokens_in": F("num", lo=0), "tokens_out": F("num", lo=0), "cost": F("num", lo=0),
        "tool_calls": F("num", lo=0), "duration_s": F("num", lo=0),
        "build": F("str", doc="the harness build that ran (its version or revision string); pass rates "
                          "group by it and a regression opens incident work (H10)"),
    }, doc="what the run consumed (H4): the kernel records it on the run — budgets and cost per "
           "verified result read it; a usage the driver measured wins over the envelope's claim"),
    "guard": F("object", fields={
        "name": F("str"), "cmd": F("str"), "timeout": F("num", lo=1),
    }, doc="a fix's regression guard (K-2), on an incident fix's execute result: registered when the "
           "fix verifies; the heartbeat runs it forever, and a failure reopens the incident"),
}
WORK_ITEM = {"title": F("text", required=True), "intent": F("text", required=True), "acceptance": TEXT,
             "dept": F("str"), "task_class": F("str"), "priority": F("int", lo=0, hi=4),
             "lane": F("str", enum=vocab.LANES, doc="fast: one session plans and acts (only work the kernel grades "
                                                   "G0); script: a routine's approved command, run by the kernel"),
             "cron": F("str", doc="makes the item a routine (a recurring job) on this 5-field schedule"),
             "command": F("str", doc="a script routine's command (lane script)"),
             "rubric": STR_LIST}

SPECS: dict[str, dict[str, dict[str, F]]] = {
    "research": {"": {
        "summary": F("text", required=True, doc="what the terrain looks like, in one paragraph"),
        "terrain": F("list", required=True, items=F("object", fields={
            "content": F("text", required=True), "source": F("text", required=True),
            "kind": F("str", enum=vocab.MEMORY_KINDS), "confidence": F("str", enum=vocab.CONFIDENCE)}),
            doc="one item per fact a planner needs, each with its source"),
        "open_questions": STR_LIST,
    }},
    "shape": {"": {
        "decision": F("str", required=True, enum=vocab.OUTCOMES["shape"]),
        "note": F("text", required=True, doc="why, for the planner and the requester"),
        "title": TEXT, "intent": TEXT, "acceptance": TEXT, "task_class": F("str"),
        "risk": F("str", enum=vocab.RISKS), "priority": F("int", lo=0, hi=4), "assignee": F("str"),
        "dept": F("str", required_if=("decision", "redirect")), "research": F("bool"),
    }},
    "plan": {
        "plan": {
            "kind": F("str", required=True, enum=("plan",)),
            "findings": F("text", required=True), "steps": F("text_or_list", required=True),
            "writes": STR_LIST, "external_effects": STR_LIST, "grade": F("grade"), "assignee": F("str"),
            "risks": TEXT, "rollback": TEXT, "estimate": TEXT, "acceptance": TEXT, "context_refs": STR_LIST,
        },
        "decompose": {
            "kind": F("str", required=True, enum=("decompose",)),
            "rationale": F("text", required=True), "grade": F("grade"),
            "children": F("list", required=True, min_items=2, items=F("object", fields={
                **WORK_ITEM, "assignee": F("str"), "risk": F("str", enum=vocab.RISKS), "workdir": F("str"),
                "after": F("list", items=F("int", lo=0))})),
        },
        "needs_input": {
            "kind": F("str", required=True, enum=("needs_input",)),
            "question": F("text", required=True, doc="one question with options and your recommendation"),
        },
    },
    "approve": {"": {
        "decision": F("str", required=True, enum=vocab.OUTCOMES["approve"]),
        "note": F("text", required_if=("decision", "reject")),
    }},
    "execute": {"": {
        "outcome": F("str", required=True, enum=vocab.OUTCOMES["execute"]),
        "summary": F("text", required=True), "documentation": F("text", required=True, max_chars=MAX_DOC_CHARS),
        "artifacts": STR_LIST, "blockers": STR_LIST, "next_intent": TEXT,
        "writes": F("list", items=F("str"), doc="paths you changed (the fast lane is graded again on them)"),
        "external_effects": F("list", items=F("str"), doc="anything you sent or published outside the organization"),
        "skill_changes": F("list", items=F("object", fields={
            "skill": F("str", required=True), "file": F("str", required=True),
            "content": F("text", required=True, max_chars=MAX_DOC_CHARS), "why": F("text", required=True)}),
            doc="department skill files to write; the approved plan must list each as `skill:<skill>/<file>`, "
                "and the kernel applies them when the work is verified"),
    }},
    "verify": {"": {
        "verdict": F("str", required=True, enum=vocab.OUTCOMES["verify"]),
        "note": F("text", required=True, doc="which criteria were checked, how, and what you found"),
    }},
    "retro": {"": {
        "summary": F("text", required=True), "cause": F("text", required=True, doc="the root cause, not the symptom"),
        "actions": F("list", items=F("object", fields=dict(WORK_ITEM)),
                     doc="each becomes new work in the department, planned and approved like any other; one with a "
                         "`cron` becomes a routine proposal for its approver"),
        "objective_met": F("bool", doc="an objective's review: its key results are met, so it closes"),
    }},
}
assert set(SPECS) == set(vocab.STAGES)


class EnvelopeError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _check(path: str, v: Any, f: F, problems: list[str]) -> None:
    t = f.type
    if t == "entry":  # a string, or an object of the item spec
        if isinstance(v, str):
            if not v.strip():
                problems.append(f"'{path}' is empty")
            return
        if not isinstance(v, dict):
            problems.append(f"'{path}' must be a string or an object")
            return
        _check(path, v, f.items, problems)
        return
    if t in ("text", "str"):
        if not isinstance(v, str) or (t == "text" and f.required and not v.strip()):
            problems.append(f"'{path}' must be a {'non-empty ' if f.required else ''}string")
        elif f.enum and v not in f.enum:
            problems.append(f"'{path}' must be one of {list(f.enum)}")
        elif f.max_chars and len(v) > f.max_chars:
            problems.append(f"'{path}' exceeds {f.max_chars} characters")
    elif t == "text_or_list":
        if isinstance(v, list):
            if not v or not all(isinstance(s, (str, dict)) for s in v):
                problems.append(f"'{path}' list must contain strings or objects")
        elif not isinstance(v, str) or not v.strip():
            problems.append(f"'{path}' must be a non-empty string or list")
    elif t == "int":
        if not isinstance(v, int) or isinstance(v, bool) or (f.lo is not None and v < f.lo) or \
                (f.hi is not None and v > f.hi):
            problems.append(f"'{path}' must be an integer" + (f" in {f.lo}..{f.hi}" if f.hi is not None else ""))
    elif t == "num":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            problems.append(f"'{path}' must be a number")
        elif f.lo is not None and v < f.lo:
            problems.append(f"'{path}' must be >= {f.lo}")
        elif f.hi is not None and v > f.hi:
            problems.append(f"'{path}' must be <= {f.hi}")
    elif t == "bool":
        if not isinstance(v, bool):
            problems.append(f"'{path}' must be true or false")
    elif t == "num":
        s = {"type": "number"}
        if f.lo is not None:
            s["minimum"] = f.lo
        if f.hi is not None:
            s["maximum"] = f.hi
    elif t == "grade":
        if parse_grade(v) is None:
            problems.append(f"'{path}' must be 0-4 or 'G0'..'G4'")
    elif t == "list":
        if not isinstance(v, list):
            problems.append(f"'{path}' must be a list")
            return
        if len(v) < f.min_items:
            problems.append(f"'{path}' must have at least {f.min_items} items")
        for i, x in enumerate(v):
            _check(f"{path}[{i}]", x, f.items, problems)
    elif t == "object":
        if not isinstance(v, dict):
            problems.append(f"'{path}' must be an object")
            return
        _fields(path + ".", v, f.fields, problems)


def _fields(prefix: str, env: dict, spec: dict[str, F], problems: list[str]) -> None:
    for name, f in spec.items():
        required = f.required or (f.required_if is not None and env.get(f.required_if[0]) == f.required_if[1])
        if env.get(name) is None:
            if required:
                problems.append(f"missing '{prefix}{name}'")
            continue
        _check(prefix + name, env[name], f, problems)


def variant(stage: str, env: dict) -> str:
    variants = SPECS[stage]
    return "" if "" in variants else env.get("kind", "")


def validate(stage: str, env: Any) -> dict:
    if stage not in SPECS:
        raise EnvelopeError([f"unknown stage {stage!r}"])
    if not isinstance(env, dict):
        raise EnvelopeError(["envelope must be a JSON object"])
    variants = SPECS[stage]
    key = variant(stage, env)
    if key not in variants:
        raise EnvelopeError([f"'kind' must be one of {[k for k in variants]}"])
    env = _repair_annotations(env)
    problems: list[str] = []
    _fields("", env, {**variants[key], **COMMON}, problems)
    if problems:
        raise EnvelopeError(problems)
    return env


def _repair_annotations(env: dict) -> dict:
    """A learning or decision whose `kind` or `confidence` is not in the vocabulary loses that label (memory's
    defaults apply) instead of voiding the result: annotations steer memory, not the work, and an hour of
    agent work must not be discarded over a label. Everything that decides an outcome stays strict."""
    allowed = {"kind": vocab.MEMORY_KINDS, "confidence": vocab.CONFIDENCE}
    out = dict(env)
    for key in ("learnings", "decisions"):
        if isinstance(out.get(key), list):
            out[key] = [{k: v for k, v in e.items() if k not in allowed or v in allowed[k]} if isinstance(e, dict) else e
                        for e in out[key]]
    return out


def parse_grade(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip().upper().lstrip("G")
    try:
        g = int(v)
    except (TypeError, ValueError):
        return None
    return g if 0 <= g <= 4 else None


# ── JSON Schema (published for harness authors) ─────────────────────────
def _schema(f: F) -> dict:
    t = f.type
    if t == "entry":
        return {"oneOf": [{"type": "string", "minLength": 1}, _schema(f.items)]}
    if t in ("text", "str"):
        s: dict = {"type": "string"}
        if f.enum:
            s["enum"] = list(f.enum)
        if t == "text" and f.required:
            s["minLength"] = 1
        if f.max_chars:
            s["maxLength"] = f.max_chars
    elif t == "text_or_list":
        s = {"oneOf": [{"type": "string", "minLength": 1},
                       {"type": "array", "minItems": 1, "items": {"type": ["string", "object"]}}]}
    elif t == "int":
        s = {"type": "integer"}
        if f.lo is not None:
            s["minimum"] = f.lo
        if f.hi is not None:
            s["maximum"] = f.hi
    elif t == "bool":
        s = {"type": "boolean"}
    elif t == "grade":
        s = {"oneOf": [{"type": "integer", "minimum": 0, "maximum": 4}, {"type": "string", "pattern": "^[Gg]?[0-4]$"}]}
    elif t == "list":
        s = {"type": "array", "items": _schema(f.items)}
        if f.min_items:
            s["minItems"] = f.min_items
    else:
        s = _object(f.fields)
    if f.doc:
        s["description"] = f.doc
    return s


def _object(spec: dict[str, F]) -> dict:
    s: dict = {"type": "object", "properties": {k: _schema(v) for k, v in spec.items()},
               "required": [k for k, v in spec.items() if v.required]}
    conds = [{"if": {"properties": {f.required_if[0]: {"const": f.required_if[1]}}, "required": [f.required_if[0]]},
              "then": {"required": [k]}} for k, f in spec.items() if f.required_if]
    if conds:
        s["allOf"] = conds
    if not s["required"]:
        del s["required"]
    return s


def json_schema(stage: str) -> dict:
    variants = SPECS[stage]
    body = ({"oneOf": [_object({**v, **COMMON}) for v in variants.values()]} if "" not in variants
            else _object({**variants[""], **COMMON}))
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"holon:{stage}-result",
            "title": f"Holon {stage} result", **body}


def schema_text(stage: str) -> str:
    return json.dumps(json_schema(stage), indent=2, ensure_ascii=False) + "\n"
