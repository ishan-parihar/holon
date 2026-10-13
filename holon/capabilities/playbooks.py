"""Playbooks: the protocol and rubric a job is done and judged by, as a department skill that improves.

A playbook is a skill of kind `playbook` in a department's layer (`$HOLON_HOME/skills/@<dept>/<name>/`):

    SKILL.md    frontmatter (version) + the protocol: objective, steps, lessons
    rubric.md   the criteria every result is judged on, one `- ` line each

Routines get one when they are approved (`routine-<slug>`); a task class gets one when the department
has enough verified results to write it down (`class-<task_class>`). Executors read it, the rubric judge
scores results against it, and refinement work (planned, approved, executed and verified like any
other) rewrites it from the evidence: judgments per criterion, verifier notes, gold standards. The
kernel bumps the version each time a verified change lands, and every judgment records the version it
was made against, so a refinement can be seen to help or not.
"""

from __future__ import annotations

import re

from holon.capabilities.skills import Library, parse_frontmatter

KIND = "playbook"


def slug(text: str, prefix: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (prefix + s)[:63].rstrip("-")


def for_routine(title: str) -> str:
    return slug(title, "routine-")


def for_class(task_class: str) -> str:
    return slug(task_class, "class-")


def parse_rubric(text: str) -> list[str]:
    return [line[2:].strip() for line in text.splitlines() if line.startswith("- ") and line[2:].strip()]


def read(lib: Library, dept: str | None, name: str) -> dict | None:
    """{"name", "version", "protocol", "rubric": [criteria], "layer"} or None if the department has none."""
    if not dept:
        return None
    try:
        layer, path = lib.resolve(name, "SKILL.md", dept)
    except LookupError:
        return None
    meta, body = parse_frontmatter(path.read_text())
    if meta.get("kind") != KIND:
        return None
    try:
        rubric = parse_rubric(lib.read(name, "rubric.md", dept))
    except LookupError:
        rubric = []
    return {"name": name, "version": int(meta.get("version") or 1), "protocol": body.strip(), "rubric": rubric,
            "layer": layer}


def render(name: str, title: str, objective: str, steps: list[str] | str, rubric: list[str], version: int = 1,
           lessons: str = "") -> dict[str, str]:
    """The files of a playbook (SKILL.md, rubric.md)."""
    steps = [steps] if isinstance(steps, str) else steps
    skill = (f"---\nname: {name}\ndescription: The playbook for \"{title}\": how it is done and judged. Use when "
             f"doing, verifying or refining it.\nkind: {KIND}\nroles: [executor, verifier, planner, steward]\n"
             f"version: {version}\n---\n\n# {title}\n\n## Objective\n\n{objective.strip()}\n\n## Protocol\n\n"
             + "\n".join(f"{i}. {s.strip()}" for i, s in enumerate(steps, 1))
             + "\n\n## What good looks like\n\nEvery result is judged on the criteria in `rubric.md`; the gold "
               "standard is the last result a verifier passed. Report what you did and what you found in a way "
               "the judge can check against each criterion.\n\n## Lessons\n\n"
             + (lessons.strip() or "None yet: refinement adds what the evidence teaches.") + "\n")
    rub = (f"# Rubric: {title}\n\nEach result is judged on every criterion. Keep each one checkable from the result "
           "itself; refinement sharpens, adds or retires criteria as the job is better understood.\n\n"
           + "\n".join(f"- {c.strip()}" for c in rubric) + "\n")
    return {"SKILL.md": skill, "rubric.md": rub}


def write(lib: Library, dept: str, name: str, files: dict[str, str]) -> None:
    root = lib.instance / f"@{dept}" / name
    root.mkdir(parents=True, exist_ok=True)
    for f, text in files.items():
        (root / f).write_text(text)


def bump(lib: Library, dept: str, name: str, old: int) -> int:
    """After a verified change: the version is the kernel's, whatever the change wrote."""
    p = lib.instance / f"@{dept}" / name / "SKILL.md"
    text = p.read_text()
    new = old + 1
    if re.search(r"^version:.*$", text, flags=re.M):
        text = re.sub(r"^version:.*$", f"version: {new}", text, count=1, flags=re.M)
    else:
        text = text.replace("\n---", f"\nversion: {new}\n---", 1)
    p.write_text(text)
    return new
