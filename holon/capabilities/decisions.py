"""Decision models: typed judgments with calibrated probabilities, for choices the kernel makes often.

A decision endpoint takes a `state` (text) and typed `questions` and returns, per question, a choice
with probabilities, a score, or a yes/no probability ("noul"). It costs a fraction of a second and no
output tokens, so the kernel can ask on every intake and every result instead of spending an agent
session. Configured in holon.toml (docs/BYLAWS.md §10):

    [classifier]
    url = "https://…/decisions"           # the endpoint; empty = off
    key_file = "~/.config/holon/classifier.key"   # 0600; read by the kernel, never put in a run's environment
    model = "…"                           # answers choice, score and yes/no questions
    noul_model = "…"                      # optional: a yes/no-only model used when every question is yes/no
    fallback_url = "…"                    # optional: a second endpoint (same protocol) for decisions that can
    fallback_model = "…"                  #   wait (the rubric judge), tried when the first fails; it may be
    fallback_key_file = "…"               #   slow, hence its own timeout
    fallback_timeout_s = 330

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
            "min_confidence": 0.8, "pass_p": 0.85, "fallback_url": "", "fallback_model": "", "fallback_key_file": "",
            "fallback_timeout_s": 330}


class Decider:
    def __init__(self, cfg: dict | None):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.error: str | None = None

    @property
    def on(self) -> bool:
        return bool(self.cfg["url"] and self.cfg["model"])

    @staticmethod
    def _key(f: str) -> str | None:
        try:
            return Path(f).expanduser().read_text().strip() if f else None
        except OSError:
            return None

    def ask(self, state: str, questions: dict, patient: bool = False) -> dict | None:
        """Answers keyed by question id, or None: off, or every endpoint failed (`self.error` says why).
        Callers treat None as "no opinion" and take their safe default. `patient`: the caller can wait for the
        fallback endpoint (it runs in a dispatch, not in the Chair's terminal or the heartbeat)."""
        if not self.on or not questions:
            return None
        noul_only = all(q["type"] == "noul" for q in questions.values())
        model = (self.cfg["noul_model"] or self.cfg["model"]) if noul_only else self.cfg["model"]
        a = self._post(self.cfg["url"], model, self.cfg["key_file"], self.cfg["timeout_s"], state, questions)
        if a is None and patient and self.cfg["fallback_url"] and self.cfg["fallback_model"]:
            first = self.error
            a = self._post(self.cfg["fallback_url"], self.cfg["fallback_model"], self.cfg["fallback_key_file"],
                           self.cfg["fallback_timeout_s"], state, questions)
            if a is None:
                self.error = f"{first}; fallback: {self.error}"
        return a

    def _post(self, url: str, model: str, key_file: str, timeout_s, state: str, questions: dict) -> dict | None:
        headers = {"Content-Type": "application/json"}
        key = self._key(key_file) if key_file else None
        if key:
            headers["Authorization"] = f"Bearer {key}"
        body = json.dumps({"model": model, "state": state[:60_000], "questions": questions}).encode()
        try:
            req = urllib.request.Request(url, body, headers)
            with urllib.request.urlopen(req, timeout=float(timeout_s)) as r:
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
