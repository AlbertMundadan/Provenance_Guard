# Provenance Guard — Planning

Backend that classifies submitted text as AI-generated vs. human-written, scores
confidence in that classification, renders a plain-language transparency label, logs every
decision to an audit trail, and lets creators appeal.

Stack: **Flask + Python**, **SQLite**
for the audit log and appeals, **Groq** for the LLM signal (with graceful offline fallback).

## 1. Detection Signals

The pipeline combines two distinct signals that measure different properties
of the text. Signal A is a holistic semantic judgment; Signal B is a structural/statistical
measurement. They can disagree and both contribute to the overall judgement.

### Signal A — LLM classification (Groq)

- **What it measures:** holistic semantic and stylistic coherence — "voice," cliché density,
  structural predictability, the things a reader intuits. Captures patterns that are hard to
  reduce to counting (e.g. generic hedging, over-balanced structure, absence of lived detail).
- **How:** send the text to a Groq-hosted instruction model (e.g. `llama-3.3-70b-versatile`)
  with a rubric prompt asking it to rate the probability the text is AI-generated and give a
  one-sentence rationale, returned as strict JSON.
- **Output:** `p_ai_llm` ∈ [0,1] (probability text is AI) + `rationale` (short string).
- **Fallback:** if no API key / network / parse failure, Signal A is marked `unavailable`
  and excluded from the combination; the run is flagged in the audit log and confidence is
  penalized.

### Signal B — Stylometric heuristics (pure Python)

- **What it measures:** measurable statistical regularities. AI prose tends to be uniform;
  human prose is bursty and irregular. 3 sub-metrics, each mapped to an "AI-ness" sub-score
  in [0,1] via a piecewise-linear map from documented human/AI reference ranges:
  1. **Sentence-length burstiness** — standard deviation of sentence length (in words).
     Low variance ⇒ AI-leaning.
  2. **Lexical diversity (MATTR)** — moving-average type-token ratio over a fixed window
     (window=50 words) so it's length-robust. Used as a mild monotonic cue (verified in M4 to
     be a weak discriminator — AI and human prose both cluster near 0.85): unusually low
     diversity / heavy repetition leans AI-uniform, rich vocabulary leans human.
  3. **Punctuation diversity & density** — variety and rate of `; : — ( ) ?` etc. Humans use
     a wider, less uniform punctuation palette.
- **Combination within Signal B:** weighted mean of the 3 sub-scores → `p_ai_style` ∈ [0,1].
  Weights: burstiness 0.45, MATTR 0.35, punctuation 0.20.
- **Output:** `p_ai_style` ∈ [0,1] + the three raw measurements (for the audit log).

### Why these two

They fail differently. The LLM is strong on meaning but can be fooled by surface mimicry;
stylometry is strong on structure but blind to meaning (it will flag a repetitive human poem
as "uniform"). Pairing a holistic judge with a structural measurer means each covers the
other's blind spot, and their _agreement_ is a usable confidence signal.

### Combining into one score

- `p_ai = w_llm · p_ai_llm + w_style · p_ai_style`, default **w_llm = 0.6, w_style = 0.4**
  (the LLM is the stronger holistic judge; stylometry anchors and catches its misses).
- If Signal A is unavailable: `p_ai = p_ai_style` and confidence is multiplied by 0.7 (reducing maximum confidence).

## 2. Uncertainty Representation

The system returns two numbers, not one binary flag:

- **`p_ai`** ∈ [0,1] — combined estimate the text is AI-generated (the _lean_).
- **`confidence`** ∈ [0,1] — how _decisive_ the classification is (drives the label tier).

**Classification (the lean), from `p_ai`:**
| `p_ai` range | classification |
|---|---|
| ≥ 0.65 | likely **AI** |
| 0.35 – 0.65 | **uncertain** |
| ≤ 0.35 | likely **human** |

**Confidence** is computed, not asserted:

