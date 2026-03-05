import asyncio
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_browser: Any = None
_playwright: Any = None


async def get_browser():
    """Singleton Playwright browser instance with stealth enabled."""
    global _browser, _playwright
    if _browser is None or not _browser.is_connected():
        from playwright.async_api import async_playwright
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-accelerated-2d-canvas",
                "--no-first-run",
                "--no-zygote",
                "--disable-gpu",
            ],
        )
        log.info("browser.launched")
    return _browser


async def close_browser() -> None:
    global _browser, _playwright
    if _browser:
        await _browser.close()
        _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None
    log.info("browser.closed")


async def screenshot_url(url: str) -> bytes:
    """For debugging — screenshot what the agent actually sees."""
    browser = await get_browser()
    page = await browser.new_page()
    try:
        from playwright_stealth import stealth_async
        await stealth_async(page)
        await page.goto(url, wait_until="networkidle", timeout=30000)
        return await page.screenshot(full_page=True)
    finally:
        await page.close()
