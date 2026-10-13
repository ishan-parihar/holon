"""Decision models: typed judgments with calibrated probabilities, for choices the kernel makes often.

A decision endpoint takes a `state` (text) and typed `questions` and returns, per question, a choice
with probabilities, a score, or a yes/no probability ("noul"). It costs a fraction of a second and no
output tokens, so the kernel can ask on every intake and every result instead of spending an agent
session. Configured in holon.toml (docs/GENOME.md §10):

    [classifier]
    url = "https://…/decisions"           # the endpoint; empty = off
    key_file = "~/.config/holon/classifier.key"   # 0600; read by the kernel, never put in a run's environment
    model = "…"                           # answers choice, score and yes/no questions
    noul_model = "…"                      # optional: a yes/no-only model used when every question is yes/no

The rule every caller keeps: a decision may only make the organization *more* careful on its own
(a costlier lane, a verifier, a higher floor). Where it lets something skip a step, the kernel's
deterministic checks must allow it too, and anything below `min_confidence` takes the safe default.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

DEFAULTS = {"url": "", "key_file": "", "model": "", "noul_model": "", "timeout_s": 15,
            "min_confidence": 0.8, "pass_p": 0.85}


class Decider:
    def __init__(self, cfg: dict | None):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.error: str | None = None

    @property
    def on(self) -> bool:
        return bool(self.cfg["url"] and self.cfg["model"])

    def _key(self) -> str | None:
        f = self.cfg["key_file"]
        try:
            return Path(f).expanduser().read_text().strip() if f else None
        except OSError:
            return None

    def ask(self, state: str, questions: dict) -> dict | None:
        """Answers keyed by question id, or None: off, or the endpoint failed (`self.error` says why).
        Callers treat None as "no opinion" and take their safe default."""
        if not self.on or not questions:
            return None
        noul_only = all(q["type"] == "noul" for q in questions.values())
        model = (self.cfg["noul_model"] or self.cfg["model"]) if noul_only else self.cfg["model"]
        headers = {"Content-Type": "application/json"}
        key = self._key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        body = json.dumps({"model": model, "state": state[:60_000], "questions": questions}).encode()
        try:
            req = urllib.request.Request(self.cfg["url"], body, headers)
            with urllib.request.urlopen(req, timeout=float(self.cfg["timeout_s"])) as r:
                answers = json.load(r).get("answers") or {}
        except urllib.error.HTTPError as e:
            with e:
                self.error = f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}"
            return None
        except (OSError, ValueError) as e:
            self.error = f"{type(e).__name__}: {e}"
            return None
        if set(answers) != set(questions):
            self.error = f"answers {sorted(answers)} do not match questions {sorted(questions)}"
            return None
        self.error = None
        return answers


def noul(question: str, yes: str, no: str) -> dict:
    return {"type": "noul", "instructions": question, "criteria": {"true": yes, "false": no}}


def choice(question: str, options: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": question, "criteria": options}


def picked(answer: dict) -> tuple[str | None, float]:
    """(choice, confidence) of a choice answer."""
    return answer.get("choice"), float(answer.get("confidence") or 0.0)


def p_yes(answer: dict) -> float:
    return float(answer.get("noul") or 0.0)
