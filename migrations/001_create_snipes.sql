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
