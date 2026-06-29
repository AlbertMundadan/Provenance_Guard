# Provenance Guard

A backend that classifies submitted text as **AI-generated vs. human-written**, scores its
**confidence**, returns a plain-language **transparency label**, logs every decision to a
structured **audit trail**, and lets creators **appeal** a classification.

Stack: **Flask + Python**, **SQLite** (audit log + appeals), **Groq** for the LLM signal
(with a graceful offline fallback). Full design rationale lives in [planning.md](planning.md).

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "GROQ_API_KEY=your_key_here" > .env
python app.py                                  # serves on http://localhost:5000
```

### Endpoints

| Method & path  | Purpose                                                                             |
| -------------- | ----------------------------------------------------------------------------------- |
| `POST /submit` | Classify text. Body: `{"text": "...", "creator_id": "..."}`                         |
| `POST /appeal` | Contest a classification. Body: `{"content_id": "...", "creator_reasoning": "..."}` |
| `GET /appeals` | Reviewer queue (`?status=under_review` to filter)                                   |
| `GET /log`     | Structured audit log, newest first                                                  |

```bash
curl -s -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"text": "The sun dipped below the horizon...", "creator_id": "test-user-1"}' | python -m json.tool
```

---

## Detection pipeline — two distinct signals

The classifier combines **two signals that measure genuinely different properties** of the
text (details in [planning.md §1](planning.md)). They can disagree, and that disagreement is
itself used as a confidence input.

**Signal A — LLM classification (Groq, `llama-3.3-70b-versatile`).** Captures _holistic
semantic and stylistic coherence_ — voice, cliché density, structural predictability, the
absence of lived detail. Output: `p_ai_llm ∈ [0,1]` + a one-sentence rationale.
Implemented in [signals/llm.py](signals/llm.py).

**Signal B — Stylometric heuristics (pure Python).** Captures _measurable structural
regularity_ — AI prose tends to be uniform, human prose bursty. Three sub-metrics combine
(weighted 0.45 / 0.35 / 0.20) into `p_ai_style ∈ [0,1]`: sentence-length **burstiness**
(stdev), **lexical diversity** (MATTR), and **punctuation diversity & density**.
Implemented in [signals/stylometry.py](signals/stylometry.py).

**Why two?** They fail differently. The LLM understands meaning but can be fooled by surface
mimicry; stylometry measures structure but is blind to meaning. Each covers the other's blind
spot. Run them side by side with `python compare_signals.py`.

### Combining into one score

```
p_ai       = 0.6 · p_ai_llm + 0.4 · p_ai_style          # the "lean"
classification:  p_ai ≥ 0.65 → likely_ai  |  ≤ 0.35 → likely_human  |  else uncertain
```

If the LLM is unavailable, `p_ai = p_ai_style` and confidence is penalised (see below).

---

## Confidence scoring & uncertainty

Confidence is a **separate** number from the lean — it answers "how sure are we?", and it
drives which label a reader sees. It is computed, not asserted (full derivation in
[planning.md §2](planning.md)):

```
decisiveness = 2 · |p_ai − 0.5|          # 0 at the 50/50 fence, 1 at the extremes
boosted      = decisiveness ** 0.5        # concave: confidence rises fast off the fence
agreement    = 1 − |p_ai_llm − p_ai_style|
confidence   = boosted · (0.6 + 0.4 · agreement)
   · 0.7   if the LLM signal was unavailable
   · 0.5   if the text has < 4 sentences or < 50 words   (signals are unstable on tiny inputs)
