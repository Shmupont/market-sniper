from dataclasses import dataclass

import httpx
import structlog

from src.config import get_settings

log = structlog.get_logger(__name__)

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


@dataclass
class SearchResult:
    title: str
    url: str
    description: str
    age: str | None = None


async def web_search(query: str, num_results: int = 5) -> list[SearchResult]:
    """
    Search the web via Brave Search API.
    Returns list of SearchResult(title, url, description, age).
    """
    settings = get_settings()
    if not settings.brave_search_api_key:
        raise RuntimeError("BRAVE_SEARCH_API_KEY not configured")

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": settings.brave_search_api_key,
    }
    params = {
        "q": query,
        "count": min(num_results, 20),
        "search_lang": "en",
        "country": "us",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(BRAVE_SEARCH_URL, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

    results = []
    for item in data.get("web", {}).get("results", []):
        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                description=item.get("description", ""),
                age=item.get("age"),
            )
        )

    log.info("search.complete", query=query, num_results=len(results))
    return results[:num_results]
