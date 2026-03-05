# Market Sniper — Full Product Spec
_Version 1.0 | Started: 2026-03-04_

---

## What It Is

Market Sniper is a standalone autonomous agent product. It watches the internet for you — prices, restocks, deal opportunities, listings — and alerts you the moment conditions are met. 

Think of it like a personal shopping assistant that never sleeps. You tell it what you want, set your price, and it hunts 24/7 until it finds it.

It operates as a first-party agent listed on SWARM's marketplace, and as a standalone product at its own domain.

---

## Core Use Cases (v1)

1. **Restock watcher** — "Alert me when Nike Air Max 90 size 11 restocks on StockX"
2. **Price sniper** — "Alert me when this item drops below $X on any of these sites"
3. **Listing watcher** — "Alert me when a new listing matches [query] on eBay/Craigslist/FB Marketplace"
4. **Arbitrage spotter** — "Find price discrepancies for [item] across multiple platforms"
5. **Custom URL monitor** — "Alert me when anything on this page changes"

---

## Architecture

```
User (web UI or SWARM hire flow)
  └── Creates a "Snipe" (target + condition + schedule + notification prefs)
  
SWARM DB (shared)
  └── Stores snipes, job runs, notifications
  
Market Sniper Agent (Python service on Railway)
  └── Polls DB every 60s for due snipes
  └── For each due snipe:
      ├── Runs LLM with tools (web_search, fetch_url, extract_data)
      ├── Evaluates condition (is price < threshold? is item in stock?)
      ├── If triggered → fires notification (email, SMS, webhook)
      ├── Logs run result
      └── Schedules next run
  
Notification Layer
  └── Email (Resend)
  └── SMS (Twilio — future)
  └── Discord webhook (future)
  └── SWARM in-app notification
```

---

## Tech Stack

| Layer | Tech | Why |
|---|---|---|
| Agent runtime | Python 3.11 | Best AI/scraping ecosystem |
| LLM | Claude haiku (speed) + sonnet (complex extraction) | Fast + smart where needed |
| Job queue | Celery + Redis | Production-grade, scales horizontally |
| HTTP (fast) | httpx (async) | Fast, async, connection pooling |
| HTML parsing | BeautifulSoup4 | Lightweight parsing |
| Browser automation | Playwright + playwright-stealth | Handles JS-heavy + anti-bot sites |
| Proxy layer | ScraperAPI | Rotating residential proxies, handles CAPTCHAs, pay-per-request |
| Search | Brave Search API | Clean, fast, no Google restrictions |
| DB | PostgreSQL (Railway, same as SWARM) | Single source of truth |
| Notifications | Resend (email) + SWARM notifications API | Email-first, extensible |
| Deployment | Railway (2 services: API + Celery worker) | Already there, easy |
| Frontend | Standalone Next.js or SWARM-integrated | TBD |

### Scraping Strategy — Tiered (tries cheapest first)

```
Tier 1: Official API (eBay API, Amazon PA API) — free, fast, reliable
Tier 2: httpx + BeautifulSoup — fast, cheap, works on simple sites
Tier 3: ScraperAPI proxy — rotating IPs, handles basic bot detection
Tier 4: Playwright + stealth — full browser, JS rendering, anti-bot bypass
Tier 5: Playwright + ScraperAPI residential proxy — nuclear option
```

Each tool tries the appropriate tier based on the target platform.

---

## Database Schema

### `snipes` table
```sql
CREATE TABLE snipes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  name TEXT NOT NULL,                        -- "Jordan 1 Restock Watch"
  type TEXT NOT NULL,                        -- restock | price | listing | url_change | arbitrage
  status TEXT DEFAULT 'active',              -- active | paused | triggered | expired
  
  -- Target config
  target_url TEXT,                           -- specific URL to watch (optional)
  search_query TEXT,                         -- what to search for
  platforms TEXT[],                          -- ['stockx', 'ebay', 'goat', 'amazon']
  
  -- Condition config
  condition_type TEXT NOT NULL,              -- price_below | in_stock | new_listing | page_changed
  condition_value JSONB,                     -- {"price": 180, "currency": "USD"} or {"size": "11"}
  
  -- Schedule
  interval_minutes INT DEFAULT 10,          -- how often to check
  next_run_at TIMESTAMPTZ DEFAULT now(),
  expires_at TIMESTAMPTZ,                    -- optional expiry
  
  -- Notifications
  notify_email BOOLEAN DEFAULT true,
  notify_inapp BOOLEAN DEFAULT true,
  notify_webhook TEXT,                       -- optional webhook URL
  
  -- Billing
  credits_per_run INT DEFAULT 5,            -- charged per execution
  total_runs INT DEFAULT 0,
  total_spend_credits INT DEFAULT 0,
  
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

### `snipe_runs` table
```sql
CREATE TABLE snipe_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  snipe_id UUID REFERENCES snipes(id),
  ran_at TIMESTAMPTZ DEFAULT now(),
  duration_ms INT,
  status TEXT,                               -- success | error | triggered
  result JSONB,                              -- raw agent output
  triggered BOOLEAN DEFAULT false,
  trigger_summary TEXT,                      -- human-readable: "Found at $172 on StockX"
  credits_charged INT,
  error_message TEXT
);
```

---

## Tool Library (Agent Tools)

These are the real-world capabilities the agent has access to during each run:

### 1. `fetch_url(url: str) -> str`
- Fetches HTML content of a URL
- Handles basic JS rendering via Playwright fallback
- Returns cleaned text + structured data where possible

### 2. `web_search(query: str, num_results: int) -> list`
- Searches the web via Brave/SerpAPI
- Returns title, URL, snippet for each result
- Used for broad searches ("Nike AM90 size 11 lowest price")

### 3. `extract_price(html: str, context: str) -> dict`
- LLM-powered extraction of price data from HTML
- Returns: `{price: float, currency: str, available: bool, url: str}`

### 4. `check_availability(url: str, item_desc: str) -> dict`
- Checks if an item is in stock at a URL
- Returns: `{in_stock: bool, variants: list, price: float}`

### 5. `search_listings(query: str, platform: str) -> list`
- Platform-specific listing search (eBay, Craigslist, FB Marketplace)
- Returns structured listing data

### 6. `compare_prices(item: str, platforms: list) -> list`
- Runs price check across multiple platforms in parallel
- Returns sorted list of {platform, price, url, in_stock}

---

## Agent System Prompt

```
You are Market Sniper — an autonomous deal-hunting agent.

