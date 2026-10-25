"""The architecture index: the distribution's own AD/RG corpus, queryable and injectable.

Plan: the Chair's 2026-10-25 red-team ruling on the AD/RG proposal. The corpus stays
markdown — it is the source of truth, because every gate that holds the system honest reads
prose (ref.guard, pack_register, pack_scrub, pack_boundary, pack_tables, the citation guard),
and because git is how it reaches both checkouts and the sync gate. What is missing is not a
new store; it is **retrieval**: no command could ask "what did we learn about X" across the
ledger, the design records and the decisions, and no brief carried the findings that matter to
the work at hand — an agent touching isolation had no way to learn that isolation's last three
defects were about a volatile script path, a refused plan and an ACL arg limit.

This module derives a SQLite index at `$HOLON_HOME/ad/index.db` from the corpus, the same
shape as the KB's derived `kb.db`: files stay the source of truth, the index is rebuildable,
and each row carries the file and sha it was derived from so staleness is checkable. The
build is a *guard*: it refuses to complete when a ledger row names a test that does not exist —
the invariant `ref.guard` greps for, as a build error rather than a lint pass, so a deleted
guard fails at the seam that writes the index, not only in CI.

One writer per database (law 3): this module is the only writer of index.db. Everything else —
including the context bundle — opens it read-only.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

from holon.core.paths import Home


class IndexError_(ValueError):
    """The build refused: the corpus says something the tests do not carry."""


def _dist() -> Path:
    """The distribution root, decided the one place both sides import (the cold-start rule)."""
    from holon.integrity.lint import DIST
    return DIST


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _parse_ledger(text: str) -> list[dict]:
    """Every row of AGENTS.md's regression ledger: | Found | Defect | Guard |.

    The row is one markdown table line; the last cell is the backticked guard names. A row whose
    defect prose contains a literal `\\|` is one row (the tables pack's own rule), so cells are
    counted the way markdown counts them.
    """
    out = []
    start = text.find("## Regression ledger")
    if start < 0:
        return out
    for line in text[start:].splitlines():
        if not line.startswith("| ") or line.startswith("| Found |") or "---" in line:
            continue
        body = line.strip().strip("|")
        cells = [c.strip() for c in body.replace("\\|", "\x00").split("|")]
        if len(cells) < 3:
            continue
        # the guard cell carries backticked test names plus parenthetical prose ("(driver
        # `usage_line`)", "(the folded cases)") — measured 2026-10-25, splitting on commas glued
        # the prose onto the name and the build read 7 real guards as missing. Only a backticked
        # `test_...` token is a guard name; everything else in the cell is annotation.
        guards_cell = cells[2].replace("\x00", "|")
        out.append({
            "area": cells[0],
            "defect": cells[1].replace("\x00", "|"),
            "guards": re.findall(r"`(test_\w+)`", guards_cell),
        })
    return out


def _parse_decisions(dist: Path) -> list[dict]:
    """AD-NNN records under docs/decisions/: id, status, title, supersedes, file."""
    out = []
    d = dist / "docs" / "decisions"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.md")):
        text = f.read_text(errors="replace")
        fm = dict(re.findall(r"^(\w+):\s*(.+)$", text.split("---")[0] if "---" in text else text[:600],
                             re.M))
        out.append({
            "id": fm.get("id") or f.stem,
            "status": fm.get("status", "active"),
            "title": fm.get("title") or f.stem,
            "supersedes": fm.get("supersedes"),
            "file": f.relative_to(dist).as_posix(),
            "sha": _sha(text),
        })
    return out


def _parse_records(dist: Path) -> list[dict]:
    """Dated design records under docs/design/: id, date, status, title, supersedes."""
    out = []
    d = dist / "docs" / "design"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.md")):
        text = f.read_text(errors="replace")
        head = text[:1200]
        fm = dict(re.findall(r"^(\w+):\s*(.+)$", head, re.M))
        title = next((l.lstrip("# ").strip() for l in head.splitlines() if l.startswith("# ")), f.stem)
        out.append({
            "id": f.stem,
            "date": fm.get("date") or f.stem[:10],
            "status": fm.get("status", "unknown"),
            "title": title,
            "supersedes": fm.get("supersedes"),
            "file": f.relative_to(dist).as_posix(),
            "sha": _sha(text),
        })
    return out


def _guards(dist: Path) -> dict[str, str]:
    """Every test name in the suite, by module — the set ref.guard checks against."""
    out: dict[str, str] = {}
    tests = dist / "tests"
    if not tests.is_dir():
        return out
    for f in sorted(tests.glob("test_*.py")):
        for m in re.findall(r"def (test_\w+)", f.read_text(errors="replace")):
            out[m] = f.name
    return out


def build(k) -> dict:
    """Derive the index from the corpus. Refuses to complete on a dangling guard name.

    The refusal is the point: `ref.guard` makes a deleted guard fail *lint*, which a person
    runs; this makes it fail the *build*, which the heartbeat, the brief and every `ad find`
    runs. A regression ledger that names a test nobody carries is a claim with no teeth, and
    the seam that writes the index is the first thing that needs the teeth.
    """
    dist = _dist()
    home: Home = k.home
    root = home.root / "ad"
    root.mkdir(parents=True, exist_ok=True)
    db = root / "index.db"
    agents = (dist / "AGENTS.md").read_text(errors="replace")
    findings = _parse_ledger(agents)
    decisions = _parse_decisions(dist)
    records = _parse_records(dist)
    guards = _guards(dist)
    missing: list[str] = []
    for row in findings:
        for g in row["guards"]:
            if g not in guards:
                missing.append(g)
    if missing:
        raise IndexError_(
            f"{len(missing)} guard(s) named by the regression ledger do not exist in the test "
            f"suite: {', '.join(sorted(set(missing))[:5])} — a defect's guard cannot be deleted "
            "silently (AGENTS.md: the ledger is checked by lint, and the index build refuses to "
            "carry a claim with no teeth)")
    if db.exists():
        db.unlink()
    conn = sqlite3.connect(db)
    try:
        conn.executescript("""
CREATE TABLE decisions(id TEXT PRIMARY KEY, status TEXT, title TEXT, supersedes TEXT,
                       file TEXT, sha TEXT);
CREATE TABLE findings(id INTEGER PRIMARY KEY, area TEXT, defect TEXT);
CREATE TABLE guards(name TEXT PRIMARY KEY, module TEXT);
CREATE TABLE findings_guards(finding_id INTEGER REFERENCES findings(id),
                             guard TEXT REFERENCES guards(name),
                             PRIMARY KEY(finding_id, guard));
CREATE TABLE records(id TEXT PRIMARY KEY, date TEXT, status TEXT, title TEXT, supersedes TEXT,
                     file TEXT, sha TEXT);
CREATE VIRTUAL TABLE corpus_fts USING fts5(text, source_id, tokenize='porter');
""")
        conn.executemany("INSERT INTO decisions VALUES (?,?,?,?,?,?)",
                         [(d["id"], d["status"], d["title"], d["supersedes"], d["file"], d["sha"])
                          for d in decisions])
        conn.executemany("INSERT INTO findings(area, defect) VALUES (?,?)",
                         [(r["area"], r["defect"]) for r in findings])
        conn.executemany("INSERT INTO guards VALUES (?,?)", sorted(guards.items()))
        # join by the finding's own rowid, not by area: two ledger rows can share an area
        # (measured: the IntegrityError a first draft hit), and a shared area glued every row's
        # guards onto every same-area row.
        for i, r in enumerate(findings):
            conn.executemany("INSERT INTO findings_guards VALUES (?,?)",
                             [(i + 1, g) for g in r["guards"]])
        conn.executemany("INSERT INTO records VALUES (?,?,?,?,?,?,?)",
                         [(r["id"], r["date"], r["status"], r["title"], r["supersedes"], r["file"],
                           r["sha"]) for r in records])
        rows = []
        for i, r in enumerate(findings):
            rows.append((f"{r['area']}: {r['defect']}", f"finding:{i}"))
        for d in decisions:
            rows.append((f"{d['id']}: {d['title']}", f"decision:{d['id']}"))
        for r in records:
            rows.append((f"{r['id']}: {r['title']}", f"record:{r['id']}"))
        conn.executemany("INSERT INTO corpus_fts(text, source_id) VALUES (?,?)", rows)
        conn.commit()
    finally:
        conn.close()
    return {"db": str(db), "findings": len(findings), "decisions": len(decisions),
            "records": len(records), "guards": len(guards)}


def _open(k, readonly: bool = True) -> sqlite3.Connection:
    db = getattr(getattr(k, "home", k), "root", k) / "ad" / "index.db"
    if not db.exists():
        raise IndexError_("the architecture index is not built for this instance: `holon ad index`")
    uri = f"file:{db}" + ("?mode=ro" if readonly else "")
    return sqlite3.connect(uri, uri=True)


def find(k, query: str, limit: int = 8) -> list[dict]:
    """FTS over findings, decisions and records; the id each hit resolves to."""
    conn = _open(k)
    try:
        rows = conn.execute(
            "SELECT source_id, snippet(corpus_fts, 0, '', '', '…', 12), rank "
            "FROM corpus_fts WHERE corpus_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit)).fetchall()
    finally:
        conn.close()
    return [{"source": r[0], "text": r[1]} for r in rows]


def show(k, ident: str) -> dict | None:
    """One row by id: AD-NNN, a record's file stem, or a finding's area."""
    conn = _open(k)
    try:
        if re.match(r"^AD-", ident):
            row = conn.execute("SELECT * FROM decisions WHERE id=?", (ident,)).fetchone()
            if row:
                return {"kind": "decision", **dict(zip([c[0] for c in conn.execute(
                    "SELECT * FROM decisions LIMIT 0").description], row))}
        row = conn.execute("SELECT * FROM records WHERE id=? OR file=?", (ident, ident)).fetchone()
        if row:
            return {"kind": "record", **dict(zip([c[0] for c in conn.execute(
                "SELECT * FROM records LIMIT 0").description], row))}
        rows = conn.execute("SELECT id, area, defect FROM findings WHERE area LIKE ? "
                            "ORDER BY id", (f"%{ident}%",)).fetchall()
        if rows:
            return {"kind": "findings", "rows": [{"id": r[0], "area": r[1], "defect": r[2]}
                                                 for r in rows]}
    finally:
        conn.close()
    return None


def precedents(k, text: str, limit: int = 5) -> list[dict]:
    """The context-injection seam: findings whose defect prose matches `text`, capped.

    This is what a brief's Precedent section renders — the recall floor's shape, not the
    bundle's: a small, relevant cut of the ledger, never the whole corpus. The corpus is
    100k+ tokens; a body needs the five rows that bear on its work.

    `k` needs only `.home.root` (a Kernel or a Home both carry it), so the context compiler can
    pass its own `home` without building a Kernel.
    """
    words = [w for w in re.findall(r"[a-z][a-z0-9_-]{3,}", text.lower()) if len(w) > 3]
    if not words:
        return []
    q = " OR ".join(f'"{w}"' for w in words[:12])
    return find(k, q, limit)
