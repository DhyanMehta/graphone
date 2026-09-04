"""
Async HTTP client using aiohttp.

Used for sources that return usable HTML/JSON directly without JS rendering.
Provides per-domain rate limiting via asyncio.Semaphore and configurable delays.
"""

import asyncio
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# Default request timeout in seconds
DEFAULT_TIMEOUT = 30
# Default max retries with exponential backoff
DEFAULT_MAX_RETRIES = 3
# Base backoff delay in seconds
BACKOFF_BASE = 2.0

USER_AGENT = (
    "Graphone-Scraper/1.0 "
    "(research data pipeline; +https://github.com/DhyanMehta/graphone)"
)


class HttpClient:
    """Async HTTP fetcher with rate limiting and retry logic."""

    def __init__(
        self,
        concurrency_limit: int = 5,
        rate_limit_delay: float = 1.0,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self._semaphore = asyncio.Semaphore(concurrency_limit)
        self._rate_limit_delay = rate_limit_delay
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._max_retries = max_retries
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT},
            )
        return self._session

    async def fetch(
        self,
        url: str,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> str:
        """
        Fetch a URL and return the response body as a string.

        Respects per-domain concurrency limit and rate-limit delay.
        Retries on transient failures with exponential backoff.
        """
        async with self._semaphore:
            last_error: Optional[Exception] = None
            for attempt in range(1, self._max_retries + 1):
                try:
                    session = await self._get_session()
                    async with session.get(
                        url, headers=headers, params=params
                    ) as resp:
                        if resp.status == 200:
                            body = await resp.text()
                            # Respect rate limit delay after successful request
                            await asyncio.sleep(self._rate_limit_delay)
                            return body
                        elif resp.status == 429:
                            # Rate limited — back off longer
                            wait = BACKOFF_BASE * (2 ** attempt)
                            logger.warning(
                                "Rate limited on %s, waiting %.1fs (attempt %d/%d)",
                                url, wait, attempt, self._max_retries,
                            )
                            await asyncio.sleep(wait)
                            continue
                        elif resp.status in (500, 502, 503, 504):
                            wait = BACKOFF_BASE * attempt
                            logger.warning(
                                "Server error %d on %s, retrying in %.1fs (attempt %d/%d)",
                                resp.status, url, wait, attempt, self._max_retries,
                            )
                            await asyncio.sleep(wait)
                            continue
                        else:
                            # Non-retryable HTTP error
                            logger.error(
                                "HTTP %d on %s — not retrying", resp.status, url
                            )
                            raise aiohttp.ClientResponseError(
                                resp.request_info,
                                resp.history,
                                status=resp.status,
                                message=f"HTTP {resp.status}",
                            )
                except aiohttp.ClientResponseError as exc:
                    # Don't retry 4xx errors (except 429 which is handled above)
                    raise exc
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    last_error = exc
                    if attempt < self._max_retries:
                        wait = BACKOFF_BASE * attempt
                        logger.warning(
                            "Request error on %s: %s — retrying in %.1fs (attempt %d/%d)",
                            url, exc, wait, attempt, self._max_retries,
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.error(
                            "Request failed after %d attempts on %s: %s",
                            self._max_retries, url, exc,
                        )
            if last_error:
                raise last_error
            raise RuntimeError(f"Fetch failed for {url} with no captured error")

    async def fetch_json(
        self,
        url: str,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        """Fetch a URL and parse the response as JSON."""
        async with self._semaphore:
            last_error: Optional[Exception] = None
            for attempt in range(1, self._max_retries + 1):
                try:
                    session = await self._get_session()
                    async with session.get(
                        url, headers=headers, params=params
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json(content_type=None)
                            await asyncio.sleep(self._rate_limit_delay)
                            return data
                        elif resp.status == 429:
                            wait = BACKOFF_BASE * (2 ** attempt)
                            logger.warning(
                                "Rate limited on %s, waiting %.1fs",
                                url, wait,
                            )
                            await asyncio.sleep(wait)
                            continue
                        elif resp.status in (500, 502, 503, 504):
                            wait = BACKOFF_BASE * attempt
                            logger.warning(
                                "Server error %d on %s, retrying in %.1fs",
                                resp.status, url, wait,
                            )
                            await asyncio.sleep(wait)
                            continue
                        else:
                            logger.error(
                                "HTTP %d on %s — not retrying", resp.status, url
                            )
                            raise aiohttp.ClientResponseError(
                                resp.request_info,
                                resp.history,
                                status=resp.status,
                                message=f"HTTP {resp.status}",
                            )
                except aiohttp.ClientResponseError as exc:
                    raise exc
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    last_error = exc
                    if attempt < self._max_retries:
                        wait = BACKOFF_BASE * attempt
                        logger.warning(
                            "Request error on %s: %s — retrying in %.1fs",
                            url, exc, wait,
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.error(
                            "Request failed after %d attempts on %s: %s",
                            self._max_retries, url, exc,
                        )
            if last_error:
                raise last_error
            raise RuntimeError(f"Fetch failed for {url} with no captured error")

    async def close(self):
        """Close the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
