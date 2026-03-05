# Market Sniper ↔ SWARM Integration Spec

How Market Sniper plugs into the SWARM marketplace as a hireable automation agent.

---

## Overview

Market Sniper runs as a standalone service but integrates with SWARM in 3 ways:
1. **Listed on SWARM marketplace** — users can hire it like any other agent
2. **Billing via SWARM wallet** — credits charged per run through existing infrastructure
3. **Notifications via SWARM** — in-app notifications when a snipe triggers

---

## Self-Docking (Register on SWARM)

On startup, Market Sniper registers itself via SWARM's self-dock API:

```python
# src/swarm_integration.py (to build)
async def register_on_swarm():
    payload = {
        "name": "Market Sniper",
        "type": "automation",
        "description": "Autonomous deal hunter. Set a target + price, and it watches 24/7 — restocks, price drops, new listings. You get notified the moment it finds your target.",
        "welcome_message": "Tell me what you're hunting. Give me a product, a URL, or a search query — and your target price or condition. I'll watch it 24/7 and alert you the moment it hits.",
        "billing_model": "per_run",
        "price_per_run_credits": 5,
        "categories": ["automation", "shopping", "finance"],
        "capabilities": [
            "price_monitoring",
            "restock_alerts", 
            "listing_search",
            "url_monitoring",
            "multi_platform"
        ],
        "config_schema": {
            "type": "object",
            "required": ["name", "type", "condition_type"],
            "properties": {
                "name": {
                    "type": "string",
                    "label": "Snipe Name",
                    "placeholder": "Jordan 1 Restock Watch"
                },
                "type": {
                    "type": "select",
                    "label": "Watch Type",
                    "options": ["restock", "price", "listing", "url_change"]
                },
                "search_query": {
                    "type": "string",
                    "label": "What to Search For",
                    "placeholder": "Nike Air Max 90 size 11"
                },
                "target_url": {
                    "type": "string",
                    "label": "Specific URL (optional)",
                    "placeholder": "https://stockx.com/nike-air-max-90"
                },
                "condition_type": {
                    "type": "select",
                    "label": "Alert Me When",
                    "options": ["price_below", "in_stock", "new_listing", "page_changed"]
                },
                "condition_value": {
                    "type": "number",
                    "label": "Price Target (USD)",
                    "placeholder": "180"
                },
                "interval_minutes": {
                    "type": "select",
                    "label": "Check Every",
                    "options": [
                        {"value": 10, "label": "10 minutes (~$7/day)"},
                        {"value": 30, "label": "30 minutes (~$2.40/day)"},
                        {"value": 60, "label": "1 hour (~$1.20/day)"},
                        {"value": 360, "label": "6 hours (~$0.20/day)"}
                    ],
                    "default": 30
                },
                "notify_email": {
                    "type": "email",
                    "label": "Email for Alerts (optional)"
                }
            }
        }
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SWARM_API_URL}/agents/self-dock",
            json=payload,
            headers={"Authorization": f"Bearer {SWARM_AGENT_API_KEY}"}
        )
        resp.raise_for_status()
        return resp.json()
```

---

## Hire Flow

When a user hires Market Sniper on SWARM:

1. SWARM shows the config_schema as a form (name, type, query, condition, interval)
2. User fills it in and clicks "Hire"
3. SWARM creates a `background_job` record (existing table)
4. SWARM calls Market Sniper's webhook: `POST /swarm/hire`

```python
# Market Sniper handles the hire webhook
@router.post("/swarm/hire")
async def handle_hire(payload: HirePayload, x_agent_key: str = Header(None)):
    # Validate SWARM agent key
    # Create a snipe from the job config
    # The swarm_job_id links the snipe back to SWARM's background_job
    snipe = await create_snipe({
        "user_id": payload.user_id,
        "swarm_job_id": payload.job_id,
        "name": payload.config["name"],
        "type": payload.config["type"],
        "search_query": payload.config.get("search_query"),
        "target_url": payload.config.get("target_url"),
        "condition_type": payload.config["condition_type"],
        "condition_value": {"price": payload.config.get("condition_value")},
        "interval_minutes": payload.config.get("interval_minutes", 30),
        "notify_email": payload.config.get("notify_email"),
        "notify_inapp": True,
    })
    return {"status": "active", "snipe_id": str(snipe["id"])}
```

---

## Credit Charging

Every time a snipe runs, Market Sniper calls SWARM to charge the user's wallet:

```
POST /v1/jobs/charge
{
  "job_id": "<swarm background_job_id>",
  "credits": 5,
  "reason": "Market Sniper run: Jordan 1 Watch"
}
```

SWARM handles the debit atomically (existing billing infrastructure).

---

## In-App Notifications

When a snipe triggers, Market Sniper pushes a notification into SWARM's notification system:

```
POST /v1/notifications
{
  "user_id": "<user_id>",
  "type": "agent_result",
  "title": "🎯 Market Sniper: Jordan 1 Restock Watch triggered!",
  "body": "Found Nike Jordan 1 High OG at $165 on GOAT — below your $200 target.",
  "data": {
    "snipe_id": "...",
    "price": 165,
    "url": "https://goat.com/...",
    "platform": "GOAT"
  }
}
```

---

## What Needs to Be Built

### In Market Sniper (`src/swarm_integration.py`)
- [ ] `register_on_swarm()` — self-dock on startup
- [ ] `POST /swarm/hire` — handle new hire, create snipe
- [ ] `POST /swarm/pause` — pause a snipe when job is paused
- [ ] `POST /swarm/cancel` — delete snipe when job is cancelled
- [ ] SWARM credit charging in `tasks.py` (hook already exists, needs SWARM endpoint)
- [ ] SWARM notification push on trigger (alongside email)

### In SWARM (`swarm` repo)
- [ ] Automation hire modal needs to render `config_schema` dynamically
- [ ] `POST /v1/jobs/charge` endpoint (worker charges when agent calls this)
- [ ] `POST /v1/notifications` endpoint accessible by agent API keys
- [ ] `POST /swarm/hire` webhook call from hire flow

---

## Architecture Diagram

```
User clicks "Hire" on Market Sniper in SWARM marketplace
  │
  ▼
SWARM creates background_job, shows config form
  │
  ▼
User configures snipe (what/where/when/condition)
  │
  ▼
SWARM calls Market Sniper: POST /swarm/hire
  │
  ▼
Market Sniper creates snipe in its DB (snipes table)
  │
  ▼ (every interval_minutes)
Celery worker runs the snipe
  ├── Calls SWARM: POST /v1/jobs/charge (deduct 5 credits)
  ├── Searches / fetches / extracts with Claude
  └── If triggered:
      ├── Email notification (Resend)
      ├── SWARM in-app: POST /v1/notifications
      └── Webhook (if configured)
```

---

## Timeline

This integration can be built in 1-2 days once the core is deployed.
Priority: Deploy core first → test standalone → wire SWARM integration.
