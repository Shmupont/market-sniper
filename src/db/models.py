from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Snipe:
    id: str
    name: str
    type: str
    status: str
    condition_type: str
    interval_minutes: int
    next_run_at: datetime
    created_at: datetime
    updated_at: datetime

    user_id: str | None = None
    swarm_job_id: str | None = None
    description: str | None = None
    target_url: str | None = None
    search_query: str | None = None
    platforms: list[str] = field(default_factory=list)
    condition_value: dict[str, Any] = field(default_factory=dict)
    last_run_at: datetime | None = None
    expires_at: datetime | None = None

    notify_email: str | None = None
    notify_inapp: bool = True
    notify_webhook: str | None = None
    notify_on_every_run: bool = False

    credits_per_run: int = 5
    total_runs: int = 0
    total_spend_credits: int = 0


@dataclass
class SnipeRun:
    id: str
    snipe_id: str
    ran_at: datetime
    status: str

    duration_ms: int | None = None
    triggered: bool = False
    confidence: str | None = None
    trigger_summary: str | None = None
    raw_result: dict[str, Any] | None = None
    tools_used: list[str] = field(default_factory=list)
    tier_used: str | None = None
    credits_charged: int = 0
    error_message: str | None = None
    error_type: str | None = None
