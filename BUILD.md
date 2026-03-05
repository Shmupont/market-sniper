# Market Sniper — Build Spec for Claude Code
_This is the implementation guide. Build everything described here._

---

## Project Overview

Market Sniper is a standalone autonomous agent that monitors prices, restocks, and listings across the internet. It runs as a Python service on Railway, uses Claude as its brain, and has real web tools (Playwright + proxies) to handle any site.

**Repository:** `/Users/swarm.dev/market-sniper`
**Language:** Python 3.11
**DB:** PostgreSQL at `postgresql://postgres:xVdaBmfhuUcdFYMQWAbSPjTvlALNaFKK@switchback.proxy.rlwy.net:54371/railway`

---

## Directory Structure

Build this exact structure:

```
market-sniper/
├── src/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py           # FastAPI app entrypoint
│   │   ├── snipes.py         # CRUD routes for snipes
│   │   ├── runs.py           # Run history routes
│   │   └── notifications.py  # Notification routes
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── brain.py          # Claude LLM integration + tool dispatch
│   │   ├── prompts.py        # System prompts
│   │   └── tools/
│   │       ├── __init__.py
│   │       ├── fetch.py      # Tiered URL fetching (httpx → ScraperAPI → Playwright)
│   │       ├── search.py     # Brave Search API
│   │       ├── extract.py    # LLM-powered data extraction
│   │       └── browser.py    # Playwright + stealth browser
│   ├── worker/
│   │   ├── __init__.py
│   │   ├── celery_app.py     # Celery configuration
│   │   ├── tasks.py          # Task definitions (run_snipe, schedule_snipes)
│   │   └── scheduler.py      # Celery Beat periodic scheduler
│   ├── db/
│   │   ├── __init__.py
│   │   ├── connection.py     # DB connection pool
│   │   ├── models.py         # Table definitions (dataclasses, no ORM)
│   │   └── queries.py        # All DB queries
│   ├── notifications/
│   │   ├── __init__.py
│   │   └── email.py          # Resend email notifications
│   └── config.py             # All env vars + settings
├── migrations/
│   └── 001_create_snipes.sql # DB migration
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── railway.toml
├── .env.example
├── SPEC.md                   # (already exists)
└── README.md
```

---

## Environment Variables

```bash
# Database
DATABASE_URL=postgresql://postgres:xVdaBmfhuUcdFYMQWAbSPjTvlALNaFKK@switchback.proxy.rlwy.net:54371/railway

# LLM
ANTHROPIC_API_KEY=

# Scraping
SCRAPERAPI_KEY=           # https://scraperapi.com — rotating proxy layer
BRAVE_SEARCH_API_KEY=     # https://brave.com/search/api/

# Notifications
RESEND_API_KEY=           # https://resend.com

# Redis (for Celery)
REDIS_URL=redis://localhost:6379/0

# SWARM integration
SWARM_API_URL=https://api.openswarm.world
SWARM_AGENT_API_KEY=      # swrm_agent_* key for self-docking

# App
ENVIRONMENT=development   # development | production
LOG_LEVEL=INFO
```

---

## Database Migration

File: `migrations/001_create_snipes.sql`

