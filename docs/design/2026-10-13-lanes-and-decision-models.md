# Lanes, routines and decision models (2026-10-13)

Status: built (this commit). Closes G3 of `2026-10-13-paperclip-contrast.md`; the mechanism is
documented in `docs/GENOME.md` §10. The trust boundary (G1) follows in its own record.

## 1. What the Chair asked for

- A **fast lane** for small and recurring work: plan and execute in one session, the way agentic
  cron jobs already run.
- **Rubrics** for the agent loops inside those jobs, so the **golden trajectory** (the best verified
  run) becomes each job's gold standard.
- **Efficiency enforced by the architecture**: a new automation uses the cheapest method that
  works (a script where possible, then a lean agent, then a full workflow), and a **classifier**
  decides which, on decision models from OpenRouter (`respan/span-01-lite:free`,
  `inception/mercury-decide:free`).
- More uses for those decision models.

## 2. Decisions

| Decision | Why |
|---|---|
| Three lanes, `script` → `fast` → `full`, stored on every work node and stage (`tasks.lane`) | one word for "how this runs" that briefs, claims, metrics and Notion all read |
| Fast work is a normal execute stage after a *standing* plan and approval recorded by the kernel (`lane:fast`) | nothing downstream changes: context, staleness, verification and harnesses see ordinary approved work |
| The fast lane is the kernel's call, not the classifier's: only work whose deterministic grade floor is G0 | skipping plan and approval must never rest on a model's opinion |
| Fast results are graded again on the `writes` and `external_effects` they report; above the approved grade is a *lane breach* told to the Chair | the gate moved from before the act to after it, so the check must exist after it |
| Script stages are claimable only by the kernel (`Work.claim(kernel=True)`); the command is part of the approved plan | an agent can never run, or rewrite, a script routine |
| A routine is a kernel proposal graded ≥ G2 (G3 if it reaches outside); approval starts its schedule | it runs unattended forever: a significant decision by default |
| Lane enforcement at `routine add`: deterministic ⇒ script (or `--why`); cheaper than needed ⇒ raised; costlier ⇒ `--why` | "most efficient method" in both directions, with the Chair able to overrule on the record |
| Rubric judge = one yes/no question per criterion plus one against the gold standard; all ≥ `pass_p` closes without a verifier | decision models are calibrated per question; a verifier session is minutes, a judgment is a second |
| Judged results stay unverified (`verified_by = rubric`), and a sample still goes to a verifier (`rubric_agreement`) | the judge never earns anyone trust, and its precision is measured, with a bound that triggers a retro |
| Gold standard = the last result a verifier or the Chair passed for the same routine (else class + department), shown to executor and judge | no curation step to forget; the standard improves as verified runs accumulate |
| Decision models are reached directly at their endpoint (`[classifier]`), not through free-stack | free-stack is a chat pipeline (it took > 60 s for a one-word reply); decision models have their own endpoint and no chat interface |

## 3. What the decision models are (verified live)

Both are served on OpenRouter's decisions endpoint (`POST https://openrouter.ai/api/alpha/decisions`,
body `{"model", "state", "questions": {id: {"type", "instructions", "criteria"}}}`), not on chat
completions, which reject them.

| Model | Accepts | Observed |
|---|---|---|
| `inception/mercury-decide:free` | `choice` (criteria: option → description), `score` (criteria: ordered levels), `noul` (yes/no) | 0.8–0.9 s; choice returns probabilities and a confidence; cost 0 |
| `respan/span-01-lite:free` | `noul` only; criteria must be an object `{"true", "false"}` of strings | 0.6–1.2 s; returns `noul` probability; cost 0 |

Hence `model = mercury-decide` (any question) and `noul_model = span-01-lite` (used when every
question is yes/no, i.e. the rubric judge).

## 4. Evidence: the organism's 61 cron jobs

`holon classify` over every `ventures/*/cron-jobs/*.md` (title + description + protocol, 55 s for
all 61):

| Lane (confidence ≥ 0.8) | Jobs | Sessions per firing |
|---|---|---|
| script | 18 | 0 |
| fast | 28 | 1 |
| unsure → full | 15 | 4 |

- All 7 jobs the organism already runs without an agent (`no_agent: true`) were classified
  `script`, each at ≥ 0.9: the classifier agrees with the hand-made ground truth 7/7.
- Moving all 61 into Holon's full pipeline would cost 244 sessions per round of firings; with lanes it
  is 88, and 18 jobs stop costing agent time altogether.
- The first "reaches outside the organization" question flagged 58 of 61 jobs, because every job
  delivers its digest to the Chair's Telegram. Rephrased to exclude reports to the Chair, it flags 9,
  which are the ones that do reach third parties (outreach, follow-ups, a Reddit post, a WhatsApp
  message to a client). Guard: `test_reports_to_the_chair_do_not_count_as_reaching_outside`.

## 5. Other uses of the decision models

Built (GENOME §10 table): lane choice at intake and for routines; the outside-reach flag; the
rubric judge; failure triage on incidents (harness / model / brief / environment / work); routing
new work to a department by mission (`task add --dept auto`).

Proposed next, in order of leverage:

1. **Pilot guard.** The Chair's rule "pilot work never sends outreach" as a check: an execute
   result or plan the outside-reach question flags in a pilot department goes to the Chair instead of
   running.
2. **Duplicate intake.** "Is this the same as open work X?" before `task add` and venture intake
   (G4), so scanners do not file the same lead twice.
3. **Notify urgency.** Which Chair items are urgent now and which wait for the digest (G6).
4. **Memory dedup.** "Is lesson A the same lesson as B?" so promotion counts colleagues who learned
   the same thing in different words.
5. **Inbound Chair notes.** Classify a Notion note as instruction, question or decision before it
   reaches a planner.
6. **Stall detection.** "Is this driver log making progress?" on long runs, before the lease expires.

## 6. Limits

- **Quota.** OpenRouter's free tier allows 50 decision requests a day per account (1000 a day after
  a one-time 10-credit top-up). The live run used them up in an afternoon; a judged script then got
  HTTP 429 and fell back to its verifier. Every failure is now an event and one line to the Chair
  (`decision model unavailable …; results go to verifiers meanwhile`). The organism's crons alone
  would need well over 50 a day.
- **Cline.** The Cline API (`api.cline.bot/api/v1`) proxies chat completions only; it answers
  `model not found` for `inception/mercury-decide:free` and has no decisions endpoint, so these
  models cannot be reached through it.
- Free decision models can be withdrawn or rate-limited; every use has a safe default, and
  `holon doctor` does not yet report a failing classifier (a failure is recorded in the result as
  `source: failed`).
- The rubric judge reads the result's own documentation; a result that misdescribes what it did
  can pass. The machine checks, the post-hoc regrade and the sampled verifier are the guards, and
  `rubric_agreement` measures how often they disagree.
- The fast lane needs a track record (G0 requires `novelty_min_verified` verified results in the
  task class), so a new kind of work runs full until it has one. This is intended.
