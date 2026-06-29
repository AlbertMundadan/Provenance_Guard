"""Canonical test fixtures shared by both signals' self-tests and the comparison script.

Two "clear" cases (should classify confidently) and two "borderline" cases (should land in
the uncertain band with low confidence — a confident call either way would be a failure).
See planning.md §2 ("How meaningfulness will be tested") and §5 (edge cases).
"""

SAMPLES = [
    {
        "name": "clearly_ai",
        "expect": "likely_ai (high confidence)",
        "text": (
            "Artificial intelligence represents a transformative paradigm shift in modern "
            "society. It is important to note that while the benefits of AI are numerous, it "
            "is equally essential to consider the ethical implications. Furthermore, "
            "stakeholders across various sectors must collaborate to ensure responsible "
            "deployment. Ultimately, a balanced approach will yield the most sustainable "
            "outcomes for all parties involved."
        ),
    },
    {
        "name": "clearly_human",
        "expect": "likely_human (high confidence)",
        "text": (
            "ok so i finally tried that new ramen place downtown and honestly? underwhelming. "
            "the broth was fine but they put WAY too much sodium in it and i was thirsty for "
            "like three hours after. my friend got the spicy version and said it was better. "
            "probably won't go back unless someone drags me there — maybe for the gyoza, those "
            "were actually decent."
        ),
    },
    {
        "name": "borderline_formal_human",
        "expect": "uncertain (signals should disagree -> low confidence)",
        "text": (
            "The relationship between monetary policy and asset price inflation has been "
            "extensively studied in the literature. Central banks face a fundamental tension "
            "between their mandate for price stability and the unintended consequences of "
            "prolonged low interest rates on equity and real estate valuations. Empirical "
            "evidence on the magnitude of these effects remains contested."
        ),
    },
    {
        "name": "borderline_edited_ai",
        "expect": "uncertain (hybrid authorship -> near the fence)",
        "text": (
            "I've been thinking a lot about remote work lately. There are genuine tradeoffs — "
            "flexibility and no commute on one side, isolation and blurred work-life boundaries "
            "on the other. Studies show productivity varies widely by individual and role type. "
            "Personally, I get more done at home, but I miss the hallway conversations."
        ),
    },
    # --- extra cases to check the calibration generalises (not just the 4 above) ---
    {
        "name": "clearly_ai_2",
        "expect": "likely_ai (high confidence)",
        "text": (
            "In today's rapidly evolving digital landscape, businesses must leverage "
            "cutting-edge technologies to remain competitive. It is crucial to recognize that "
            "digital transformation is not merely a trend but a fundamental necessity. "
            "Organizations should prioritize a customer-centric approach while simultaneously "
            "optimizing operational efficiency. By embracing innovation, companies can unlock "
            "unprecedented opportunities for sustainable growth and long-term success."
        ),
    },
    {
        "name": "clearly_human_2",
        "expect": "likely_human (high confidence)",
        "text": (
            "My grandmother kept her recipes in a battered tin box, half of them written on the "
            "backs of envelopes. None of them had real measurements — a 'handful' of this, a "
            "'splash' of that. When she died we tried to recreate her bread and it came out "
            "wrong every single time. Turns out the secret ingredient was just her hands, "
            "fifty years of knowing exactly when the dough felt right. I still have the tin box."
        ),
    },
    {
        "name": "repetitive_poem",
        "expect": "edge case (planning §5): repetition may fool stylometry; should stay uncertain",
        "text": (
            "I do not sleep. I do not sleep at all. I count the hours and I count them slow. "
            "The night is long and I do not sleep. I wait for light and I do not sleep. "
            "I count the hours. I count them slow. I do not sleep at all."
        ),
    },
]
