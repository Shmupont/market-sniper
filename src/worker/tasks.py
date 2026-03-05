import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx
import structlog

from src.worker.celery_app import app

log = structlog.get_logger(__name__)


@app.task(name="src.worker.tasks.dispatch_due_snipes")
def dispatch_due_snipes():
    """
    Runs every 30s via Celery Beat.
    1. Query DB for snipes WHERE status='active' AND next_run_at <= NOW()
    2. For each due snipe, dispatch run_snipe_task.delay(snipe_id)
    3. Update next_run_at immediately to prevent double-dispatch
    """
    from src.db.queries import get_due_snipes, update_snipe_next_run

    try:
        due = get_due_snipes(limit=50)
        log.info("dispatcher.due_snipes", count=len(due))

        for snipe in due:
            snipe_id = str(snipe["id"])
            interval = snipe.get("interval_minutes", 10)

            # Advance next_run_at immediately to prevent double-dispatch
            next_run = datetime.now(timezone.utc) + timedelta(minutes=interval)
            update_snipe_next_run(snipe_id, next_run)

            # Dispatch the run task
            run_snipe_task.delay(snipe_id)
            log.info("dispatcher.dispatched", snipe_id=snipe_id, next_run=next_run.isoformat())

    except Exception as exc:
        log.error("dispatcher.error", error=str(exc))
        raise


@app.task(
    name="src.worker.tasks.run_snipe_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def run_snipe_task(self, snipe_id: str):
    """
    Executes a single snipe run.
    1. Load snipe from DB
    2. Call brain.run_snipe(snipe)
    3. Log the run result to snipe_runs
    4. Charge credits (call SWARM API if swarm_job_id set)
    5. If triggered: send notifications
    6. Update snipe status and next_run_at
    """
    from src.db.queries import (
        create_run,
        get_snipe_by_id,
        mark_snipe_triggered,
        update_snipe,
    )

    start_time = time.time()
    trace_id = f"{snipe_id[:8]}-{int(start_time)}"
    log = structlog.get_logger(__name__).bind(snipe_id=snipe_id, trace_id=trace_id)

    try:
        snipe = get_snipe_by_id(snipe_id)
        if not snipe:
            log.warning("run_snipe.not_found")
            return

        if snipe.get("status") != "active":
            log.info("run_snipe.skipped", status=snipe.get("status"))
            return

        # Run the agent
        from src.agent.brain import run_snipe
        result = asyncio.run(run_snipe(snipe))

        elapsed_ms = int((time.time() - start_time) * 1000)

        # Determine run status
        if result.error:
            run_status = "error"
        elif result.triggered:
            run_status = "triggered"
        else:
            run_status = "success"

        # Log the run
        run_data = {
            "status": run_status,
            "duration_ms": elapsed_ms,
            "triggered": result.triggered,
            "confidence": result.confidence,
            "trigger_summary": result.summary if result.triggered else None,
            "raw_result": result.raw_output,
            "tools_used": result.tools_used,
            "tier_used": result.tier_used,
            "credits_charged": snipe.get("credits_per_run", 5),
            "error_message": result.error,
            "error_type": "agent_error" if result.error else None,
        }
        create_run(snipe_id, run_data)

        # Handle trigger
        if result.triggered:
            log.info("run_snipe.triggered", summary=result.summary)
            mark_snipe_triggered(snipe_id)

            # Send notifications
            _send_notifications(snipe, result)

            # Charge credits via SWARM if applicable
            if snipe.get("swarm_job_id"):
                _charge_swarm_credits(snipe, snipe.get("credits_per_run", 5))

        log.info(
            "run_snipe.complete",
            triggered=result.triggered,
            confidence=result.confidence,
            duration_ms=elapsed_ms,
        )

    except Exception as exc:
        elapsed_ms = int((time.time() - start_time) * 1000)
        log.error("run_snipe.error", error=str(exc))

        try:
            create_run(snipe_id, {
                "status": "error",
                "duration_ms": elapsed_ms,
                "triggered": False,
                "error_message": str(exc),
                "error_type": type(exc).__name__,
                "credits_charged": 0,
            })
        except Exception as inner:
            log.error("run_snipe.log_error_failed", error=str(inner))

        raise self.retry(exc=exc)


def _send_notifications(snipe: dict, result) -> None:
    """Send notifications for a triggered snipe."""
    from src.notifications.email import send_trigger_email

    notify_email = snipe.get("notify_email")
    if notify_email:
        try:
            asyncio.run(send_trigger_email(to=notify_email, snipe=snipe, result=result))
            log.info("notification.email_sent", snipe_id=snipe.get("id"), to=notify_email)
        except Exception as exc:
            log.error("notification.email_failed", error=str(exc))

    webhook_url = snipe.get("notify_webhook")
    if webhook_url:
        try:
            _send_webhook(webhook_url, snipe, result)
        except Exception as exc:
            log.error("notification.webhook_failed", error=str(exc))


def _send_webhook(url: str, snipe: dict, result) -> None:
    """POST trigger notification to a webhook URL."""
    payload = {
        "event": "snipe_triggered",
        "snipe_id": str(snipe.get("id")),
        "snipe_name": snipe.get("name"),
        "triggered": result.triggered,
        "confidence": result.confidence,
        "summary": result.summary,
        "data": result.data,
    }
    with httpx.Client(timeout=10) as client:
        client.post(url, json=payload)


def _charge_swarm_credits(snipe: dict, credits: int) -> None:
    """Notify SWARM to charge credits for a job run."""
    import os
    swarm_url = os.getenv("SWARM_API_URL", "https://api.openswarm.world")
    agent_key = os.getenv("SWARM_AGENT_API_KEY", "")
    if not agent_key:
        return

    payload = {
        "job_id": str(snipe.get("swarm_job_id")),
        "credits": credits,
        "reason": f"Snipe run: {snipe.get('name')}",
    }
    try:
        with httpx.Client(timeout=10) as client:
            client.post(
                f"{swarm_url}/v1/jobs/charge",
                json=payload,
                headers={"X-Agent-Key": agent_key},
            )
    except Exception as exc:
        log.warning("swarm.charge_failed", error=str(exc))