1. `decisiveness = 2 · |p_ai − 0.5|` → 0 at the 0.5 fence, 1 at the extremes.
2. `boosted = decisiveness ** 0.5` → a concave response: confidence rises quickly as the lean
   leaves the fence, then plateaus. (The first clear evidence is the most informative; we do
   not demand near-certainty before showing a confident label. Without this boost, blending a
   strong signal with a middling one compresses `p_ai` toward 0.5 and structurally caps
   confidence below the tier cutoff — see the **Calibration note** below.)
3. `agreement = 1 − |p_ai_llm − p_ai_style|` → 1 when both signals concur, low when they diverge.
4. `confidence = boosted · (0.6 + 0.4 · agreement)`
5. If the LLM signal was unavailable: drop the agreement term → `confidence = boosted · 0.7`.
6. Short-text guard: if the text has < 4 sentences or < 50 words, `confidence ·= 0.5`
   (stylometry is unstable on tiny inputs).
7. Clamp to [0,1].

**What the cutoff means:** the high-confidence tier begins at **0.55**. A confidence just
under it means the lean is genuine but either close to the 0.5 fence or undercut by the two
signals disagreeing — so the reader sees the hedged "uncertain" label rather than a confident
one. A confidence near 0.9 means a strong lean _and_ both signals agreeing. That gap is why a
barely-leaning input and a clear-cut one surface meaningfully different labels.

**Label tier** (combines lean + confidence):

- **High-confidence AI** — classification = AI **and** confidence ≥ 0.55.
- **High-confidence human** — classification = human **and** confidence ≥ 0.55.
- **Uncertain** — classification = uncertain, **or** confidence < 0.55 for either lean.

**Calibration note (set in M4, verified empirically).** The 0.55 cutoff and the concave boost
were chosen by running both signals over a labelled fixture set (`samples.py`) and checking
the tiers land correctly. With a purely linear `decisiveness`, even clearly-AI text topped out
near 0.35 confidence (stylometry is a middling signal and drags the blended `p_ai` toward 0.5),
so the high-confidence tiers were unreachable and every result showed "uncertain." The boost +
0.55 cutoff make clearly-AI and clearly-human clear the bar while both borderline cases stay
under it. **Known residual misses** (see §5): when text fools _both_ signals the same way
(e.g. a terse repetitive poem that reads as AI to the LLM too), they agree and the result can
be a confident **mis**label — the "disagreement saves it" mitigation only works when the
signals actually disagree. A lightly-edited AI draft that the LLM reads as human can likewise
pass as confident human. (An earlier miss — polished human prose false-flagged by stylometry —
was traced to the MATTR sub-metric and fixed in M5.)

## 3. Transparency Label Design

Three variants. `{conf}` is the confidence score rendered as a whole-number percent.
Verbatim template text, followed by a concrete rendered example.

**High-confidence AI**

> **Likely AI-generated.** Our analysis found strong, consistent signs that this text was
> produced by an AI system. **Confidence: High (~{conf}%).** This is an automated assessment,
> not a certainty — automated checks can be wrong. If you wrote this yourself, you can appeal.

_Rendered (conf=88):_ "**Likely AI-generated.** Our analysis found strong, consistent signs
that this text was produced by an AI system. **Confidence: High (~88%).** This is an automated
assessment, not a certainty — automated checks can be wrong. If you wrote this yourself, you
can appeal."

**High-confidence human**

> **Likely human-written.** Our analysis found consistent signs that this text was written
> by a person. **Confidence: High (~{conf}%).** This is an automated assessment, not a guarantee
> of authorship. If you disagree with this label, you can appeal.

_Rendered (conf=91):_ "**Likely human-written.** Our analysis found consistent signs that
this text was written by a person. **Confidence: High (~91%).** This is an automated assessment,
not a guarantee of authorship. If you disagree with this label, you can appeal."

**Uncertain**

> **Not enough certainty to label.** Our analysis was mixed — the signals disagreed or were
> too weak to call. We can't confidently say whether this was written by a person or generated
> by AI. **Confidence: Low ({conf}%).** We're showing this openly rather than guessing. You can
> appeal if you'd like a human to review it.

_Rendered (conf=41):_ "**Not enough certainty to label.** Our analysis was mixed — the signals
disagreed or were too weak to call. We can't confidently say whether this was written by a
person or generated by AI. **Confidence: Low (41%).** We're showing this openly rather than
guessing. You can appeal if you'd like a human to review it."

