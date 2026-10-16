"""`holon lint`: rubric and consistency checks over Holon's artifacts (not its live state; see doctor.py).

Six packs. Each finding is {severity: error | warning, rule, where, message}; errors fail CI.

  SCHEMA    every artifact matches its schema: published JSON Schemas = the contract, skill frontmatter,
            genome profiles (and each department's copy), drivers, holon.toml, migration checksums,
            SQL CHECK lists = holon.core.vocab
  REF       every reference resolves: `holon …` commands and options in skills/docs exist in the command
            registry, skill and markdown links, brief placeholders, documented environment variables
  BIND      every org word is a mechanism: each role has stages, skills and briefs; mechanism columns in
            INTENT/GENOME name real commands or code; every config key is documented
  DEPTH     skills and docs meet the rubric: required sections per kind, word bounds, a "Use when"
            trigger, runnable commands, limits for roles that act
  DRY       nothing is said twice: near-duplicate paragraphs across skills/briefs/docs, and copies of
            vocabulary lists in code
  AGNOSTIC  stdlib-only imports; no harness named anywhere in the kernel distribution
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import stat
import sys
import tomllib
from pathlib import Path

from holon.core import contract, vocab
from holon.core.db import checksum, migration_files

PACKS = ("schema", "ref", "bind", "depth", "dry", "agnostic", "api", "genome")
PKG = Path(__file__).resolve().parents[1]
DIST = PKG.parent          # the source checkout (docs/, schemas/, README.md) when present
DOC_FILES = ("README.md", "AGENTS.md")
SUBSTITUTED = {"run_id", "task_id", "work_id", "stage", "context_md", "result_path", "workdir", "holon"}
SKILL_SECTIONS = {"protocol": ("## Protocol", "## Always"), "role": ("## Principles",),
                  "operation": ("## Protocol",), "playbook": ("## Objective", "## Protocol")}
SKILL_WORDS = (120, 700)
DOC_WORDS_MIN = 150
SIMILAR_WARN, SIMILAR_ERROR = 0.6, 0.85


class Findings(list):
    def add(self, severity: str, rule: str, where: str, message: str) -> None:
        self.append({"severity": severity, "rule": rule, "where": where, "message": message})


def _rel(p: Path) -> str:
    for base in (DIST, PKG):
        try:
            return str(p.relative_to(base))
        except ValueError:
            pass
    return str(p)


def _docs() -> list[Path]:
    out = [DIST / f for f in DOC_FILES if (DIST / f).exists()]
    if (DIST / "docs").is_dir():
        out += sorted((DIST / "docs").glob("*.md"))
    return out


def _skill_files(k) -> list[tuple[str, str, Path]]:
    """(skill, file, path) for every resolved file of every skill, stdlib and instance layers."""
    out = []
    for name in k.skills.names():
        m = k.skills.meta(name)
        for f in ["SKILL.md"] + m["files"]:
            out.append((name, f, k.skills.resolve(name, f)[1]))
    return out


def _strip_code(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


# ── SCHEMA ────────────────────────────────────────────────────────────────
def pack_schema(k, f: Findings) -> None:
    schemas = DIST / "schemas"
    if schemas.is_dir():
        published = {p.name for p in schemas.glob("*.schema.json")}
        for stage in vocab.STAGES:
            name = f"{stage}-result.schema.json"
            p = schemas / name
            if not p.exists():
                f.add("error", "schema.contract", _rel(p), "missing; run `holon contract schemas --write schemas`")
            elif p.read_text() != contract.schema_text(stage):
                f.add("error", "schema.contract", _rel(p), "drifted from holon/core/contract.py; regenerate it")
            published.discard(name)
        for extra in sorted(published):
            f.add("error", "schema.contract", f"schemas/{extra}", "no stage has this schema; delete it")
    for p in k.skills.check(None, list(vocab.ROLES)):
        f.add("error", "schema.skill", p.split(":")[0], p)
    from holon.execution import genome
    for name, path in genome.profile_paths(k.home).items():
        try:
            raw = tomllib.loads(path.read_text())
            g = genome.load_profile(name, k.home)
            if "version" not in raw:
                f.add("error", "schema.genome", _rel(Path(str(path))), "profile has no `version`")
        except (genome.GenomeError, tomllib.TOMLDecodeError) as e:
            f.add("error", "schema.genome", _rel(Path(str(path))), str(e))
    for d in k.org.depts():
        if not d.get("genome"):
            f.add("error", "schema.genome", f"dept:{d['slug']}", "department has no genome profile "
                  "(`holon dept profile <slug> light|standard --reason …`)")
            continue
        snap = json.loads(d["genome"])
        try:
            current = genome.load_profile(snap["name"], k.home)
            if current.get("version", 1) > snap.get("version", 1):
                f.add("warning", "schema.genome", f"dept:{d['slug']}",
                      f"follows {snap['name']} v{snap.get('version', 1)}; v{current['version']} exists "
                      "(re-apply with `holon dept profile`)")
        except genome.GenomeError:
            f.add("warning", "schema.genome", f"dept:{d['slug']}", f"its profile {snap['name']!r} no longer exists")
    from holon.execution import drivers
    try:
        drivers.available(k.home)
    except drivers.DriverError as e:
        f.add("error", "schema.driver", "drivers", str(e))
    from holon.execution import autonomy
    from holon.execution.service import CONFIG_KEYS
    try:
        autonomy.policy(k.config)
    except ValueError as e:
        f.add("error", "schema.config", "holon.toml [autonomy]", str(e))
    if k.home.config_file.exists():
        raw = tomllib.loads(k.home.config_file.read_text())
        known = {*CONFIG_KEYS, "grading", "autonomy", "pipeline", "authority"}
        for section, values in raw.items():
            if section not in known:
                f.add("warning", "schema.config", "holon.toml", f"unknown section [{section}]")
            elif section == "classes" and isinstance(values, dict):
                for slug, classes in values.items():
                    if slug != "org":
                        try:
                            k.org.dept(slug)
                        except LookupError:
                            f.add("warning", "schema.config", "holon.toml", f"[classes] {slug}: no such department")
                    if not isinstance(classes, list) or not all(isinstance(c, str) and c for c in classes):
                        f.add("error", "schema.config", "holon.toml", f"[classes] {slug}: must be a list of names")
            elif section in CONFIG_KEYS and isinstance(values, dict):
                if section in ("budget", "scope", "secrets"):
                    for slug, sub in (values.get("dept") or {}).items():
                        try:
                            k.org.dept(slug)
                        except LookupError:
                            f.add("warning", "schema.config", "holon.toml", f"[{section}.dept.{slug}]: no such department")
                        if section != "secrets":
                            for key in set(sub) - (CONFIG_KEYS[section] - {"dept"}):
                                f.add("warning", "schema.config", "holon.toml", f"unknown key [{section}.dept.{slug}] {key}")
                if section == "secrets":   # G8: the value is a 0600 key-file path, never the secret itself
                    for slug, sub in (values.get("dept") or {}).items():
                        for var, path in sorted((sub or {}).items()):
                            if not isinstance(path, str) or not path.startswith(("/", "~")) or "://" in path:
                                f.add("error", "secrets.plaintext", f"[secrets.dept.{slug}] {var}",
                                      "the value must be the path to a 0600 key file — a secret in config is "
                                      "readable by every process running as this user")
                                continue
                            p = Path(path).expanduser()
                            if p.exists() and stat.S_IMODE(p.stat().st_mode) & 0o077:
                                f.add("error", "secrets.plaintext", f"[secrets.dept.{slug}] {var}",
                                      f"{p} is readable by others (chmod 600)")
                if section == "scope":
                    for where, sub in [("scope", values), *((f"scope.dept.{s}", v) for s, v in (values.get("dept") or {}).items())]:
                        for r in sub.get("roots", []):
                            if not (r.startswith("/") or r.startswith("~")):
                                f.add("error", "schema.config", "holon.toml", f"[{where}] roots: {r!r} must be absolute")
                for key in set(values) - CONFIG_KEYS[section]:
                    f.add("warning", "schema.config", "holon.toml", f"unknown key [{section}] {key}")
            elif section == "authority":
                f.add("warning", "schema.config", "holon.toml", "[authority] is legacy: move its keys to [autonomy]")
    for db, conn in (("work", k.wconn), ("memory", k.mconn)):
        sums = {r[0]: r[1] for r in conn.execute("SELECT name, checksum FROM schema_migrations")}
        for mf in migration_files(db):
            if mf.name in sums:
                if sums[mf.name] is None:
                    f.add("warning", "schema.migration", f"{db}/{mf.name}", "applied before checksums were recorded")
                elif sums[mf.name] != checksum(mf.read_text()):
                    f.add("error", "schema.migration", f"{db}/{mf.name}", "edited after it was applied")
    checks = [(k.wconn, "tasks", "stage", vocab.STAGES), (k.wconn, "tasks", "source", vocab.SOURCES),
              (k.wconn, "tasks", "risk", vocab.RISKS), (k.wconn, "tasks", "lane", vocab.LANES), (k.wconn, "messages", "kind", vocab.MESSAGE_KINDS),
              (k.wconn, "schedules", "kind", vocab.SCHEDULE_KINDS), (k.wconn, "runs", "status", vocab.RUN_STATUSES),
              (k.wconn, "employees", "status", vocab.EMPLOYEE_STATUSES),
              (k.mconn, "items", "kind", vocab.MEMORY_KINDS), (k.mconn, "items", "confidence", vocab.CONFIDENCE)]
    for conn, table, col, values in checks:
        sql = (conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone() or [""])[0]
        m = re.search(rf"\b{col}\b[^,]*?\bIN\s*\(([^)]*)\)", sql, flags=re.S | re.I)
        if not m:
            f.add("warning", "schema.vocab", f"{table}.{col}", "no CHECK constraint lists its values")
            continue
        found = set(re.findall(r"'([^']*)'", m.group(1)))
        if found != set(values):
            f.add("error", "schema.vocab", f"{table}.{col}",
                  f"CHECK lists {sorted(found)}; holon.core.vocab says {sorted(values)}")


# ── REF ───────────────────────────────────────────────────────────────────
CMD_RE = re.compile(r"(\$holon|`holon|\bholon|bin/holon|\$HOLON)\s+((?:[a-z][a-z-]*)(?:\s+[a-z][a-z-]*)?)([^`\n|]*)")


def _cli_refs(text: str, where: str, f: Findings) -> None:
    from holon.cli import REGISTRY
    cmds = {c.path: c for c in REGISTRY}
    groups = {c.path[0] for c in REGISTRY}
    for m in CMD_RE.finditer(text):
        explicit = m.group(1) != "holon"  # `$holon x`, `holon x` in code, bin/holon x: certainly a command
        words = m.group(2).split()
        if not words:
            continue
        if words[0] not in groups:
            if explicit:
                f.add("error", "ref.cli", where, f"`holon {words[0]}` is not a command")
            continue
        key = tuple(words[:2]) if tuple(words[:2]) in cmds else (words[0],)
        if key not in cmds:
            f.add("error", "ref.cli", where, f"`holon {' '.join(words)}` is not a command")
            continue
        opts = {o for a, _ in cmds[key].args for o in a if o.startswith("-")} | {"--json", "--home", "--token"}
        for o in re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", m.group(3)):
            if o not in opts:
                f.add("error", "ref.cli", where, f"`holon {' '.join(key)}` has no option {o}")


def pack_ref(k, f: Findings) -> None:
    for name, file, path in _skill_files(k):
        text = path.read_text()
        _cli_refs(text, f"skill {name}/{file}", f)
        if file.startswith("briefs/"):
            used = set(re.findall(r"\$([a-z_]+)", text))
            for bad in sorted(used - SUBSTITUTED):
                f.add("error", "ref.placeholder", f"skill {name}/{file}", f"${bad} is never substituted")
            if "result_path" not in used:
                f.add("error", "ref.placeholder", f"skill {name}/{file}", "a brief must say where to write ($result_path)")
    for doc in _docs():
        text = doc.read_text()
        _cli_refs(text, _rel(doc), f)
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
            if "://" in target or target.startswith("mailto:"):
                continue
            if not (doc.parent / target).exists():
                f.add("error", "ref.link", _rel(doc), f"link to missing {target}")
    _guards(f)
    _refs(k, f)
    contract_doc = DIST / "docs" / "CONTRACT.md"
    if contract_doc.exists():
        from holon.execution import drivers
        brief = {"stage": "plan", "run_id": "r", "task_id": "t", "brief_md": "/x/BRIEF.md", "result_path": "/x/r"}
        expected = set(drivers.run_env(k.home, brief, "tok", broker="@b"))  # every variable, isolation included
        documented = set(re.findall(r"\b(HOLON_[A-Z_]+|HOLON|PYTHONPATH)\b", contract_doc.read_text()))
        for var in sorted(expected - documented):
            f.add("error", "ref.env", "docs/CONTRACT.md", f"the driver sets {var} but the contract does not document it")
        for var in sorted(documented - expected - {"HOLON_KEY", "HOLON_CHAIR_KEY", "HOLON_CHAIR_KEY_FILE"}):
            f.add("error", "ref.env", "docs/CONTRACT.md", f"documents {var}, which nothing sets or reads")


SHELL_WORDS = {"cd", "test", "[", "echo", "true", "false", "export", "exit", "set", "if", "then", "else", "fi",
               "for", "do", "done", "while", "!", "{", "}", "(", ")", "printf", "read", "source", ".", "eval", "exec",
               "command", "time", "env"}


def command_refs(cmd: str) -> tuple[list[str], list[str]]:
    """(executables, absolute paths) a shell command names: the first word of each simple command (after
    VAR=value assignments), and every /… or ~/… word that is not a redirect target or an option."""
    import shlex
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        words = list(lex)
    except ValueError:
        return [], []
    exes, paths, start, redirect = [], [], True, False
    for w in words:
        if w and set(w) <= set("&|;()<>"):
            start, redirect = w in ("&&", "||", ";", "|", "(", ")"), w in (">", ">>", "<", "&>", "2>")
            continue
        if start and "=" in w and not w.startswith(("/", "~")):
            continue
        if start:
            if w not in SHELL_WORDS:
                exes.append(w)
            start = False
        elif w.startswith(("/", "~/")) and not redirect and "$" not in w and "*" not in w:
            paths.append(w)
        redirect = False
    return exes, paths


def _commands(k) -> list[tuple[str, str]]:
    """Every command the instance will run, with where it is declared."""
    from holon.execution import drivers
    c = k.config
    out = [("[checks]", x) for x in (c.get("checks") or {}).get("commands", [])]
    for kind in ("dept", "class"):
        for name, sub in ((c.get("checks") or {}).get(kind) or {}).items():
            out += [(f"[checks.{kind}.{name}]", x) for x in sub.get("commands", [])]
    out += [(f"smoke {s['dept'] or 'org'}: {s['name']}", s["cmd"]) for s in k.smoke_checks()]
    for r in k.routines():
        if r["command"]:
            out.append((f"routine {r['title']}", r["command"]))
    for d in drivers.available(k.home).values():
        if not d.source.startswith("builtin:"):
            out.append((f"driver {d.name}", d.command.split("{")[0]))
    return out


def _refs(k, f: Findings) -> None:
    """Commands that name an executable or a path that does not exist fail when they run, often at 3am on a
    schedule; a linter has to catch these before they ever run."""
    import shutil
    for where, cmd in _commands(k):
        exes, paths = command_refs(cmd)
        for e in exes:
            found = Path(e).expanduser().exists() if "/" in e else shutil.which(e)
            if not found:
                f.add("error", "ref.command", where, f"`{e}` is not an executable on this machine ({cmd[:120]})")
        for p in paths:
            if not Path(p).expanduser().exists():
                f.add("warning", "ref.path", where, f"{p} does not exist ({cmd[:120]})")
    from holon.capabilities import playbooks
    for r in k.routines():
        src = k.work.get(k.work.get(r["plan"])["parent_id"])
        name = (k.work.output(r["plan"]) or {"body": {}})["body"].get("playbook")
        if r["enabled"] and name and not playbooks.read(k.skills, k._dept_slug(src["dept_id"]), name):
            f.add("error", "ref.playbook", f"routine {r['title']}", f"its playbook {name} is missing from its "
                  "department's skills: its runs have no protocol and the judge no rubric")
    smoked = {s["dept"] for s in k.smoke_checks()}
    scoped = set((k.config.get("scope", {}).get("dept") or {})) | set(((k.config.get("checks") or {}).get("dept") or {}))
    scripted = {k._dept_slug(k.work.get(k.work.get(r["plan"])["parent_id"])["dept_id"]) for r in k.routines()
                if r["command"]}
    for slug in sorted((scoped | scripted) - smoked - {None}):
        f.add("warning", "ref.smoke", f"dept:{slug}", "runs scripts or has its own reach but declares no smoke checks "
              "([[smoke.dept.<slug>.checks]]): nothing verifies its infrastructure before its agents run")


def _guards(f: Findings) -> None:
    """The regression ledger (AGENTS.md): every defect a real run found names the test that guards it,
    and every guard named anywhere in the docs still exists. Deleting a guard fails lint."""
    tests = DIST / "tests"
    if not tests.is_dir():  # an installed package without its tests: nothing to check against
        return
    defined = set(re.findall(r"def (test_\w+)", "\n".join(p.read_text() for p in tests.glob("test_*.py"))))
    for doc in _docs():
        for name in sorted(set(re.findall(r"`(test_\w+)`", doc.read_text())) - defined):
            f.add("error", "ref.guard", _rel(doc), f"names guard test {name}, which does not exist")
    agents = DIST / "AGENTS.md"
    if agents.exists():
        text = agents.read_text()
        start = text.find("## Regression ledger")
        if start < 0:
            f.add("error", "ref.guard", "AGENTS.md", "the regression ledger section is missing")
            return
        section = text[start:text.find("\n## ", start + 1) if "\n## " in text[start + 1:] else len(text)]
        rows = [r for r in section.splitlines() if r.startswith("|") and not set(r) <= set("|-: ")][1:]
        for r in rows:
            if not re.search(r"`test_\w+`", r):
                f.add("error", "ref.guard", "AGENTS.md", f"ledger row has no guard test: {r[:80]}")


# ── BIND ──────────────────────────────────────────────────────────────────
def _resolves(symbol: str) -> bool:
    parts = symbol.split(".")
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        for attr in parts[i:]:
            if not hasattr(obj, attr):
                return False
            obj = getattr(obj, attr)
        return True
    return False


def pack_bind(k, f: Findings) -> None:
    from holon.execution.briefs import BRIEF_OF_STAGE
    for role in vocab.ROLES:
        if not vocab.ROLE_STAGES[role]:
            f.add("error", "bind.role", role, "role performs no stage")
        if f"holon-{role}" not in k.skills.names():
            f.add("error", "bind.role", role, f"no skill holon-{role}")
    for stage, (skill, file) in BRIEF_OF_STAGE.items():
        try:
            k.skills.resolve(skill, file)
        except LookupError:
            f.add("error", "bind.stage", stage, f"no brief {skill}/{file}")
    from holon.cli import REGISTRY
    cmds = {" ".join(c.path) for c in REGISTRY}
    for doc in _docs():
        lines = doc.read_text().splitlines()
        col = None
        for line in lines:
            if not line.startswith("|"):
                col = None
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if col is None:
                col = next((i for i, c in enumerate(cells) if c.lower() == "mechanism"), -1)
                continue
            if col < 0 or set(line) <= set("|-: ") or col >= len(cells):
                continue
            cell = cells[col]
            refs = re.findall(r"`([^`]+)`", cell)
            if not refs and "planned" not in cell.lower():
                f.add("error", "bind.term", _rel(doc), f"'{cells[0]}' names no mechanism (code, command, or *planned*)")
            for r in refs:
                if r.startswith("holon "):
                    if not any(r[6:].startswith(c) for c in cmds):
                        f.add("error", "bind.term", _rel(doc), f"'{cells[0]}': `{r}` is not a command")
                elif re.fullmatch(r"[a-z_]+(\.[A-Za-z_]+)+", r) and not _resolves("holon." + r):
                    f.add("error", "bind.term", _rel(doc), f"'{cells[0]}': `{r}` does not exist in holon")
    from holon.execution.service import DEFAULT_CONFIG
    docs_text = "\n".join(p.read_text() for p in _docs())
    if docs_text:
        for section, keys in DEFAULT_CONFIG.items():
            for key in keys:
                if key not in docs_text:
                    f.add("error", "bind.config", f"[{section}] {key}", "config key is not documented")


# ── DEPTH ─────────────────────────────────────────────────────────────────
def _words(text: str) -> int:
    return len(re.findall(r"[A-Za-z][\w'-]*", _strip_code(re.sub(r"^---.*?---", "", text, flags=re.S))))


def pack_depth(k, f: Findings) -> None:
    for name in k.skills.names():
        m = k.skills.meta(name)
        text = Path(m["path"]).read_text()
        where = f"skill {name}"
        if "use when" not in (m.get("description") or "").lower():
            f.add("error", "depth.trigger", where, "description must say when to use it ('Use when …')")
        for section in SKILL_SECTIONS.get(m.get("kind"), ()):
            if section not in text:
                f.add("error", "depth.section", where, f"a {m.get('kind')} skill needs '{section}'")
        n = _words(text)
        if not SKILL_WORDS[0] <= n <= SKILL_WORDS[1]:
            f.add("error" if n < SKILL_WORDS[0] else "warning", "depth.size", where,
                  f"{n} words (rubric: {SKILL_WORDS[0]}–{SKILL_WORDS[1]})")
        bodies = text + "".join(k.skills.read(name, x) for x in m["files"])
        if not re.search(r"\$holon\s+[a-z]", bodies):
            f.add("error", "depth.runnable", where, "names no runnable `$holon …` command")
        acts = any(vocab.POSTURE[s] == "act" for r in m.get("roles") or [] if m.get("kind") == "role"
                   for s in vocab.ROLE_STAGES.get(r, ()))
        if acts and "never" not in bodies.lower() and "deviate only" not in bodies.lower():
            f.add("error", "depth.limits", where, "a role that changes the world must state its limits")
        for file in m["files"]:
            if file.startswith("briefs/") and "## Output" not in k.skills.read(name, file):
                f.add("error", "depth.section", f"{where}/{file}", "a brief needs '## Output'")
    for doc in _docs():
        text = doc.read_text()
        if not text.startswith("# "):
            f.add("error", "depth.doc", _rel(doc), "starts without a '# Title'")
        first = next((p for p in text.split("\n\n")[1:] if p.strip() and not p.startswith(("#", "|", "```", "-"))),
                     "")
        if len(first.split()) < 10:
            f.add("error", "depth.doc", _rel(doc), "needs a purpose paragraph after the title")
        if _words(text) < DOC_WORDS_MIN:
            f.add("error", "depth.doc", _rel(doc), f"under {DOC_WORDS_MIN} words")


# ── DRY ───────────────────────────────────────────────────────────────────
def _paragraphs(text: str) -> list[str]:
    out = []
    for p in _strip_code(re.sub(r"^---.*?---", "", text, flags=re.S)).split("\n\n"):
        p = p.strip()
        if not p or p.startswith(("#", "|")) or len(p.split()) < 25:
            continue
        out.append(p)
    return out


def _shingles(text: str, n: int = 5) -> set:
    w = re.findall(r"[a-z0-9]+", text.lower())
    return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def pack_dry(k, f: Findings) -> None:
    corpus = []
    for name, file, path in _skill_files(k):
        corpus += [(f"skill {name}/{file}", p) for p in _paragraphs(path.read_text())]
    for doc in _docs():
        corpus += [(_rel(doc), p) for p in _paragraphs(doc.read_text())]
    sh = [(w, p, _shingles(p)) for w, p in corpus]
    for i in range(len(sh)):
        for j in range(i + 1, len(sh)):
            a, b = sh[i][2], sh[j][2]
            if not a or not b:
                continue
            sim = len(a & b) / len(a | b)
            if sim >= SIMILAR_WARN:
                f.add("error" if sim >= SIMILAR_ERROR else "warning", "dry.paragraph", sh[i][0],
                      f"{sim:.0%} the same as a paragraph in {sh[j][0]}: \"{sh[i][1][:70]}…\"")
    enums = {name: set(v) for name, v in vars(vocab).items()
             if isinstance(v, tuple) and len(v) >= 3 and all(isinstance(x, str) for x in v)}
    for py in PKG.rglob("*.py"):
        if py.name == "vocab.py" or "migrations" in py.parts:
            continue
        for node in ast.walk(ast.parse(py.read_text())):
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and len(node.elts) >= 3 and \
                    all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
                values = {e.value for e in node.elts}
                for name, enum in enums.items():
                    if values == enum:
                        f.add("error", "dry.vocab", f"{_rel(py)}:{node.lineno}", f"a copy of holon.core.vocab.{name}")


# ── AGNOSTIC ──────────────────────────────────────────────────────────────
def pack_agnostic(k, f: Findings) -> None:
    stdlib = set(sys.stdlib_module_names)
    for py in PKG.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                if n.split(".")[0] not in stdlib | {"holon"}:
                    f.add("error", "agnostic.import", _rel(py), f"imports {n} (stdlib only)")
    words = harness_names()
    for p in [*PKG.rglob("*.py"), *PKG.rglob("*.md"), *PKG.rglob("*.toml")]:
        text = p.read_text().lower()
        for word in words:
            if re.search(rf"\b{word}\b", text):
                f.add("error", "agnostic.harness", _rel(p), f"names a harness ({word})")


def harness_names() -> set[str]:
    """The harnesses Holon has adapters for (adapters/<name>/driver.toml): none may be named in the kernel."""
    names = set()
    for d in (DIST / "adapters").glob("*/driver.toml"):
        name = d.parent.name.lower()
        names |= {name, name.split("-")[0]}
    return names


def pack_genome(k, f: Findings) -> None:
    """The genome layer (U1): unit manifests parse, every consumed service is offered by some unit
    (bind.service), every pooled unit names a real department (bind.pool), and the sync is not
    stale. The manifests are the source; the tables are a view of them."""
    from holon.genome import units as units_mod
    for p in units_mod.validate(k):
        f.add("error", "genome.manifest", "genome/units", p)


def pack_api(k, f: Findings) -> None:
    """The read API (boundary doc 4.1, H2): every api_* view exists on a migrated database
    and api_version matches the views this Holon ships. A view changes only with a version
    bump; read-API consumers refuse a version they do not know."""
    expected = {
        "api_employees": ("id", "name", "title", "department", "manager_id", "manager", "status", "driver", "roles"),
        "api_departments": ("id", "slug", "name", "mission", "head_id", "head", "status", "profile"),
        "api_board": ("id", "title", "stage", "status", "lane", "priority", "risk", "department",
                      "assignee", "approver", "grade", "run_id", "lease_until", "employee_id", "started_at"),
        "api_approvals": ("id", "title", "department", "grade", "approver", "risk", "plan", "created_at"),
        "api_messages": ("id", "ts", "sender", "to_ref", "kind", "task_id", "body"),
        "api_triggers": ("id", "trigger_kind", "pattern", "department", "employee", "enabled",
                         "failure_streak", "next_run_at"),
    }
    row = k.wconn.execute("SELECT value FROM api_meta WHERE key='api_version'").fetchone()
    if row is None:
        f.add("error", "api.version", "work.db", "no api_version row; the read-API migration did not run")
        return
    if row[0] not in ("1", "2", "3"):
        f.add("error", "api.version", "work.db", f"api_version {row[0]!r} is not one this Holon knows (1, 2, 3)")
    if row[0] == "3":
        expected = {**expected,
                    "api_units": ("slug", "charter", "category", "tier", "synced_at"),
                    "api_services": ("unit", "service", "command", "accepts", "returns")}
        # api_memory lives on memory.db (a view cannot reach across attached databases)
        have = tuple(r[1] for r in k.mconn.execute("PRAGMA table_info(api_memory)"))
        if have != ("id", "scope", "kind", "content", "provenance", "confidence", "pinned", "valid_from"):
            f.add("error", "api.views", "memory.db",
                  f"api_memory columns {have} do not match api_version 3; a view changes only with a version bump")
    present = {r[0] for r in k.wconn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    for name, cols in expected.items():
        if name not in present:
            f.add("error", "api.views", "work.db", f"{name} missing; the read-API migration did not run")
            continue
        have = tuple(r[1] for r in k.wconn.execute(f"PRAGMA table_info({name})"))
        if have != cols:
            f.add("error", "api.views", "work.db",
                  f"{name} columns {have} do not match api_version {row[0]} {cols}; a view changes only with a version bump")
    for extra in sorted(n for n in present if n.startswith("api_") and n not in expected):
        f.add("warning", "api.views", "work.db", f"{extra} is not part of api_version {row[0]}; rename or bump")


def run(k, packs: list[str] | None = None) -> list[dict]:
    f = Findings()
    for pack in packs or PACKS:
        if pack not in PACKS:
            raise ValueError(f"unknown pack {pack!r}; packs: {PACKS}")
        globals()[f"pack_{pack}"](k, f)
    return sorted(f, key=lambda x: (x["severity"] != "error", x["rule"], x["where"]))
