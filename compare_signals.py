"""Run both detection signals on the shared fixtures and show where they agree/diverge.

Usage:  .venv/bin/python compare_signals.py
This calls the Groq API for the LLM signal (needs GROQ_API_KEY); stylometry is offline.
"""

from samples import SAMPLES
from scoring import score
from signals.llm import classify_with_llm
from signals.stylometry import analyze_stylometry


def fmt(x):
    return f"{x:.2f}" if isinstance(x, (int, float)) else "  -- "


def main():
    header = (
        f"{'sample':<26} {'llm':>5} {'style':>6} {'agree':>6} "
        f"{'p_ai':>6} {'conf':>6}  {'classification':<14} {'tier'}"
    )
    print(header)
    print("-" * len(header))
    for s in SAMPLES:
        llm = classify_with_llm(s["text"])
        style = analyze_stylometry(s["text"])
        result = score(llm, style)
        print(
            f"{s['name']:<26} {fmt(llm.get('p_ai')):>5} {fmt(style['p_ai']):>6} "
            f"{fmt(result['agreement']):>6} {fmt(result['p_ai']):>6} "
            f"{fmt(result['confidence']):>6}  {result['classification']:<14} {result['label_tier']}"
        )
    print(
        "\nReading: 'llm' and 'style' are the two raw signals; where they diverge, 'agree' "
        "drops and pulls 'conf' down — exactly the borderline cases."
    )


if __name__ == "__main__":
    main()