## 4. Appeals Workflow

- **Who can appeal:** the creator/submitter of a piece of content, referenced by its
  `content_id` (returned at submission). Authentication of "who is the real creator" is out of
  scope for this backend — anyone holding the `content_id` may file. (Noted as a known limit.)
- **What they provide:** `content_id` (required), `reasoning` free-text (required),
  `claimed_authorship` (optional enum: `i_wrote_this` | `mislabeled_ai` | `other`),
  `contact` (optional).
- **What the system does on `POST /appeal`:**
  1. Validate the `content_id` exists and has a prior decision.
  2. Create an `appeals` row linked to the original `decision_id`.
  3. Update the content's status → **`under_review`**.
  4. Write an audit-log entry `event_type = appeal_filed` capturing the appeal id, the
     creator's reasoning, the timestamp, and a **snapshot of the original decision**
     (label, `p_ai`, `confidence`, per-signal scores). **No automated re-classification.**
- **What a human reviewer sees** (`GET /appeals?status=under_review`): a queue of rows, each
  showing `appeal_id`, `content_id`, a text excerpt, the original label + confidence +
  per-signal scores, the creator's `reasoning`, `claimed_authorship`, and `filed_at` — so the
  original decision and the creator's argument sit side by side.

## 5. Anticipated Edge Cases

1. **Repetitive / minimalist poetry** (a villanelle, or a poem built on a repeated refrain
   with simple vocabulary): deliberately low lexical diversity _and_ low sentence-length
   variance look exactly like AI uniformity to Signal B → false AI lean. The intended
   mitigation is that the LLM reads the human intent, the resulting disagreement lowers
   `confidence`, and the short-text guard reduces it further — pushing it to **uncertain**.
   **Verified limit (M4):** this only holds when the signals actually disagree. A terse,
   heavily repetitive poem can read as AI to the _LLM too_ (observed `p_ai_llm ≈ 0.8` on the
   `repetitive_poem` fixture); with both signals agreeing, the result is a **confident AI
   mislabel**. This is a real residual failure mode, not a fully solved case.
2. **AI draft lightly edited by a human:** a person tweaks a ChatGPT draft. This is genuinely
   hybrid authorship. **Verified behaviour (M5):** the outcome follows the LLM — on the
   `borderline_edited_ai` fixture the LLM reads the human edits as human (`p_ai_llm ≈ 0.2`),
   stylometry mildly agrees, and the result is **high-confidence human**, not "uncertain." So
   a lightly-edited AI draft can pass as human — the system has no way to recover the AI
   provenance once a human has smoothed the text. A real risk worth naming, not a solved case.
3. **Very short submissions** (a haiku, a tweet-length excerpt): too few sentences for
   stylometry to be stable and too little context for the LLM. Handled by the < 4-sentence /
   < 50-word confidence penalty, pushing these to **uncertain**.
4. **Polished human narrative** (well-edited prose with uniform sentence lengths): originally
   false-flagged by stylometry (the `clearly_human_2` fixture scored `p_ai_style ≈ 0.68` and
   landed in "uncertain"). **Root cause + fix (M5):** the MATTR sub-metric's "mid-band = AI"
   mapping was reading normal rich vocabulary (~0.85) as AI. Replaced with a monotonic mapping
   (low diversity → AI, high → human); `clearly_human_2` now correctly resolves to
   **high-confidence human**. Residual risk remains for prose that is *both* uniform in
   sentence length and moderate in diversity, where burstiness alone carries the score.

## Architecture