```sql
-- Snipes: the user's configured watch tasks
CREATE TABLE IF NOT EXISTS snipes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID,                              -- SWARM user ID (nullable for standalone users)
  swarm_job_id UUID,                         -- links back to SWARM background_jobs if hired via marketplace
  
  -- Identity
  name TEXT NOT NULL,
  description TEXT,
  type TEXT NOT NULL CHECK (type IN ('restock', 'price', 'listing', 'url_change', 'arbitrage')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'triggered', 'expired', 'error')),
  
  -- Target
  target_url TEXT,
  search_query TEXT,
  platforms TEXT[] DEFAULT '{}',
  
  -- Condition
  condition_type TEXT NOT NULL CHECK (condition_type IN ('price_below', 'price_above', 'in_stock', 'out_of_stock', 'new_listing', 'page_changed', 'any_result')),
  condition_value JSONB DEFAULT '{}',        -- {"price": 180, "currency": "USD", "size": "11"}
  
  -- Schedule
  interval_minutes INT NOT NULL DEFAULT 10,
  next_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_run_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  
  -- Notifications
  notify_email TEXT,                         -- email address to notify
  notify_inapp BOOLEAN DEFAULT true,
  notify_webhook TEXT,
  notify_on_every_run BOOLEAN DEFAULT false, -- notify even if not triggered
  
  -- Billing
  credits_per_run INT DEFAULT 5,
  total_runs INT DEFAULT 0,
  total_spend_credits INT DEFAULT 0,
  
  -- Timestamps
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Run log: every execution of a snipe
CREATE TABLE IF NOT EXISTS snipe_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  snipe_id UUID NOT NULL REFERENCES snipes(id) ON DELETE CASCADE,
  
  ran_at TIMESTAMPTZ DEFAULT NOW(),
  duration_ms INT,
  status TEXT NOT NULL CHECK (status IN ('success', 'triggered', 'error', 'skipped')),
  
  -- What the agent found
  triggered BOOLEAN DEFAULT false,
  confidence TEXT CHECK (confidence IN ('high', 'medium', 'low')),
  trigger_summary TEXT,                      -- "Found Nike AM90 at $172 on StockX"
  raw_result JSONB,                          -- Full agent output
  
  -- Tools used
  tools_used TEXT[],
  tier_used TEXT,                            -- which scraping tier was needed
  
  -- Billing
  credits_charged INT DEFAULT 0,
  
  -- Error info
  error_message TEXT,
  error_type TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_snipes_status_next_run ON snipes(status, next_run_at) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_snipes_user_id ON snipes(user_id);
CREATE INDEX IF NOT EXISTS idx_snipe_runs_snipe_id ON snipe_runs(snipe_id);
CREATE INDEX IF NOT EXISTS idx_snipe_runs_ran_at ON snipe_runs(ran_at DESC);
```

---

## Core Modules

### `src/config.py`
Load all env vars using `python-dotenv`. Create a `Settings` dataclass with all config values. Fail fast with clear error messages if required vars are missing.

### `src/db/connection.py`
- Use `psycopg2` with a connection pool (`psycopg2.pool.ThreadedConnectionPool`)
- Pool size: min=2, max=10
- Context manager `get_db()` for acquiring/releasing connections
- Auto-commit off, manual transaction control

### `src/db/queries.py`
Implement these functions (no ORM, plain SQL):
```python
def get_due_snipes(limit: int = 50) -> list[dict]
def get_snipe_by_id(snipe_id: str) -> dict | None
def create_snipe(data: dict) -> dict
def update_snipe(snipe_id: str, updates: dict) -> dict
def pause_snipe(snipe_id: str) -> None
def mark_snipe_triggered(snipe_id: str) -> None
def create_run(snipe_id: str, result: dict) -> dict
def get_runs_for_snipe(snipe_id: str, limit: int = 20) -> list[dict]
def update_snipe_next_run(snipe_id: str, next_run_at: datetime) -> None
```

---

## Tool Implementations

### `src/agent/tools/fetch.py`

Implement tiered fetching. Try each tier in order, fall back on failure:

```python
async def fetch_url(url: str, context: str = "") -> FetchResult:
    """
    Tiered URL fetching:
    1. Simple httpx GET (fast, cheap)
    2. ScraperAPI (rotating proxy, handles basic bot detection)  
    3. Playwright + stealth (full browser, JS rendering)
    4. Playwright + ScraperAPI proxy (nuclear option)
    
    Returns: FetchResult(html=str, text=str, status=int, tier_used=str, url=str)
    """
```

ScraperAPI usage: `http://api.scraperapi.com?api_key={KEY}&url={url}&render=true`

For Playwright stealth, use `playwright-stealth` package:
```python
from playwright_stealth import stealth_async
page = await browser.new_page()
await stealth_async(page)
```

### `src/agent/tools/search.py`

```python
async def web_search(query: str, num_results: int = 5) -> list[SearchResult]:
    """
    Brave Search API: https://api.search.brave.com/res/v1/web/search
    Headers: Accept: application/json, X-Subscription-Token: {BRAVE_API_KEY}
    Returns list of SearchResult(title, url, description, age)
    """
```