Your job: Given a target and condition, search the internet and determine if the condition has been met.

Rules:
- Be precise. Return structured data.
- Use the minimum tools needed to answer the question.
- Never hallucinate prices or availability. Only report what you find.
- If you can't determine the answer definitively, return {triggered: false, confidence: "low", reason: "..."}.

Output format (always return valid JSON):
{
  "triggered": boolean,
  "confidence": "high" | "medium" | "low",
  "summary": "Human-readable summary of what you found",
  "data": {
    "price": float | null,
    "available": boolean | null,
    "url": string | null,
    "platform": string | null,
    "listings": [] | null
  },
  "next_action": "continue_monitoring" | "stop_trigger_fired" | "needs_human_review"
}
```

---

## Pricing Model

### For SWARM-listed version (hired through marketplace)
- **5 credits per run** ($0.05/run)
- Every 10 minutes = ~$7.20/day
- Every 30 minutes = ~$2.40/day
- Every hour = ~$1.20/day
- SWARM takes 10%, Coleman (creator) keeps 90%

### For standalone product (future)
- **Free tier:** 1 snipe, checks every 60 min, email only
- **Pro $9.99/mo:** 10 snipes, checks every 10 min, all notifications
- **Savage $29.99/mo:** Unlimited snipes, every 1 min, arbitrage mode, API access

---

## Build Phases

### Phase 1 — Core Agent (Week 1)
- [ ] Python agent service with polling loop
- [ ] `fetch_url` and `web_search` tools
- [ ] Basic price extraction (LLM-powered)
- [ ] Snipe schema + DB tables
- [ ] Email notifications via Resend
- [ ] Railway deployment

### Phase 2 — SWARM Integration (Week 2)
- [ ] Register as automation agent on SWARM via self-dock
- [ ] SWARM hire flow → creates snipe in DB
- [ ] In-app SWARM notifications for trigger events
- [ ] Per-run credit billing through SWARM wallet
- [ ] Creator dashboard showing run history + earnings

### Phase 3 — Expand Tool Library (Week 3+)
- [ ] Platform-specific scrapers (StockX, GOAT, eBay, Amazon)
- [ ] Playwright for JS-heavy sites
- [ ] Multi-platform price comparison
- [ ] Arbitrage mode
- [ ] Webhook notifications
- [ ] SMS via Twilio

### Phase 4 — Standalone Product
- [ ] market-sniper.com or sniper.swarm domain
- [ ] Own landing page + signup flow
- [ ] Subscription billing (Stripe)
- [ ] Public API for developers
- [ ] Agent marketplace for specialized snipers (sneakers, crypto, real estate)

---

## Competitive Landscape

| Product | What it does | Gap |
|---|---|---|
| IFTTT / Zapier | Workflow automation | Not AI-native, can't handle ambiguous targets |
| Distill.io | URL change monitoring | Dumb — just diffs HTML, no intelligence |
| Honey / Capital One Shopping | Browser extension price tracking | Passive only, no active hunting |
| Keepa | Amazon price history | Amazon only, alerts not proactive |
| **Market Sniper** | AI agent that actively hunts | **Intelligent, multi-platform, autonomous** |

The key differentiator: **Market Sniper understands intent, not just HTML diffs.** It knows what you're looking for, actively searches, and makes intelligent decisions about whether a result matches.

---

## Open Questions to Resolve

1. **Anti-bot / rate limiting** — how do we handle sites that block scrapers? (Playwright + rotating proxies?)
2. **Legal** — scraping ToS considerations for major platforms
3. **Accuracy** — LLM-powered extraction can hallucinate. Need confidence scoring + human review for high-stakes triggers.
4. **Scale** — 1000 users × 5 snipes × every 10 min = a lot of runs. Need queue system (Redis/Bull) not just DB polling.
5. **Platform-specific APIs** — eBay, Amazon have official APIs. Use them where available instead of scraping.

---

_This spec is a living document. Update as we build._
