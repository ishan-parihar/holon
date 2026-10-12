"""The skills library: how employees know how to do their jobs.

A skill is a directory with a SKILL.md (frontmatter + protocol) and optional files it points
to (references/*.md, briefs/<duty>.md). Three layers, first match wins per file:

    $HOLON_HOME/skills/@<dept>/<name>/…   tailored for one department
    $HOLON_HOME/skills/<name>/…           tailored for this organization
    holon/skills/<name>/…                 the standard library shipped with Holon

so a steward can tailor one file of one skill for one department without forking the rest.
Agents read skills through `holon skill show|invoke` (any harness) or from an exported copy
(`holon skill export`, for harnesses with native skill loading). Both resolve the same layers.
"""

from __future__ import annotations

import re
import shutil
from importlib import resources
from pathlib import Path
from string import Template

from holon.core.paths import Home

KINDS = ("protocol", "role", "operation")
REQUIRED = ("name", "description", "kind")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
LINK_RE = re.compile(r"`((?:references|briefs)/[\w./-]+\.md)`")


class SkillError(LookupError):
    pass


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """`key: value` lines between --- fences; `[a, b]` values become lists."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" not in line or line.startswith((" ", "#")):
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            meta[k.strip()] = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
        else:
            meta[k.strip()] = v.strip("\"'")
    return meta, text[end + 4:].lstrip("\n")


class Library:
    def __init__(self, home: Home | None):
        self.home = home
        self.stdlib = Path(str(resources.files("holon").joinpath("skills")))

    @property
    def instance(self) -> Path | None:
        return self.home.root / "skills" if self.home else None

    def layers(self, dept: str | None = None) -> list[tuple[str, Path]]:
        out = []
        if self.instance:
            if dept:
                out.append((f"dept:{dept}", self.instance / f"@{dept}"))
            out.append(("instance", self.instance))
        out.append(("stdlib", self.stdlib))
        return out

    def names(self, dept: str | None = None) -> list[str]:
        found = set()
        for _, root in self.layers(dept):
            if root.is_dir():
                found |= {p.parent.name for p in root.glob("*/SKILL.md") if not p.parent.name.startswith("@")}
        return sorted(found)

    def resolve(self, name: str, file: str = "SKILL.md", dept: str | None = None) -> tuple[str, Path]:
        if ".." in Path(file).parts or Path(file).is_absolute():
            raise SkillError(f"invalid skill file {file!r}")
        for layer, root in self.layers(dept):
            p = root / name / file
            if p.is_file():
                return layer, p
        raise SkillError(f"no skill file {name}/{file}" + (f" (dept {dept})" if dept else ""))

    def read(self, name: str, file: str = "SKILL.md", dept: str | None = None) -> str:
        return self.resolve(name, file, dept)[1].read_text()

    def meta(self, name: str, dept: str | None = None) -> dict:
        layer, path = self.resolve(name, "SKILL.md", dept)
        m, _ = parse_frontmatter(path.read_text())
        files = sorted({str(p.relative_to(r / name)) for _, r in self.layers(dept) if (r / name).is_dir()
                        for p in (r / name).rglob("*.md") if p.name != "SKILL.md"})
        return {**m, "source": layer, "path": str(path), "files": files}

    def catalog(self, dept: str | None = None, role: str | None = None) -> list[dict]:
        out = [self.meta(n, dept) for n in self.names(dept)]
        return [m for m in out if not role or role in (m.get("roles") or [])]

    @staticmethod
    def render(text: str, variables: dict) -> str:
        return Template(text).safe_substitute({k: v for k, v in variables.items() if v is not None})

    # ── authoring ─────────────────────────────────────────────────────────
    def fork(self, name: str, dept: str | None = None, file: str | None = None) -> list[Path]:
        """Copy the resolved skill (or one file of it) into the instance or department layer for tailoring."""
        if not self.instance:
            raise SkillError("forking needs an instance (HOLON_HOME)")
        target = self.instance / (f"@{dept}" if dept else "") / name
        files = [file] if file else ["SKILL.md"] + self.meta(name, dept)["files"]
        written = []
        for f in files:
            dst = target / f
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            # tailor from the next layer down, never from the layer being written
            src_layers = [p for _, p in self.layers(dept) if p != target.parent]
            for root in src_layers:
                if (root / name / f).is_file():
                    shutil.copy2(root / name / f, dst)
                    written.append(dst)
                    break
        return written

    def new(self, name: str, kind: str, description: str, roles: list[str] | None = None,
            dept: str | None = None) -> Path:
        if not NAME_RE.match(name):
            raise SkillError("skill names are lowercase words joined by hyphens")
        if kind not in KINDS:
            raise SkillError(f"kind must be one of {KINDS}")
        if not self.instance:
            raise SkillError("creating a skill needs an instance (HOLON_HOME)")
        if name in self.names(dept):
            raise SkillError(f"skill {name} exists; use `holon skill fork {name}` to tailor it")
        d = self.instance / (f"@{dept}" if dept else "") / name
        (d / "references").mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\nkind: {kind}\nroles: [{', '.join(roles or [])}]\n"
            f"version: 1\n---\n\n# {name}\n\n## When to use\n\n## Protocol\n\n1. \n\n## Output\n\n"
            "## References\n\n- `references/` (add files and link them here)\n")
        return d / "SKILL.md"

    def check(self, dept: str | None = None, roles: list[str] | None = None) -> list[str]:
        problems = []
        for name in self.names(dept):
            m = self.meta(name, dept)
            where = f"{name} ({m['source']})"
            for k in REQUIRED:
                if not m.get(k):
                    problems.append(f"{where}: frontmatter is missing '{k}'")
            if m.get("name") and m["name"] != name:
                problems.append(f"{where}: frontmatter name {m['name']!r} does not match its directory")
            if m.get("kind") and m["kind"] not in KINDS:
                problems.append(f"{where}: kind must be one of {KINDS}")
            if roles is not None:
                bad = set(m.get("roles") or []) - set(roles)
                if bad:
                    problems.append(f"{where}: unknown role(s) {sorted(bad)}")
            for f in ["SKILL.md"] + m["files"]:
                for link in LINK_RE.findall(self.read(name, f, dept)):
                    try:
                        self.resolve(name, link, dept)
                    except SkillError:
                        problems.append(f"{where}: {f} links to missing {link}")
        return problems

    def export(self, dest: Path, variables: dict, names: list[str] | None = None, dept: str | None = None) -> list[Path]:
        """Materialize resolved, rendered skills for a harness that loads skills from disk."""
        out = []
        for name in names or self.names(dept):
            m = self.meta(name, dept)
            for f in ["SKILL.md"] + m["files"]:
                p = dest / name / f
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(self.render(self.read(name, f, dept), variables))
            out.append(dest / name)
        return out
