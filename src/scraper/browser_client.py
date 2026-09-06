"""
Async Playwright-based browser client.

Used only for sources that require JS rendering or basic bot-protection
navigation. Reuses a single browser context across calls for efficiency.
"""

import asyncio
import logging
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 45_000  # ms — Playwright uses milliseconds


class BrowserClient:
    """Headless Chromium fetcher with rate limiting."""

    def __init__(
        self,
        concurrency_limit: int = 3,
        rate_limit_delay: float = 2.0,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self._semaphore = asyncio.Semaphore(concurrency_limit)
        self._rate_limit_delay = rate_limit_delay
        self._timeout = timeout
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    async def _ensure_browser(self):
        """Launch browser and context if not already running."""
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            self._context = await self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
            )

    async def fetch(self, url: str, wait_selector: Optional[str] = None) -> str:
        """
        Navigate to a URL with headless Chromium and return the rendered HTML.

        Args:
            url: The page URL to load.
            wait_selector: Optional CSS selector to wait for before extracting HTML.
                           Defaults to waiting for 'networkidle'.
        """
        async with self._semaphore:
            await self._ensure_browser()
            page: Page = await self._context.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=self._timeout)
                if wait_selector:
                    await page.wait_for_selector(
                        wait_selector, timeout=self._timeout
                    )
                html = await page.content()
                await asyncio.sleep(self._rate_limit_delay)
                return html
            except Exception as exc:
                logger.error("Browser fetch failed for %s: %s", url, exc)
                raise
            finally:
                await page.close()

    async def close(self):
        """Shut down browser and Playwright."""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._context = None
        self._playwright = None
