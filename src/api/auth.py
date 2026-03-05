import os

from fastapi import HTTPException, Request


async def verify_token(request: Request) -> dict:
    """
    Validate the request token.
    Accepts:
    - Authorization: Bearer {token} — SWARM user tokens
    - X-Agent-Key: swrm_agent_* — SWARM agent keys for automated runs
    """
    environment = os.getenv("ENVIRONMENT", "development")

    # In development, allow unauthenticated requests
    if environment == "development":
        return {"user_id": None, "type": "dev"}

    agent_key = request.headers.get("X-Agent-Key", "")
    if agent_key.startswith("swrm_agent_"):
        return {"user_id": None, "type": "agent", "key": agent_key}

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token:
            # TODO: validate against SWARM user tokens API
            return {"user_id": None, "type": "user", "token": token}

    raise HTTPException(status_code=401, detail="Missing or invalid authentication")
