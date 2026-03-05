import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth import verify_token

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/snipes", tags=["runs"])


@router.get("/{snipe_id}/runs")
async def get_runs(
    snipe_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    _auth=Depends(verify_token),
):
    from src.db.queries import get_runs_for_snipe, get_snipe_by_id

    if not get_snipe_by_id(snipe_id):
        raise HTTPException(status_code=404, detail="Snipe not found")

    try:
        return get_runs_for_snipe(snipe_id, limit=limit)
    except Exception as exc:
        log.error("api.get_runs.error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