### `src/agent/tools/extract.py`

```python
async def extract_price(html: str, context: str) -> PriceData:
    """
    Use Claude haiku to extract price data from HTML.
    Prompt: "Extract the price and availability from this HTML. Context: {context}"
    Returns: PriceData(price=float|None, currency=str, available=bool, confidence=str)
    """

async def extract_listing(html: str, context: str) -> ListingData:
    """Extract structured listing data (title, price, condition, seller, url)"""

async def extract_arbitrary(html: str, question: str) -> ArbitraryResult:
    """Answer an arbitrary question about page content"""
```

### `src/agent/tools/browser.py`

```python
async def get_browser() -> Browser:
    """Singleton Playwright browser instance with stealth enabled"""

async def screenshot_url(url: str) -> bytes:
    """For debugging — screenshot what the agent actually sees"""
```

---

## Agent Brain

### `src/agent/prompts.py`

```python
MARKET_SNIPER_SYSTEM_PROMPT = """
You are Market Sniper — an autonomous deal-hunting agent.

Your job: Given a snipe configuration, use your tools to determine if the target condition has been met.

Available tools:
- fetch_url(url): Fetch a webpage's content
- web_search(query): Search the web
- extract_price(html, context): Extract price/availability from HTML
- extract_listing(html, context): Extract listing data from HTML

Rules:
- Be precise and factual. Never hallucinate prices or availability.
- Use the minimum tools needed. Don't fetch if a search result already answers the question.
- If uncertain, return low confidence rather than guessing.
- Always return valid JSON in the exact format specified.

Output format (ALWAYS valid JSON):
{
  "triggered": boolean,
  "confidence": "high" | "medium" | "low",
  "summary": "Human-readable one-line summary of what you found",
  "data": {
    "price": float | null,
    "currency": "USD" | null,
    "available": boolean | null,
    "url": string | null,
    "platform": string | null,
    "listings": [] | null
  },
  "tools_used": ["fetch_url", ...],
  "next_action": "continue_monitoring" | "stop_trigger_fired" | "needs_human_review"
}
"""
```

### `src/agent/brain.py`

```python
async def run_snipe(snipe: dict) -> AgentResult:
    """
    Main agent execution loop for a single snipe.
    
    1. Build task description from snipe config
    2. Call Claude with tools available
    3. Execute tool calls as they come in (tool use loop)
    4. Parse final JSON output
    5. Evaluate against snipe condition
    6. Return AgentResult
    
    Use claude-3-5-haiku-20241022 for speed.
    Upgrade to claude-3-5-sonnet if haiku fails or returns low confidence.
    Max 5 tool call rounds per run.
    """
```

Use Anthropic's tool use API properly — implement the full tool call loop (not just one round).

---

## Worker

### `src/worker/celery_app.py`

```python
from celery import Celery

app = Celery('market_sniper')
app.config_from_object({
    'broker_url': REDIS_URL,
    'result_backend': REDIS_URL,
    'task_serializer': 'json',
    'accept_content': ['json'],
    'timezone': 'UTC',
    'beat_schedule': {
        'dispatch-due-snipes': {
            'task': 'src.worker.tasks.dispatch_due_snipes',
            'schedule': 30.0,  # every 30 seconds
        }
    }
})
```

### `src/worker/tasks.py`

```python
@app.task
def dispatch_due_snipes():
    """
    Runs every 30s via Celery Beat.
    1. Query DB for snipes WHERE status='active' AND next_run_at <= NOW()
    2. For each due snipe, dispatch run_snipe_task.delay(snipe_id)
    3. Update next_run_at immediately to prevent double-dispatch
    """

@app.task(bind=True, max_retries=3, default_retry_delay=60)
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
```

---

## Notifications

### `src/notifications/email.py`

```python
async def send_trigger_email(to: str, snipe: dict, result: AgentResult) -> bool:
    """
    Send trigger notification via Resend.
    Subject: "🎯 Market Sniper: {snipe.name} triggered!"
    Body: Clean HTML with what was found, link to view, and option to pause.
    """
```

