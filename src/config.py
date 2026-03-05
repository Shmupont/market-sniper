import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Required environment variable {key!r} is missing or empty")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass
class Settings:
    # Database
    database_url: str

    # LLM
    anthropic_api_key: str

    # Scraping
    scraperapi_key: str
    brave_search_api_key: str

    # Notifications
    resend_api_key: str

    # Redis
    redis_url: str

    # SWARM
    swarm_api_url: str
    swarm_agent_api_key: str

    # App
    environment: str
    log_level: str


def load_settings() -> Settings:
    return Settings(
        database_url=_require("DATABASE_URL"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        scraperapi_key=_optional("SCRAPERAPI_KEY"),
        brave_search_api_key=_optional("BRAVE_SEARCH_API_KEY"),
        resend_api_key=_optional("RESEND_API_KEY"),
        redis_url=_optional("REDIS_URL", "redis://localhost:6379/0"),
        swarm_api_url=_optional("SWARM_API_URL", "https://api.openswarm.world"),
        swarm_agent_api_key=_optional("SWARM_AGENT_API_KEY"),
        environment=_optional("ENVIRONMENT", "development"),
        log_level=_optional("LOG_LEVEL", "INFO"),
    )


# Module-level singleton — loaded lazily so missing vars only blow up when used
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
