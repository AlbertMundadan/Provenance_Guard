"""SQLite persistence for Provenance Guard.

Two tables in M3:
  - content:    one row per submission, tracks status (used by appeals in M5)
  - audit_log:  one structured entry per attribution decision (and, later, appeals)

The audit_log carries the decision snapshot directly (a pragmatic simplification of the
4-table design in planning.md). `signals_json` leaves room for M4's stylometry/p_ai/agreement
detail without a schema migration.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).with_name("provenance.db")


def utc_now_iso() -> str:
    """UTC timestamp as ISO-8601 with a trailing 'Z' (e.g. 2025-04-01T14:32:10.123Z)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS content (
                content_id  TEXT PRIMARY KEY,
                creator_id  TEXT NOT NULL,
                text        TEXT NOT NULL,
                status      TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id    TEXT NOT NULL,
                creator_id    TEXT,
                timestamp     TEXT NOT NULL,
                event_type    TEXT NOT NULL DEFAULT 'classification',
                attribution   TEXT,
                confidence    REAL,
                llm_score     REAL,
                style_score   REAL,
                p_ai          REAL,
                agreement     REAL,
                status        TEXT,
                signals_json  TEXT,
                appeal_id         INTEGER,
                appeal_reasoning  TEXT
            );

            CREATE TABLE IF NOT EXISTS appeals (
                appeal_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id        TEXT NOT NULL,
                decision_id       INTEGER,
                creator_reasoning TEXT NOT NULL,
                status            TEXT NOT NULL,
                filed_at          TEXT NOT NULL
            );
            """
        )
        _ensure_columns(conn)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add later-milestone columns to a pre-existing audit_log table (lightweight migration)."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(audit_log)")}
    for col, decl in (
        ("style_score", "REAL"),
        ("p_ai", "REAL"),
        ("agreement", "REAL"),
        ("appeal_id", "INTEGER"),
        ("appeal_reasoning", "TEXT"),
    ):
        if col not in existing:
            conn.execute(f"ALTER TABLE audit_log ADD COLUMN {col} {decl}")


def insert_submission(content_row: dict, audit_row: dict) -> None:
    """Persist a submission and its audit entry in a single transaction.

    `content_row` keys: content_id, creator_id, text, status, created_at
    `audit_row`   keys: content_id, creator_id, timestamp, attribution,
                        confidence, llm_score, status, signals (dict -> signals_json)
    """
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO content (content_id, creator_id, text, status, created_at)
            VALUES (:content_id, :creator_id, :text, :status, :created_at)
            """,
            content_row,
        )
        _insert_audit(conn, audit_row)


def _insert_audit(conn: sqlite3.Connection, audit_row: dict) -> None:
    """Insert one structured audit_log entry. Used for both classifications and appeals."""
    conn.execute(
        """
        INSERT INTO audit_log
            (content_id, creator_id, timestamp, event_type, attribution, confidence,
             llm_score, style_score, p_ai, agreement, status, signals_json,
             appeal_id, appeal_reasoning)
        VALUES
            (:content_id, :creator_id, :timestamp, :event_type, :attribution, :confidence,
             :llm_score, :style_score, :p_ai, :agreement, :status, :signals_json,
             :appeal_id, :appeal_reasoning)
        """,
        {
            "content_id": audit_row["content_id"],
            "creator_id": audit_row.get("creator_id"),
            "timestamp": audit_row["timestamp"],
            "event_type": audit_row.get("event_type", "classification"),
            "attribution": audit_row.get("attribution"),
            "confidence": audit_row.get("confidence"),
            "llm_score": audit_row.get("llm_score"),
            "style_score": audit_row.get("style_score"),
            "p_ai": audit_row.get("p_ai"),
            "agreement": audit_row.get("agreement"),
            "status": audit_row.get("status"),
            "signals_json": json.dumps(audit_row["signals"])
            if audit_row.get("signals") is not None
            else None,
            "appeal_id": audit_row.get("appeal_id"),
            "appeal_reasoning": audit_row.get("appeal_reasoning"),
        },
    )


def get_latest_decision(content_id: str) -> dict | None:
    """Return the most recent classification audit entry for a content_id, or None."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM audit_log
            WHERE content_id = ? AND event_type = 'classification'
            ORDER BY id DESC LIMIT 1
            """,
            (content_id,),
        ).fetchone()
    return dict(row) if row else None


def record_appeal(content_id: str, creator_reasoning: str, timestamp: str) -> dict | None:
    """File an appeal: create appeal row, flip content status, log it alongside the decision.

    Returns {appeal_id, status} on success, or None if the content_id has no prior decision.
    No automated re-classification (per planning.md §4).
    """
    decision = get_latest_decision(content_id)
    if decision is None:
        return None

    new_status = "under_review"
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO appeals
                (content_id, decision_id, creator_reasoning, status, filed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (content_id, decision["id"], creator_reasoning, new_status, timestamp),
        )
        appeal_id = cur.lastrowid

        # Flip the content's status.
        conn.execute(
            "UPDATE content SET status = ? WHERE content_id = ?", (new_status, content_id)
        )

        # Log the appeal alongside a snapshot of the original decision.
        _insert_audit(
            conn,
            {
                "content_id": content_id,
                "creator_id": None,
                "timestamp": timestamp,
                "event_type": "appeal_filed",
                "attribution": decision.get("attribution"),
                "confidence": decision.get("confidence"),
                "llm_score": decision.get("llm_score"),
                "style_score": decision.get("style_score"),
                "p_ai": decision.get("p_ai"),
                "agreement": decision.get("agreement"),
                "status": new_status,
                "signals": {"original_decision_id": decision["id"]},
                "appeal_id": appeal_id,
                "appeal_reasoning": creator_reasoning,
            },
        )

    return {"appeal_id": appeal_id, "status": new_status}


def get_appeals(status: str | None = None) -> list[dict]:
    """Reviewer queue: appeals joined to their original decision snapshot, newest first."""
    query = (
        """
        SELECT a.appeal_id, a.content_id, a.decision_id, a.creator_reasoning,
               a.status, a.filed_at, c.creator_id, substr(c.text, 1, 160) AS excerpt,
               d.attribution, d.confidence, d.llm_score, d.style_score, d.p_ai, d.agreement
        FROM appeals a
        LEFT JOIN content c ON c.content_id = a.content_id
        LEFT JOIN audit_log d ON d.id = a.decision_id
        """
    )
    params: tuple = ()
    if status:
        query += " WHERE a.status = ?"
        params = (status,)
    query += " ORDER BY a.appeal_id DESC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_log(limit: int = 50) -> list[dict]:
    """Return the most recent audit-log entries (newest first) as plain dicts."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    entries = []
    for row in rows:
        entry = dict(row)
        if entry.get("signals_json"):
            entry["signals"] = json.loads(entry.pop("signals_json"))
        else:
            entry.pop("signals_json", None)
            entry["signals"] = None
        entries.append(entry)
    return entries
