"""Signal B — Stylometric heuristics (pure Python, no dependencies).

Measures *structural* regularities rather than meaning. AI prose tends to be uniform; human
prose is bursty and irregular. Three sub-metrics (planning.md §1), each mapped to an
"AI-ness" sub-score in [0,1] via a piecewise-linear map from heuristic human/AI reference
ranges, then combined as a weighted mean:

    p_ai_style = 0.45 * burstiness + 0.35 * MATTR + 0.20 * punctuation

The reference ranges below are heuristic and tunable; they encode the direction documented in
planning.md (low variance / mid-band diversity / sparse punctuation palette => AI-leaning).
"""

import re
import statistics

# --- Sub-metric weights (planning.md §1) ---
W_BURSTINESS = 0.45
W_MATTR = 0.35
W_PUNCTUATION = 0.20

# --- Heuristic reference ranges (see module docstring) ---
# Burstiness: stdev of sentence length in words. Uniform (low) => AI.
BURST_AI = 3.0      # <= this stdev reads as fully AI-uniform
BURST_HUMAN = 10.0  # >= this stdev reads as fully human-bursty
# MATTR: a weak discriminator in practice (AI and human prose both cluster ~0.85), so it is
# used as a mild monotonic cue rather than the original "mid-band" tent: unusually LOW lexical
# diversity (heavy repetition / templated text) leans AI-uniform, while rich vocabulary leans
# human. Normal prose (~0.80–0.88) lands near 0 (human) and barely moves the score.
MATTR_AI = 0.55     # <= this diversity reads as AI/uniform-repetitive
MATTR_HUMAN = 0.85  # >= this diversity reads as human
# Punctuation diversity: distinct punctuation types used. Few types => AI.
PUNCT_DISTINCT_AI = 2     # <= this many distinct marks reads as AI
PUNCT_DISTINCT_HUMAN = 6  # >= this many distinct marks reads as human
# Punctuation density: marks per word. Sparse => AI-leaning (weaker secondary cue).
PUNCT_DENSITY_AI = 0.03
PUNCT_DENSITY_HUMAN = 0.12

_PUNCT_TYPES = set(";:—-(),.?!\"'…")
_MATTR_WINDOW = 50


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _linear_ai_score(value: float, ai_point: float, human_point: float) -> float:
    """Map `value` to [0,1] AI-ness; `ai_point` -> 1.0, `human_point` -> 0.0, linear between.

    Works whether ai_point < human_point or the reverse.
    """
    if ai_point == human_point:
        return 0.5
    frac = (value - human_point) / (ai_point - human_point)
    return _clamp01(frac)


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"[.!?]+(?:\s+|$)", text.strip())
    return [p for p in parts if p.strip()]


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", text.lower())


def _burstiness_subscore(sentences: list[str]) -> tuple[float, float]:
    """Return (ai_subscore, sentence_length_stdev). Neutral 0.5 when too few sentences."""
    lengths = [len(_words(s)) for s in sentences]
    lengths = [n for n in lengths if n > 0]
    if len(lengths) < 2:
        return 0.5, 0.0
    stdev = statistics.pstdev(lengths)
    return _linear_ai_score(stdev, BURST_AI, BURST_HUMAN), stdev


def _mattr_subscore(words: list[str]) -> tuple[float, float]:
    """Return (ai_subscore, mattr). Monotonic: low diversity -> AI, high diversity -> human."""
    if not words:
        return 0.5, 0.0
    if len(words) <= _MATTR_WINDOW:
        mattr = len(set(words)) / len(words)
    else:
        ratios = []
        for i in range(len(words) - _MATTR_WINDOW + 1):
            window = words[i : i + _MATTR_WINDOW]
            ratios.append(len(set(window)) / _MATTR_WINDOW)
        mattr = statistics.mean(ratios)
    return _linear_ai_score(mattr, MATTR_AI, MATTR_HUMAN), mattr


def _punctuation_subscore(text: str, n_words: int) -> tuple[float, int, float]:
    """Return (ai_subscore, distinct_types, density). Combines diversity (0.7) + density (0.3)."""
    marks = [c for c in text if c in _PUNCT_TYPES]
    distinct = len(set(marks))
    density = (len(marks) / n_words) if n_words else 0.0
    diversity_ai = _linear_ai_score(distinct, PUNCT_DISTINCT_AI, PUNCT_DISTINCT_HUMAN)
    density_ai = _linear_ai_score(density, PUNCT_DENSITY_AI, PUNCT_DENSITY_HUMAN)
    return 0.7 * diversity_ai + 0.3 * density_ai, distinct, density


def analyze_stylometry(text: str) -> dict:
    """Compute the stylometry signal for `text`.

    Returns:
      {
        "available": True,            # pure-Python; always runs
        "p_ai": float,                # combined stylometry AI-likelihood in [0,1]
        "metrics": {
            "n_words": int, "n_sentences": int,
            "sentence_length_stdev": float, "mattr": float,
            "punct_distinct": int, "punct_density": float,
            "sub_scores": {"burstiness": float, "mattr": float, "punctuation": float},
        },
      }
    """
    sentences = _split_sentences(text)
    words = _words(text)
    n_words = len(words)

    burst_ai, stdev = _burstiness_subscore(sentences)
    mattr_ai, mattr = _mattr_subscore(words)
    punct_ai, punct_distinct, punct_density = _punctuation_subscore(text, n_words)

    p_ai = _clamp01(
        W_BURSTINESS * burst_ai + W_MATTR * mattr_ai + W_PUNCTUATION * punct_ai
    )

    return {
        "available": True,
        "p_ai": p_ai,
        "metrics": {
            "n_words": n_words,
            "n_sentences": len(sentences),
            "sentence_length_stdev": round(stdev, 3),
            "mattr": round(mattr, 3),
            "punct_distinct": punct_distinct,
            "punct_density": round(punct_density, 4),
            "sub_scores": {
                "burstiness": round(burst_ai, 3),
                "mattr": round(mattr_ai, 3),
                "punctuation": round(punct_ai, 3),
            },
        },
    }


if __name__ == "__main__":
    import json

    from samples import SAMPLES

    for s in SAMPLES:
        out = analyze_stylometry(s["text"])
        print(f"\n=== {s['name']}  (expect: {s['expect']}) ===")
        print(f"p_ai_style = {out['p_ai']:.3f}")
        print(json.dumps(out["metrics"], indent=2))
