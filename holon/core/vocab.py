"""The organization's vocabulary: every enumeration Holon uses, defined once.

Code imports these; SQL CHECK constraints, CLI choices, JSON Schemas and documentation tables
are generated from or linted against them (`holon lint`, pack SCHEMA). Never copy a list.
"""

from __future__ import annotations

# ── the genome: six roles, and the pipeline stage each one performs ─────────
ROLES = ("head", "researcher", "planner", "executor", "verifier", "steward")

# A stage is one unit of work done by one role. A work node's pipeline is a chain of stages.
STAGES = ("research", "shape", "plan", "approve", "execute", "verify", "retro")
STAGE_ROLE = {"research": "researcher", "shape": "head", "plan": "planner", "approve": "head",
              "execute": "executor", "verify": "verifier", "retro": "steward"}
ROLE_STAGES = {r: tuple(s for s in STAGES if STAGE_ROLE[s] == r) for r in ROLES}
# observe = may read anything, must change nothing outside Holon's records; act = changes the world
POSTURE = {s: ("act" if s == "execute" else "observe") for s in STAGES}
LEASE_S = {"research": 1800, "shape": 900, "plan": 1800, "approve": 900, "execute": 3600, "verify": 1200,
           "retro": 1200}

# ── nodes ────────────────────────────────────────────────────────────────
# a work node has no stage; it holds stages (its pipeline) and/or child work nodes
WORK_STATUSES = ("blocked", "open", "waiting", "needs_input", "done", "failed", "cancelled")
STAGE_STATUSES = ("ready", "claimed", "done", "failed", "cancelled")
TERMINAL = ("done", "failed", "cancelled")
SOURCES = ("chair", "agent", "audit", "schedule", "kernel")
RISKS = ("low", "normal", "high", "irreversible")
GRADES = (0, 1, 2, 3, 4)
GRADE_NAMES = {0: "routine", 1: "minor", 2: "significant", 3: "major", 4: "critical"}
PRIORITIES = (0, 1, 2, 3, 4)
# how a piece of work is run, cheapest first: the kernel runs an approved command (script); one agent
# session plans and acts on routine work (fast); the full pipeline of separate stages (full)
LANES = ("script", "fast", "full")

# stage outcomes (tasks.outcome on a finished stage)
OUTCOMES = {
    "research": ("mapped",),
    "shape": ("accept", "redirect", "cancel"),
    "plan": ("plan", "decompose", "needs_input"),
    "approve": ("approve", "reject"),
    "execute": ("success", "partial", "failure", "noop"),
    "verify": ("pass", "fail"),
    "retro": ("done",),
}

# ── runs ─────────────────────────────────────────────────────────────────
RUN_STATUSES = ("running", "succeeded", "failed", "timeout", "abandoned")

# ── people and authority ─────────────────────────────────────────────────
EMPLOYEE_STATUSES = ("probation", "active", "paused", "retired")
CHAIR = "chair"
EXECUTIVE = "executive"
# who may run a command (the command registry declares one per command; R3 enforces it)
ACTOR_CLASSES = ("chair", "employee", "any", "kernel")

# ── memory ───────────────────────────────────────────────────────────────
MEMORY_KINDS = ("fact", "decision", "constraint", "preference", "lesson", "open_question", "glossary", "artifact")
STEERING_KINDS = ("constraint", "decision", "preference")
PERSONAL_KINDS = ("lesson", "preference")
CONFIDENCE = ("low", "medium", "high")

# ── messages and schedules ───────────────────────────────────────────────
MESSAGE_KINDS = ("dm", "channel", "request", "question", "answer", "proposal")
SCHEDULE_KINDS = ("wake", "recurring")

# ── skills ───────────────────────────────────────────────────────────────
SKILL_KINDS = ("protocol", "role", "operation")
