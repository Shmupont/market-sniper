import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

app = Celery("market_sniper")
app.config_from_object(
    {
        "broker_url": REDIS_URL,
        "result_backend": REDIS_URL,
        "task_serializer": "json",
        "result_serializer": "json",
        "accept_content": ["json"],
        "timezone": "UTC",
        "enable_utc": True,
        "beat_schedule": {
            "dispatch-due-snipes": {
                "task": "src.worker.tasks.dispatch_due_snipes",
                "schedule": 30.0,  # every 30 seconds
            }
        },
        "task_routes": {
            "src.worker.tasks.*": {"queue": "default"},
        },
    }
)
