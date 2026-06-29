"""Confidence scoring — combine both detection signals per planning.md

This module is the single source of truth for the thresholds and formulas in the planning
document. The `__main__` block asserts the implementation matches those numbers exactly.
"""

# --- planning.md -> signal combination weights ---
W_LLM = 0.6
W_STYLE = 0.4

# --- planning.md -> classification thresholds ---
AI_THRESHOLD = 0.65       # p_ai >= this  -> likely_ai
HUMAN_THRESHOLD = 0.35    # p_ai <= this  -> likely_human

# --- planning.md ->  confidence tuning ---
HIGH_CONFIDENCE = 0.55          # tier cutoff 
CONFIDENCE_BOOST_EXPONENT = 0.5  # concave response: confidence rises fast off the fence
LLM_UNAVAILABLE_PENALTY = 0.7
SHORT_TEXT_PENALTY = 0.5
SHORT_TEXT_MIN_SENTENCES = 4
SHORT_TEXT_MIN_WORDS = 50


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def classify(p_ai: float) -> str:
    """Map the combined lean to a label category (planning.md §2 table)."""
    if p_ai >= AI_THRESHOLD:
        return "likely_ai"
    if p_ai <= HUMAN_THRESHOLD:
        return "likely_human"
    return "uncertain"


def combine_p_ai(p_ai_llm, p_ai_style, llm_available: bool) -> float:
    """Weighted combination; falls back to stylometry-only when the LLM is unavailable."""
    if llm_available and p_ai_llm is not None:
        return _clamp01(W_LLM * p_ai_llm + W_STYLE * p_ai_style)
    return _clamp01(p_ai_style)


def compute_confidence(
    p_ai, p_ai_llm, p_ai_style, llm_available, n_sentences, n_words
) -> float:
    """Confidence per planning.md §2 steps 1–7."""
    # 1. decisiveness — distance from the 0.5 fence
    decisiveness = 2 * abs(p_ai - 0.5)

    # 2. concave boost — confidence rises quickly off the fence, then plateaus
    boosted = decisiveness ** CONFIDENCE_BOOST_EXPONENT

    # 3–4. agreement modulation (only meaningful when both signals ran)
    if llm_available and p_ai_llm is not None:
        agreement = 1 - abs(p_ai_llm - p_ai_style)
        confidence = boosted * (0.6 + 0.4 * agreement)
    else:
        # 5. single signal -> no agreement term, then the unavailable penalty applies
        confidence = boosted * LLM_UNAVAILABLE_PENALTY

    # 6. short-text guard
    if n_sentences < SHORT_TEXT_MIN_SENTENCES or n_words < SHORT_TEXT_MIN_WORDS:
        confidence *= SHORT_TEXT_PENALTY

    # 7. clamp
    return _clamp01(confidence)


def label_tier(classification: str, confidence: float) -> str:
    """Combine lean + confidence into one of three tiers (planning.md §2)."""
    if confidence >= HIGH_CONFIDENCE:
        if classification == "likely_ai":
            return "high_confidence_ai"
        if classification == "likely_human":
            return "high_confidence_human"
    return "uncertain"


def agreement_of(p_ai_llm, p_ai_style, llm_available: bool):
    """Signal agreement in [0,1], or None when only one signal ran (for the audit log)."""
    if llm_available and p_ai_llm is not None:
        return 1 - abs(p_ai_llm - p_ai_style)
    return None


def score(llm_result: dict, style_result: dict) -> dict:
    """Run the full combination over both signals' raw outputs.

    `llm_result`   from signals.llm.classify_with_llm
    `style_result` from signals.stylometry.analyze_stylometry
    """
    llm_available = bool(llm_result.get("available")) and llm_result.get("p_ai") is not None
    p_ai_llm = llm_result.get("p_ai")
    p_ai_style = style_result["p_ai"]
    metrics = style_result["metrics"]
    n_sentences = metrics["n_sentences"]
    n_words = metrics["n_words"]

    p_ai = combine_p_ai(p_ai_llm, p_ai_style, llm_available)
    confidence = compute_confidence(
        p_ai, p_ai_llm, p_ai_style, llm_available, n_sentences, n_words
    )
    classification = classify(p_ai)

    return {
        "p_ai": round(p_ai, 4),
        "confidence": round(confidence, 4),
        "classification": classification,
        "label_tier": label_tier(classification, confidence),
        "agreement": agreement_of(p_ai_llm, p_ai_style, llm_available),
        "p_ai_llm": p_ai_llm,
        "p_ai_style": round(p_ai_style, 4),
        "llm_available": llm_available,
    }


