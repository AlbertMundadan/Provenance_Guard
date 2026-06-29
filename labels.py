"""Transparency label generation — maps a scoring result to reader-facing label text.

The three variants are the verbatim text from planning.md §3. The label shown depends on the
`label_tier` (which is itself driven by confidence), so a low-confidence result and a
high-confidence result never produce the same text.
"""

_HIGH_CONFIDENCE_AI = (
    "Likely AI-generated. Our analysis found strong, consistent signs that this text was "
    "produced by an AI system. Confidence: High (~{conf}%). This is an automated assessment, "
    "not a certainty — automated checks can be wrong. If you wrote this yourself, you can appeal."
)

_HIGH_CONFIDENCE_HUMAN = (
    "Likely human-written. Our analysis found consistent signs that this text was written by a "
    "person. Confidence: High (~{conf}%). This is an automated assessment, not a guarantee of "
    "authorship. If you disagree with this label, you can appeal."
)

_UNCERTAIN = (
    "Not enough certainty to label. Our analysis was mixed — the signals disagreed or were too "
    "weak to call. We can't confidently say whether this was written by a person or generated "
    "by AI. Confidence: Low ({conf}%). We're showing this openly rather than guessing. You can "
    "appeal if you'd like a human to review it."
)

_TEMPLATES = {
    "high_confidence_ai": _HIGH_CONFIDENCE_AI,
    "high_confidence_human": _HIGH_CONFIDENCE_HUMAN,
    "uncertain": _UNCERTAIN,
}

_HEADLINES = {
    "high_confidence_ai": "Likely AI-generated",
    "high_confidence_human": "Likely human-written",
    "uncertain": "Not enough certainty to label",
}


def generate_label(label_tier: str, confidence: float) -> dict:
    """Return the transparency label for a given tier + confidence.

    `label_tier` is one of: high_confidence_ai | high_confidence_human | uncertain
    (as produced by scoring.label_tier). Unknown tiers fall back to the uncertain variant.
    """
    tier = label_tier if label_tier in _TEMPLATES else "uncertain"
    conf_pct = round(confidence * 100)
    return {
        "tier": tier,
        "headline": _HEADLINES[tier],
        "text": _TEMPLATES[tier].format(conf=conf_pct),
    }


if __name__ == "__main__":
    # Verify all three variants are produced and match planning.md §3.
    cases = [
        ("high_confidence_ai", 0.88),
        ("high_confidence_human", 0.91),
        ("uncertain", 0.41),
    ]
    for tier, conf in cases:
        label = generate_label(tier, conf)
        print(f"\n=== {tier}  (conf={conf}) ===")
        print(label["text"])

    # Spot-check the rendered numbers and key phrases.
    assert "~88%" in generate_label("high_confidence_ai", 0.88)["text"]
    assert "Likely AI-generated" in generate_label("high_confidence_ai", 0.88)["text"]
    assert "~91%" in generate_label("high_confidence_human", 0.91)["text"]
    assert "Likely human-written" in generate_label("high_confidence_human", 0.91)["text"]
    assert "Low (41%)" in generate_label("uncertain", 0.41)["text"]
    assert "Not enough certainty" in generate_label("uncertain", 0.41)["text"]
    # Unknown tier degrades to uncertain.
    assert generate_label("bogus", 0.5)["tier"] == "uncertain"
    print("\nlabels.py — all three variants reachable and match planning.md §3 ✓")