```
                          POST /submit  (text)
                                 │
                                 ▼
                   ┌──────────────────────────┐
                   │  Flask API + Rate Limiter │  (Flask-Limiter)
                   └──────────────────────────┘
                                 │
                                 ▼
                   ┌──────────────────────────┐
                   │   Detection Pipeline      │
                   │  ┌────────────┐ ┌───────┐ │
                   │  │ Signal A   │ │Signal │ │
                   │  │ LLM (Groq) │ │ B     │ │
                   │  │ p_ai_llm   │ │ style │ │
                   │  └─────┬──────┘ └───┬───┘ │
                   │        └────┬───────┘     │
                   │        ▼ Score Combiner   │
                   │   p_ai + confidence       │
                   │        ▼ Classifier       │
                   │        ▼ Label Generator  │
                   └────────────┬──────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                  ▼
       JSON response     Audit Log (SQLite)   content status
   (label, p_ai, conf,   classification event   = "classified"
    signals, content_id)

   APPEAL FLOW:
   POST /appeal ──► validate content ──► insert appeal (→ decision)
        ──► status = "under_review" ──► audit log "appeal_filed"
   GET /appeals ──► reviewer queue (decision snapshot + creator reasoning)
   GET /log     ──► structured audit trail (≥3 entries)
```

**Submission flow:** a client POSTs text to `/submit`; the rate limiter admits it, the
pipeline runs both signals, combines them into `p_ai` + `confidence`, picks a label tier,
and returns the structured result while writing a `classification` entry to the SQLite audit
log. **Appeal flow:** a creator POSTs `/appeal` with their `content_id` and reasoning; the
system records the appeal against the original decision, flips the content's status to
`under_review`, and logs an `appeal_filed` audit entry — a reviewer reads the queue via
`GET /appeals` with the original decision and the creator's argument side by side.

## Supporting design (for M3–M5)

- **Endpoints:** `POST /submit`, `POST /appeal`, `GET /appeals`, `GET /log`.
- **SQLite tables:** `content(id, text, status, created_at)`,
  `decisions(id, content_id, p_ai, confidence, label_tier, label_text, signals_json, created_at)`,
  `appeals(id, content_id, decision_id, reasoning, claimed_authorship, contact, status, filed_at)`,
  `audit_log(id, timestamp, event_type, content_id, decision_id, appeal_id, details_json)`.
- **Rate limiting:** `/submit` = **10 req/min and 100 req/hour per IP**; `/appeal` =
  **5 req/min per IP**; read endpoints = 60 req/min. Reasoning: `/submit` triggers a paid,
  latency-bound LLM call and is the abuse/cost vector — 10/min comfortably supports
  interactive testing and moderate platform throughput while blocking scraping and cost
  blow-ups; the hourly cap stops sustained drip abuse; appeals are cheap but throttled to
  deter spam; reads are generous.
- **Audit log:** every decision and appeal is an `audit_log` row with timestamp, event type,
  confidence, the signals used, and (for appeals) the reasoning — documented in the README
  with ≥3 sample entries via `GET /log`.

## AI Tool Plan

**M3 — submission endpoint + first signal (stylometry)**

- _Spec provided to the AI tool:_ §1 Detection Signals (Signal B in full) + the Architecture
  diagram + endpoint/table definitions.
- _Ask it to generate:_ the Flask app skeleton (`/submit` route, SQLite init) and the
  pure-Python stylometry signal function returning `p_ai_style` + raw metrics.
- _Verify:_ call the stylometry function directly on a handful of clearly-human and clearly-AI
  samples and eyeball that scores separate, **before** wiring it into the endpoint; confirm
  `/submit` persists a row and returns structured JSON.

**M4 — second signal + confidence scoring**

- _Spec provided:_ §1 (Signal A / Groq) + §2 Uncertainty Representation + the diagram.
- _Ask it to generate:_ the Groq LLM signal function (with graceful fallback) and the score
  combiner (`p_ai`, `confidence`, classification, tier) exactly per §2's formulas.
- _Check:_ run the labeled fixture set — do scores vary meaningfully between clearly-AI and
  clearly-human text, and does the disagreement test drop confidence below 0.70?

**M5 — production layer (labels + appeals)**

- _Spec provided:_ §3 Label variants + §4 Appeals workflow + the diagram.
- _Ask it to generate:_ the label-generation logic (three tiers, exact strings) and the
  `/appeal` endpoint plus `/appeals` and `/log` reads, with rate limiting wired in.
- _Verify:_ craft inputs that reach all three label tiers; confirm an appeal flips status to
  `under_review`, writes an `appeal_filed` audit entry, and appears in the reviewer queue.