if __name__ == "__main__":
    import math

    def approx(a, b):
        return math.isclose(a, b, abs_tol=1e-9)

    # --- classification thresholds (planning.md table) ---
    assert classify(0.65) == "likely_ai"          # boundary is inclusive
    assert classify(0.6499) == "uncertain"
    assert classify(0.35) == "likely_human"        # boundary is inclusive
    assert classify(0.3501) == "uncertain"
    assert classify(0.50) == "uncertain"

    # --- combination weights: 0.6/0.4 ---
    assert approx(combine_p_ai(0.9, 0.5, True), 0.6 * 0.9 + 0.4 * 0.5)  # 0.74
    assert approx(combine_p_ai(0.9, 0.5, False), 0.5)                   # LLM down -> style only

    # --- confidence formula (both signals present): boosted = decisiveness**0.5 ---
    # p_ai=0.74 -> dec=0.48, boosted=0.48**0.5; agreement=1-|0.9-0.5|=0.6 -> *(0.6+0.4*0.6)
    c = compute_confidence(0.74, 0.9, 0.5, True, n_sentences=6, n_words=120)
    assert approx(c, (0.48 ** 0.5) * (0.6 + 0.4 * 0.6)), c

    # --- short-text guard halves it (<4 sentences or <50 words) ---
    expected_full = (0.48 ** 0.5) * (0.6 + 0.4 * 0.6)
    c_short = compute_confidence(0.74, 0.9, 0.5, True, n_sentences=2, n_words=120)
    assert approx(c_short, expected_full * 0.5), c_short
    c_short_words = compute_confidence(0.74, 0.9, 0.5, True, n_sentences=6, n_words=40)
    assert approx(c_short_words, expected_full * 0.5), c_short_words

    # --- LLM unavailable: p_ai = style, confidence = boosted * 0.7, no agreement term ---
    # p_ai=0.8 -> dec=0.6, boosted=0.6**0.5 -> *0.7
    c_nollm = compute_confidence(0.8, None, 0.8, False, n_sentences=6, n_words=120)
    assert approx(c_nollm, (0.6 ** 0.5) * 0.7), c_nollm

    # --- tier cutoff at 0.55 ---
    assert label_tier("likely_ai", 0.55) == "high_confidence_ai"
    assert label_tier("likely_ai", 0.5499) == "uncertain"
    assert label_tier("likely_human", 0.85) == "high_confidence_human"
    assert label_tier("uncertain", 0.99) == "uncertain"  # lean uncertain stays uncertain

    # --- a near-fence lean can never be high confidence (decisiveness gate) ---
    c_fence = compute_confidence(0.52, 0.52, 0.52, True, n_sentences=10, n_words=200)
    assert c_fence < HIGH_CONFIDENCE, c_fence

    # --- calibration goals: clear cases clear the bar, borderline cases do not ---
    # (signal values observed from compare_signals.py on the canonical fixtures)
    c_clear_ai = compute_confidence(0.81, 0.90, 0.68, True, n_sentences=4, n_words=57)
    assert c_clear_ai >= HIGH_CONFIDENCE, c_clear_ai
    c_clear_human = compute_confidence(0.30, 0.20, 0.44, True, n_sentences=5, n_words=63)
    assert c_clear_human >= HIGH_CONFIDENCE, c_clear_human
    c_border_edit = compute_confidence(0.35, 0.20, 0.58, True, n_sentences=4, n_words=52)
    assert c_border_edit < HIGH_CONFIDENCE, c_border_edit

    print("scoring.py — all threshold/formula assertions match planning.md §1–§2 ✓")
