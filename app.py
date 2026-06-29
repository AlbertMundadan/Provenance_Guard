"""Provenance Guard — Flask API.

  POST /submit   -> classify text with both signals, persist + audit, return result + label
  POST /appeal   -> file a creator appeal: status -> under_review, logged with the decision
  GET  /appeals  -> reviewer queue (decision snapshot + creator reasoning)
  GET  /log      -> recent audit-log entries (documentation/grading visibility; no auth here)
"""

import uuid

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from db import (
    get_appeals,
    get_log,
    init_db,
    insert_submission,
    record_appeal,
    utc_now_iso,
)
from labels import generate_label
from scoring import score
from signals.llm import classify_with_llm
from signals.stylometry import analyze_stylometry

app = Flask(__name__)
init_db()

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.post("/submit")
@limiter.limit("10 per minute; 100 per hour")
def submit():
    body = request.get_json(silent=True) or {}
    text = body.get("text")
    creator_id = body.get("creator_id")

    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Field 'text' is required and must be a non-empty string."}), 400
    if not isinstance(creator_id, str) or not creator_id.strip():
        return jsonify({"error": "Field 'creator_id' is required and must be a non-empty string."}), 400

    content_id = str(uuid.uuid4())
    timestamp = utc_now_iso()
    status = "classified"

    # Run both signals and combine them per planning.md §1–§2.
    llm_result = classify_with_llm(text)
    style_result = analyze_stylometry(text)
    result = score(llm_result, style_result)

    insert_submission(
        content_row={
            "content_id": content_id,
            "creator_id": creator_id,
            "text": text,
            "status": status,
            "created_at": timestamp,
        },
        audit_row={
            "content_id": content_id,
            "creator_id": creator_id,
            "timestamp": timestamp,
            "event_type": "classification",
            "attribution": result["classification"],
            "confidence": result["confidence"],
            "llm_score": result["p_ai_llm"],
            "style_score": result["p_ai_style"],
            "p_ai": result["p_ai"],
            "agreement": result["agreement"],
            "status": status,
            "signals": {"llm": llm_result, "stylometry": style_result, "scoring": result},
        },
    )

    label = generate_label(result["label_tier"], result["confidence"])

    return jsonify(
        {
            "content_id": content_id,
            "attribution": result["classification"],
            "confidence": result["confidence"],
            "p_ai": result["p_ai"],
            "label_tier": result["label_tier"],
            "label": label["text"],
            "label_headline": label["headline"],
            "status": status,
            "signal_detail": {
                "llm": {
                    "available": llm_result.get("available"),
                    "score": result["p_ai_llm"],
                    "rationale": llm_result.get("rationale"),
                    "error": llm_result.get("error"),
                },
                "stylometry": {
                    "score": result["p_ai_style"],
                    "metrics": style_result["metrics"],
                },
                "agreement": result["agreement"],
            },
        }
    )


@app.post("/appeal")
@limiter.limit("5 per minute; 50 per hour")
def appeal():
    body = request.get_json(silent=True) or {}
    content_id = body.get("content_id")
    creator_reasoning = body.get("creator_reasoning")

    if not isinstance(content_id, str) or not content_id.strip():
        return jsonify({"error": "Field 'content_id' is required."}), 400
    if not isinstance(creator_reasoning, str) or not creator_reasoning.strip():
        return jsonify({"error": "Field 'creator_reasoning' is required and must be non-empty."}), 400

    outcome = record_appeal(content_id, creator_reasoning, utc_now_iso())
    if outcome is None:
        return jsonify({"error": f"No classified content found for content_id '{content_id}'."}), 404

    return jsonify(
        {
            "message": "Appeal received. The content is now under review by a human moderator.",
            "appeal_id": outcome["appeal_id"],
            "content_id": content_id,
            "status": outcome["status"],
        }
    ), 201


@app.get("/appeals")
def appeals_queue():
    status = request.args.get("status")
    return jsonify({"appeals": get_appeals(status)})


@app.get("/log")
def log():
    return jsonify({"entries": get_log()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
