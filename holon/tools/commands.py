"""`holon help` and the distribution's command reference.

`holon help` prints the registry as a terminal table. This module is the same registry as
prose: `docs/COMMANDS.md` is generated from it (`python3 -m holon.tools.commands`), so a command
that exists and is not documented is a lint finding rather than a reader's surprise.

Why generated rather than written: a hand-written reference of 119 commands is a document that
starts wrong on the day a command is added and is never corrected. The one-line help each
command already declares (`@command(..., "what it does")`) is the smallest honest description,
and every command necessarily has one because `command()` requires it.
"""

from __future__ import annotations

from collections import defaultdict

from holon import cli

# The families, in the order a new agent reads them: what the organization IS, then what work
# flows through it, then how it stays healthy, then how the machine it runs on is managed.
FAMILY_ORDER = [
    ("getting started", "org", "hr", "emp", "intake", "whoami", "chair-channels", "drivers"),
    ("the work graph", "task", "plan", "claim", "submit", "run", "run-script", "verify", "dispatch",
     "post-execute", "audit"),
    ("a system (department)", "dept", "venture", "bylaws", "scope", "smoke"),
    ("knowledge", "kb", "memory", "recall", "law", "ad", "playbook"),
    ("skills", "skill"),
    ("recurring work and objectives", "schedule", "routine", "objective"),
    ("communication", "msg"),
    ("intactness", "lint", "doctor", "gc", "backup", "restore", "keys", "guard", "guards", "incident"),
    ("isolation: the trust boundary", "isolation", "broker", "mcp", "adapter"),
    ("the harness contract", "contract", "classify", "route"),
    ("the instance", "init", "status", "tick", "events", "charter", "metrics", "help"),
]

# Each family's one-line framing, so a reader knows why these commands are together.
FAMILY_BLURB = {
    "getting started": "what the organization is, who works in it, and who you are to it",
    "the work graph": "work flows through a fixed pipeline; these move it",
    "a system (department)": "a department is one holon: a mission, staff, reach and checks",
    "knowledge": "what the organization has learned, and the rules it operates from",
    "skills": "the standard skills library: list, read, tailor, extend and export",
    "recurring work and objectives": "work that repeats on a clock, and work that owns an outcome",
    "communication": "asking a colleague, and reaching another department",
    "intactness": "checks that watch the live instance, and the housekeeping they trigger",
    "isolation: the trust boundary": "the Chair's boundary between agents and credentials",
    "the harness contract": "how a harness proves it speaks the contract",
    "the instance": "the machine the organization runs on",
}


def by_family() -> list[tuple[str, list[dict]]]:
    """The registry grouped into families, every command exactly once, in FAMILY_ORDER."""
    manifest = cli.manifest()
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in manifest:
        groups[c["command"].split()[0]].append(c)
    out = []
    for family, *roots in FAMILY_ORDER:
        rows = [c for r in roots for c in sorted(groups.get(r, []), key=lambda c: c["command"])]
        if rows:
            out.append((family, rows))
    # a command family a future agent adds without extending FAMILY_ORDER is still listed,
    # under a family that says so, rather than vanishing from the reference
    known = {r for _, *rest in FAMILY_ORDER for r in rest}
    for root in sorted(set(groups) - known):
        out.append((f"unclassified: {root}", groups[root]))
    return out


def render() -> str:
    lines = [
        "# Commands",
        "",
        "Every command the distribution registers, generated from the registry (`holon help` prints",
        "the same table; regenerate with `python3 -m holon.tools.commands`). A command that exists and",
        "is not in this file fails `holon lint` — so this file can never drift from the code.",
        "",
        "**Actor classes** (law 8): every command declares who may call it.",
        "",
        "| Class | Who |",
        "|---|---|",
        "| `chair` | the Chair (a `--token`, or the Chair key on a trusted path) |",
        "| `employee` | an employee, with their own key or a run token |",
        "| `intake` | a department's intake key: it may file work into that department, nothing else |",
        "| `any` | anyone holding any credential, plus the kernel calling itself |",
        "| `kernel` | the heartbeat's own calls, never a person or an agent |",
        "",
    ]
    for family, rows in by_family():
        blurb = FAMILY_BLURB.get(family)
        lines.append(f"## {family}")
        if blurb:
            lines.append("")
            lines.append(f"*{blurb}.*")
        lines.append("")
        lines.append("| Command | Actor | What it does |")
        lines.append("|---|---|---|")
        for c in rows:
            opts = "".join(f" `{o}`" for o in c["options"])
            pos = "".join(f" <{p}>" for p in c["positionals"])
            name = f"`holon {c['command']}`{opts}{pos}"
            lines.append(f"| {name} | {c['actor']} | {c['help']} |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":  # python3 -m holon.tools.commands > docs/COMMANDS.md
    print(render(), end="")
