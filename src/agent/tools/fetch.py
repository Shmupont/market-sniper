from dataclasses import dataclass

import httpx
import structlog
from bs4 import BeautifulSoup

from src.config import get_settings

log = structlog.get_logger(__name__)

FETCH_TIMEOUT = 20  # seconds
PLAYWRIGHT_TIMEOUT = 30000  # ms


@dataclass
class FetchResult:
    html: str
    text: str
    status: int
    tier_used: str
    url: str


def _extract_text(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)[:50000]
    except Exception:
        return html[:50000]


async def _tier1_httpx(url: str) -> FetchResult:
    """Tier 1: Simple httpx GET."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text
        return FetchResult(
            html=html,
            text=_extract_text(html),
            status=resp.status_code,
            tier_used="httpx",
            url=str(resp.url),
        )


async def _tier2_scraperapi(url: str) -> FetchResult:
    """Tier 2: ScraperAPI rotating proxy."""
    settings = get_settings()
    if not settings.scraperapi_key:
        raise RuntimeError("SCRAPERAPI_KEY not configured")

    proxy_url = (
        f"http://api.scraperapi.com"
        f"?api_key={settings.scraperapi_key}"
        f"&url={httpx.URL(url)}"
        f"&render=true"
    )
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        resp = await client.get(proxy_url)
        resp.raise_for_status()
        html = resp.text
        return FetchResult(
            html=html,
            text=_extract_text(html),
            status=resp.status_code,
            tier_used="scraperapi",
            url=url,
        )


async def _tier3_playwright(url: str) -> FetchResult:
    """Tier 3: Playwright stealth browser."""
    from playwright_stealth import stealth_async
    from src.agent.tools.browser import get_browser

    browser = await get_browser()
    page = await browser.new_page()
    try:
        await stealth_async(page)
        response = await page.goto(url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT)
        html = await page.content()
        status = response.status if response else 200
        return FetchResult(
            html=html,
            text=_extract_text(html),
            status=status,
            tier_used="playwright",
            url=page.url,
        )
    finally:
        await page.close()


async def _tier4_playwright_scraperapi(url: str) -> FetchResult:
    """Tier 4: Playwright + ScraperAPI residential proxy."""
    settings = get_settings()
    if not settings.scraperapi_key:
        raise RuntimeError("SCRAPERAPI_KEY not configured")

    from playwright_stealth import stealth_async
    from src.agent.tools.browser import get_browser

    proxy_server = f"http://scraperapi:{settings.scraperapi_key}@proxy-server.scraperapi.com:8001"
    browser = await get_browser()
    context = await browser.new_context(
        proxy={"server": proxy_server},
    )
    page = await context.new_page()
    try:
        await stealth_async(page)
        response = await page.goto(url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT)
        html = await page.content()
        status = response.status if response else 200
        return FetchResult(
            html=html,
            text=_extract_text(html),
            status=status,
            tier_used="playwright+scraperapi",
            url=page.url,
        )
    finally:
        await page.close()
        await context.close()


async def fetch_url(url: str, context: str = "") -> FetchResult:
    """
    Tiered URL fetching:
    1. Simple httpx GET (fast, cheap)
    2. ScraperAPI (rotating proxy, handles basic bot detection)
    3. Playwright + stealth (full browser, JS rendering)
    4. Playwright + ScraperAPI proxy (nuclear option)

    Returns: FetchResult(html, text, status, tier_used, url)
    """
    tiers = [
        ("httpx", _tier1_httpx),
        ("scraperapi", _tier2_scraperapi),
        ("playwright", _tier3_playwright),
        ("playwright+scraperapi", _tier4_playwright_scraperapi),
    ]

    last_error: Exception | None = None
    for tier_name, tier_fn in tiers:
        try:
            log.info("fetch.attempt", url=url, tier=tier_name, context=context)
            result = await tier_fn(url)
            log.info("fetch.success", url=url, tier=tier_name, status=result.status)
            return result
        except Exception as exc:
            log.warning("fetch.tier_failed", url=url, tier=tier_name, error=str(exc))
            last_error = exc
            continue

    raise RuntimeError(f"All fetch tiers failed for {url}: {last_error}") from last_error