Email template should include:
- What was found (summary from agent)
- Price / availability
- Direct link to the item
- "Pause this snipe" link
- SWARM branding

---

## API

### `src/api/main.py`

FastAPI app with these routes:

```
POST   /snipes              Create a new snipe
GET    /snipes              List snipes (filter by user_id, status)
GET    /snipes/{id}         Get snipe details
PATCH  /snipes/{id}         Update snipe (pause, change interval, etc.)
DELETE /snipes/{id}         Delete snipe
POST   /snipes/{id}/run     Manually trigger a run now
GET    /snipes/{id}/runs    Get run history for a snipe
GET    /health              Health check
```

Authentication: `Authorization: Bearer {token}` — for now, validate against SWARM user tokens. Accept `X-Agent-Key: swrm_agent_*` for SWARM-initiated runs.

---

## Deployment

### `Dockerfile`

```dockerfile
FROM python:3.11-slim

# Install Playwright deps
RUN apt-get update && apt-get install -y \
    wget gnupg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN playwright install chromium
RUN playwright install-deps chromium

COPY . .

# Default: run API
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### `railway.toml`

Configure two services:
1. **API service**: `uvicorn src.api.main:app --host 0.0.0.0 --port $PORT`
2. **Worker service**: `celery -A src.worker.celery_app worker --beat --loglevel=info`

### `docker-compose.yml`

Local dev setup with Redis + the app:
```yaml
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
  
  api:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    depends_on: [redis]
    command: uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
  
  worker:
    build: .
    env_file: .env
    depends_on: [redis]
    command: celery -A src.worker.celery_app worker --beat --loglevel=info
```

---

## requirements.txt

```
# Core
fastapi==0.115.0
uvicorn[standard]==0.30.0
python-dotenv==1.0.0
pydantic==2.8.0

# Database
psycopg2-binary==2.9.9

# Queue
celery[redis]==5.4.0
redis==5.0.8

# HTTP & Scraping
httpx==0.27.0
beautifulsoup4==4.12.3
lxml==5.3.0

# Browser automation
playwright==1.47.0
playwright-stealth==1.0.6

# LLM
anthropic==0.34.0

# Notifications
resend==2.3.0

# Utils
python-dateutil==2.9.0
structlog==24.4.0
```

---

## Build Order

Build in this exact order — each step must work before proceeding:

1. **Project scaffolding** — all files/dirs, requirements.txt, .env.example
2. **DB migration** — create snipes + snipe_runs tables (run against production DB)
3. **Config + DB layer** — config.py, connection.py, queries.py
4. **Tool: fetch_url** — tiered fetching, test against a real URL
5. **Tool: web_search** — Brave API integration
6. **Tool: extract_price** — Claude-powered extraction
7. **Agent brain** — full tool use loop with Claude
8. **Worker** — Celery app + dispatch + run task
9. **Notifications** — email via Resend
10. **API** — FastAPI routes
11. **Docker + Railway config** — deployment ready

---

## Test Cases to Verify (Manual)

After building, verify these work:

1. Create a snipe via API: `POST /snipes` with `{type: "price", search_query: "Nike Air Max 90 size 11", condition_type: "price_below", condition_value: {"price": 200}}`
2. Manually trigger it: `POST /snipes/{id}/run`
3. Check run history: `GET /snipes/{id}/runs`
4. Verify the agent actually searched the web and returned structured data
5. Verify Celery worker picks up due snipes automatically

---

## Notes for the Builder

- **No hallucinating tools or packages** that don't exist. Use exact package names from requirements.txt.
- **Error handling everywhere** — every tool call can fail. Log errors, don't crash the worker.
- **Async where possible** — the agent tools should all be async. Celery tasks are sync wrappers around async code (use `asyncio.run()`).
- **Structured logging** — use `structlog` throughout. Every run should have a trace ID.
- **Never store raw API keys in code** — always from env vars.
- **The DB is shared with SWARM** — don't drop or alter SWARM tables. Only create new ones.
