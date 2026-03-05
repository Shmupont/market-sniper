import json
from datetime import datetime
from typing import Any

import psycopg2.extras
import structlog

from src.db.connection import get_db

log = structlog.get_logger(__name__)


def _row_to_dict(cursor, row) -> dict:
    """Convert a psycopg2 row to a plain dict using cursor description."""
    cols = [desc[0] for desc in cursor.description]
    return dict(zip(cols, row))


def get_due_snipes(limit: int = 50) -> list[dict]:
    """Return active snipes whose next_run_at is in the past."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM snipes
            WHERE status = 'active'
              AND next_run_at <= NOW()
            ORDER BY next_run_at ASC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]


def get_snipe_by_id(snipe_id: str) -> dict | None:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM snipes WHERE id = %s", (snipe_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_dict(cur, row)


def create_snipe(data: dict) -> dict:
    cols = [
        "user_id", "swarm_job_id", "name", "description", "type", "status",
        "target_url", "search_query", "platforms", "condition_type",
        "condition_value", "interval_minutes", "next_run_at", "expires_at",
        "notify_email", "notify_inapp", "notify_webhook", "notify_on_every_run",
        "credits_per_run",
    ]
    present = {k: v for k, v in data.items() if k in cols}
    if "condition_value" in present and isinstance(present["condition_value"], dict):
        present["condition_value"] = json.dumps(present["condition_value"])
    if "platforms" in present and isinstance(present["platforms"], list):
        present["platforms"] = present["platforms"]  # psycopg2 handles lists natively

    keys = list(present.keys())
    values = [present[k] for k in keys]
    placeholders = ", ".join(["%s"] * len(keys))
    col_str = ", ".join(keys)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO snipes ({col_str}) VALUES ({placeholders}) RETURNING *",
            values,
        )
        row = cur.fetchone()
        return _row_to_dict(cur, row)


def update_snipe(snipe_id: str, updates: dict) -> dict:
    allowed = [
        "name", "description", "status", "target_url", "search_query",
        "platforms", "condition_type", "condition_value", "interval_minutes",
        "next_run_at", "last_run_at", "expires_at", "notify_email",
        "notify_inapp", "notify_webhook", "notify_on_every_run",
    ]
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return get_snipe_by_id(snipe_id)

    if "condition_value" in filtered and isinstance(filtered["condition_value"], dict):
        filtered["condition_value"] = json.dumps(filtered["condition_value"])

    filtered["updated_at"] = datetime.utcnow()
    set_clause = ", ".join(f"{k} = %s" for k in filtered)
    values = list(filtered.values()) + [snipe_id]

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE snipes SET {set_clause} WHERE id = %s RETURNING *",
            values,
        )
        row = cur.fetchone()
        return _row_to_dict(cur, row)


def pause_snipe(snipe_id: str) -> None:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE snipes SET status = 'paused', updated_at = NOW() WHERE id = %s",
            (snipe_id,),
        )


def mark_snipe_triggered(snipe_id: str) -> None:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE snipes SET status = 'triggered', updated_at = NOW() WHERE id = %s",
            (snipe_id,),
        )


def create_run(snipe_id: str, result: dict) -> dict:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO snipe_runs (
                snipe_id, status, duration_ms, triggered, confidence,
                trigger_summary, raw_result, tools_used, tier_used,
                credits_charged, error_message, error_type
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                snipe_id,
                result.get("status", "success"),
                result.get("duration_ms"),
                result.get("triggered", False),
                result.get("confidence"),
                result.get("trigger_summary"),
                json.dumps(result.get("raw_result")) if result.get("raw_result") else None,
                result.get("tools_used", []),
                result.get("tier_used"),
                result.get("credits_charged", 0),
                result.get("error_message"),
                result.get("error_type"),
            ),
        )
        row = cur.fetchone()
        result_dict = _row_to_dict(cur, row)

        # Update snipe stats
        cur.execute(
            """
            UPDATE snipes
            SET total_runs = total_runs + 1,
                total_spend_credits = total_spend_credits + %s,
                last_run_at = NOW(),
                updated_at = NOW()
            WHERE id = %s
            """,
            (result.get("credits_charged", 0), snipe_id),
        )

        return result_dict


def get_runs_for_snipe(snipe_id: str, limit: int = 20) -> list[dict]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM snipe_runs WHERE snipe_id = %s ORDER BY ran_at DESC LIMIT %s",
            (snipe_id, limit),
        )
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]


def update_snipe_next_run(snipe_id: str, next_run_at: datetime) -> None:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE snipes SET next_run_at = %s, updated_at = NOW() WHERE id = %s",
            (next_run_at, snipe_id),
        )


def delete_snipe(snipe_id: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM snipes WHERE id = %s", (snipe_id,))
        return cur.rowcount > 0


def list_snipes(user_id: str | None = None, status: str | None = None) -> list[dict]:
    conditions = []
    params: list[Any] = []

    if user_id:
        conditions.append("user_id = %s")
        params.append(user_id)
    if status:
        conditions.append("status = %s")
        params.append(status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM snipes {where} ORDER BY created_at DESC", params)
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]