```

A confidence of **0.95** means a strong lean _and_ both signals agreeing; a **0.51** means
the lean is weak or the signals disagree. The two produce different labels — that's the point.

**High-confidence tier cutoff: 0.55** (empirically calibrated).

### How I tested whether the scores are meaningful

- **Formula assertions** — `python scoring.py` asserts the implementation matches every
  threshold and formula in planning.md (classification cutoffs, the confidence formula, the
  short-text/LLM-down penalties, the tier cutoff). It also locks in the calibration goals
  (clear cases clear the bar; borderline cases don't).
- **Labelled fixtures** — `samples.py` holds clearly-AI, clearly-human, and borderline texts.
  `python compare_signals.py` runs both signals over them and prints the lean, agreement,
  combined score, confidence, and tier, so you can see the scores separate:

```
sample                       llm  style  agree   p_ai   conf  classification tier
clearly_ai                  0.90   0.48   0.58   0.73   0.57  likely_ai      high_confidence_ai
clearly_human               0.20   0.23   0.97   0.21   0.75  likely_human   high_confidence_human
borderline_formal_human     0.80   0.38   0.58   0.63   0.22  uncertain      uncertain
repetitive_poem             0.80   0.94   0.86   0.86   0.80  likely_ai      high_confidence_ai
```

**A calibration finding worth stating plainly:** with a purely linear `decisiveness`, even
clearly-AI text topped out near 0.35 confidence (stylometry is a middling signal that drags
the blend toward 0.5), so the high-confidence tiers were unreachable. The concave boost +
0.55 cutoff fixed that. See [planning.md §2 "Calibration note"](planning.md) and the **Known
limitations** section below for the residual misses this surfaced.

---

## Transparency label — the three variants

The label returned by `/submit` **changes with the confidence tier** — it is never the same
text regardless of score. Generated by [labels.py](labels.py) (`python labels.py` prints and
verifies all three). Verbatim text:

| Tier                      | Label text (`{conf}` = confidence as a percent)                                                                                                                                                                                                                                                                           |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **High-confidence AI**    | "Likely AI-generated. Our analysis found strong, consistent signs that this text was produced by an AI system. Confidence: High (~{conf}%). This is an automated assessment, not a certainty — automated checks can be wrong. If you wrote this yourself, you can appeal."                                                |
| **High-confidence human** | "Likely human-written. Our analysis found consistent signs that this text was written by a person. Confidence: High (~{conf}%). This is an automated assessment, not a guarantee of authorship. If you disagree with this label, you can appeal."                                                                         |
| **Uncertain**             | "Not enough certainty to label. Our analysis was mixed — the signals disagreed or were too weak to call. We can't confidently say whether this was written by a person or generated by AI. Confidence: Low ({conf}%). We're showing this openly rather than guessing. You can appeal if you'd like a human to review it." |

**All three reachable via the live endpoint** (canonical fixtures, captured run):

```
[clearly_ai              ] tier=high_confidence_ai     conf=0.5668
[clearly_human           ] tier=high_confidence_human  conf=0.7518
[borderline_formal_human ] tier=uncertain              conf=0.2156
```

---

## Appeals workflow

A creator who disagrees with a classification can contest it. `POST /appeal` with the
`content_id` (from the original `/submit` response) and `creator_reasoning`:

```bash
curl -s -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id": "PASTE-CONTENT-ID", "creator_reasoning": "I wrote this myself from personal experience..."}' | python -m json.tool
```

On receipt the system (no automated re-classification — a human reviews):

1. validates the `content_id` has a prior decision (404 otherwise),
2. updates the content's status → **`under_review`**,
3. logs an `appeal_filed` audit entry alongside a snapshot of the original decision,
4. returns a confirmation.

```json
{
  "message": "Appeal received. The content is now under review by a human moderator.",
  "appeal_id": 1,
  "content_id": "81e71bee-c717-4867-a50a-c46b2c0305cc",
  "status": "under_review"
}
```

A reviewer opens the queue via `GET /appeals?status=under_review`, which shows the creator's
reasoning beside the original decision (label, confidence, per-signal scores) and a text
excerpt. **Auth note:** identifying the "real" creator is out of scope here — anyone holding
the `content_id` may file; `/appeals` and `/log` are unauthenticated for grading visibility.

---

## Rate limiting

Applied with Flask-Limiter (per client IP, in-memory store):

| Endpoint       | Limit                | Reasoning                                                                                                                                                                                                                                                                                                                      |
| -------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `POST /submit` | **10/min, 100/hour** | `/submit` triggers a paid, latency-bound LLM call — it's the cost and abuse vector. A real writer submits a handful of pieces per session, so 10/min is generous for legitimate interactive use while stopping a script from flooding the system; the hourly cap blocks sustained drip abuse without throttling a normal user. |
| `POST /appeal` | **5/min, 50/hour**   | Appeals are cheap but should not be spammable; a creator files one or two per piece.                                                                                                                                                                                                                                           |

**Evidence** — 12 rapid requests against the 10/min limit (first 10 succeed, rest are
rejected with HTTP 429):

```
request 1 -> 200      request 7  -> 200
request 2 -> 200      request 8  -> 200
request 3 -> 200      request 9  -> 200
request 4 -> 200      request 10 -> 200
request 5 -> 200      request 11 -> 429
request 6 -> 200      request 12 -> 429
```

---

## Audit log

Every classification and appeal is written to the SQLite `audit_log` table (structured rows,
not console output) and exposed at `GET /log`. Each entry captures: `timestamp`, `content_id`,
`attribution`, `confidence`, **both** individual signal scores (`llm_score`, `style_score`),
the combined `p_ai`, signal `agreement`, `status`, and — for appeals — `appeal_id` +
`appeal_reasoning`. Sample (`GET /log`, three representative entries):

```json
[
  {
    "id": 4,
    "event_type": "classification",
    "content_id": "81e71bee-c717-4867-a50a-c46b2c0305cc",
    "creator_id": "writer-ai",
    "timestamp": "2026-06-29T18:51:35.311Z",
    "attribution": "likely_ai",
    "confidence": 0.5668,
    "llm_score": 0.9,
    "style_score": 0.48,
    "p_ai": 0.732,
    "agreement": 0.58,
    "status": "classified",
    "appeal_id": null,
    "appeal_reasoning": null
  },
  {
    "id": 5,
    "event_type": "classification",
    "content_id": "6a2837ad-82c2-4964-a06c-da6cf0cb3170",
    "creator_id": "writer-human",
    "timestamp": "2026-06-29T18:51:35.737Z",
    "attribution": "likely_human",
    "confidence": 0.7518,
    "llm_score": 0.2,
    "style_score": 0.2277,
    "p_ai": 0.2111,
    "agreement": 0.972,
    "status": "classified",
    "appeal_id": null,
    "appeal_reasoning": null
  },
  {
    "id": 7,
    "event_type": "appeal_filed",
    "content_id": "81e71bee-c717-4867-a50a-c46b2c0305cc",
    "creator_id": null,
    "timestamp": "2026-06-29T18:51:49.816Z",
    "attribution": "likely_ai",
    "confidence": 0.5668,
    "llm_score": 0.9,
    "style_score": 0.48,
    "p_ai": 0.732,
    "agreement": 0.58,
    "status": "under_review",
    "appeal_id": 1,
    "appeal_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical."
  }
]
```

---

## Graceful fallback (no Groq key / no network)

If `GROQ_API_KEY` is unset or the call fails, Signal A is marked `unavailable`, the system
falls back to **stylometry only**, the run is flagged in the audit log, and confidence is
multiplied by 0.7 — so a single structural signal can never produce a high-confidence label
on its own. The service stays fully runnable and testable offline.

---

## Known limitations

These are real, tested failure modes (see [planning.md §5](planning.md)), not hypotheticals:

- **Repetitive / minimalist text** (e.g. a terse repeated-refrain poem) can read as AI to
  _both_ signals at once — they agree, so the "disagreement lowers confidence" safeguard
  doesn't fire, and the result can be a **confident AI mislabel** (`repetitive_poem` fixture).
- **Lightly-edited AI** that the LLM reads as human passes as **confident human** — once a
  person has smoothed an AI draft, the system has no way to recover its provenance.
- **Stylometry is blind to meaning.** A polished-human-prose miss was traced to the MATTR
  sub-metric and fixed (M5); a residual risk remains for text that is both uniform in sentence
  length and moderate in diversity.

---

## Project layout

```
app.py                 Flask API + rate limiting
scoring.py             signal combination + confidence (self-verifying: python scoring.py)
labels.py              transparency label generation (python labels.py)
db.py                  SQLite: content, audit_log, appeals
signals/llm.py         Signal A — Groq LLM
signals/stylometry.py  Signal B — stylometric heuristics
samples.py             labelled test fixtures
compare_signals.py     run both signals over the fixtures
planning.md            full design rationale (signals, scoring, labels, appeals, edge cases)
```

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

## AI Usage

- One sections where I used AI was to generate potential edge cases so that I could brainstorm possible ways that the system would fail.
  I used this as a basis and expanded upon it to create a more comprehensive set of edge cases like adding repetitive text as a possible edge case.

- Another sections where I used AI was to help me finetune the threshold values. Initially I decided values and then used AI to come up with its own values and used both as a basis to tweak the values until I obtained the final result.
