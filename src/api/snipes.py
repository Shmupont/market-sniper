import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.auth import verify_token

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/snipes", tags=["snipes"])


class CreateSnipeRequest(BaseModel):
    name: str
    type: str  # restock | price | listing | url_change | arbitrage
    condition_type: str
    condition_value: dict[str, Any] = {}
    description: str | None = None
    target_url: str | None = None
    search_query: str | None = None
    platforms: list[str] = []
    interval_minutes: int = 10
    notify_email: str | None = None
    notify_inapp: bool = True
    notify_webhook: str | None = None
    notify_on_every_run: bool = False
    credits_per_run: int = 5
    user_id: str | None = None
    swarm_job_id: str | None = None
    expires_at: str | None = None


class UpdateSnipeRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    interval_minutes: int | None = None
    notify_email: str | None = None
    notify_inapp: bool | None = None
    notify_webhook: str | None = None
    condition_value: dict[str, Any] | None = None


@router.post("", status_code=201)
async def create_snipe(
    body: CreateSnipeRequest,
    _auth=Depends(verify_token),
):
    from src.db.queries import create_snipe as db_create

    data = body.model_dump(exclude_none=True)
    try:
        snipe = db_create(data)
        return snipe
    except Exception as exc:
        log.error("api.create_snipe.error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("")
async def list_snipes(
    user_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    _auth=Depends(verify_token),
):
    from src.db.queries import list_snipes as db_list

    try:
        return db_list(user_id=user_id, status=status)
    except Exception as exc:
        log.error("api.list_snipes.error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{snipe_id}")
async def get_snipe(
    snipe_id: str,
    _auth=Depends(verify_token),
):
    from src.db.queries import get_snipe_by_id

    snipe = get_snipe_by_id(snipe_id)
    if not snipe:
        raise HTTPException(status_code=404, detail="Snipe not found")
    return snipe


@router.patch("/{snipe_id}")
async def update_snipe(
    snipe_id: str,
    body: UpdateSnipeRequest,
    _auth=Depends(verify_token),
):
    from src.db.queries import get_snipe_by_id, update_snipe as db_update

    if not get_snipe_by_id(snipe_id):
        raise HTTPException(status_code=404, detail="Snipe not found")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return db_update(snipe_id, updates)
    except Exception as exc:
        log.error("api.update_snipe.error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{snipe_id}", status_code=204)
async def delete_snipe(
    snipe_id: str,
    _auth=Depends(verify_token),
):
    from src.db.queries import delete_snipe as db_delete

    deleted = db_delete(snipe_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Snipe not found")


@router.post("/{snipe_id}/run")
async def trigger_run(
    snipe_id: str,
    _auth=Depends(verify_token),
):
    """Manually trigger an immediate run of a snipe."""
    from src.db.queries import get_snipe_by_id

    snipe = get_snipe_by_id(snipe_id)
    if not snipe:
        raise HTTPException(status_code=404, detail="Snipe not found")

    try:
        from src.worker.tasks import run_snipe_task
        task = run_snipe_task.delay(snipe_id)
        return {"task_id": task.id, "status": "queued"}
    except Exception as exc:
        # If Celery isn't running, run inline
        log.warning("api.trigger_run.celery_unavailable", error=str(exc))
        from src.agent.brain import run_snipe
        from src.db.queries import create_run

        import time
        start = time.time()
        result = await run_snipe(snipe)
        elapsed_ms = int((time.time() - start) * 1000)

        run_status = "triggered" if result.triggered else ("error" if result.error else "success")
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
        }
        run = create_run(snipe_id, run_data)
        return {"status": "complete", "run": run, "result": result.raw_output}
