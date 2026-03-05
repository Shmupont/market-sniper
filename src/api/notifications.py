import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.auth import verify_token

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


class TestEmailRequest(BaseModel):
    to: str
    snipe_id: str | None = None


@router.post("/test-email")
async def test_email(
    body: TestEmailRequest,
    _auth=Depends(verify_token),
):
    """Send a test notification email."""
    from src.notifications.email import send_trigger_email
    from src.agent.brain import AgentResult

    dummy_snipe = {
        "id": body.snipe_id or "test-id",
        "name": "Test Snipe",
        "type": "price",
    }
    dummy_result = AgentResult(
        triggered=True,
        confidence="high",
        summary="Test notification — Market Sniper is configured correctly.",
        data={"price": 149.99, "currency": "USD", "available": True, "url": None, "platform": "Test"},
        tools_used=["web_search"],
        next_action="continue_monitoring",
    )

    success = await send_trigger_email(to=body.to, snipe=dummy_snipe, result=dummy_result)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to send email — check RESEND_API_KEY")
    return {"status": "sent", "to": body.to}
