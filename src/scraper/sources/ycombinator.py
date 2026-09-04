"""
Y Combinator startup directory scraper.

Uses the Algolia search API (public credentials embedded in YC's page source)
to fetch company data from the YCCompany_production index. This avoids
scraping HTML entirely — the data comes back as structured JSON.

Filters to Active companies only per user requirement.
"""

import asyncio
import base64
import json
import logging
from typing import Optional

import aiosqlite

from src.scraper.http_client import HttpClient
from src.scraper.storage import save_startup

logger = logging.getLogger(__name__)

# Algolia credentials are publicly embedded in the YC companies page source.
# These are READ-ONLY search keys, not admin keys.
ALGOLIA_APP_ID = "45BWZJ1SGC"
ALGOLIA_API_KEY_ENCODED = (
    "NzllNTY5MzJiZGM2OTY2ZTQwMDEzOTNhYWZiZGRjODlhYzVkNjBmOGRj"
    "NzJiMWM4ZTU0ZDlhYTZjOTJiMjlhMWFuYWx5dGljc1RhZ3M9eWNkYyZy"
    "ZXN0cmljdEluZGljZXM9WUNDb21wYW55X3Byb2R1Y3Rpb24lMkNZQ0Nv"
    "bXBhbnlfQnlfTGF1bmNoX0RhdGVfcHJvZHVjdGlvbiZ0YWdGaWx0ZXJz"
    "PSU1QiUyMnljZGNfcHVibGljJTIyJTVE"
)
ALGOLIA_INDEX = "YCCompany_production"
ALGOLIA_SEARCH_URL = (
    f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"
)


def _get_api_key() -> str:
    """Return the Algolia API key directly."""
    return ALGOLIA_API_KEY_ENCODED


def _parse_team_size(team_size_str: Optional[str]) -> Optional[int]:
    """
    Parse YC's team size string into an integer.

    Examples:
        "1-10" -> 10  (upper bound)
        "11-50" -> 50
        "51-200" -> 200
        "201-500" -> 500
        "501-1000" -> 1000
        "1000+" -> 1000
        "6132" -> 6132
    """
    if not team_size_str:
        return None

    team_size_str = str(team_size_str).strip()

    # Direct integer
    try:
        return int(team_size_str)
    except ValueError:
        pass

    # Range like "11-50" -> take upper bound
    if "-" in team_size_str:
        parts = team_size_str.split("-")
        try:
            return int(parts[-1].strip())
        except ValueError:
            pass

    # "1000+" format
    if team_size_str.endswith("+"):
        try:
            return int(team_size_str[:-1])
        except ValueError:
            pass

    return None


async def scrape_ycombinator(
    client: HttpClient,
    db: aiosqlite.Connection,
    limit: Optional[int] = None,
) -> int:
    """
    Scrape Active startups from Y Combinator's directory via Algolia.

    Args:
        client: HttpClient instance.
        db: Open database connection.
        limit: Max startups to fetch (for testing). None = no limit.

    Returns:
        Total number of startups saved.
    """
    api_key = _get_api_key()
    headers = {
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "X-Algolia-API-Key": api_key,
        "Content-Type": "application/json",
    }

    total_saved = 0
    page = 0
    hits_per_page = 100  # Algolia max is 1000

    while True:
        if limit is not None and total_saved >= limit:
            logger.info("Reached limit of %d startups, stopping", limit)
            break

        # Algolia search query — filter to Active companies only
        payload = {
            "query": "",
            "page": page,
            "hitsPerPage": hits_per_page,
            "facetFilters": [["status:Active"]],
            "attributesToRetrieve": [
                "name",
                "slug",
                "team_size",
                "status",
                "long_description",
                "one_liner",
                "website",
                "all_locations",
                "batch_name",
                "industries",
            ],
        }

        logger.info(
            "Fetching YC Algolia page %d (hitsPerPage=%d)", page, hits_per_page
        )

        try:
            # Use the underlying session directly for POST
            session = await client._get_session()
            async with session.post(
                ALGOLIA_SEARCH_URL,
                headers=headers,
                json=payload,
            ) as resp:
                if resp.status != 200:
                    logger.error(
                        "Algolia search failed with status %d", resp.status
                    )
                    break
                data = await resp.json()
        except Exception as exc:
            logger.error("Failed to query Algolia: %s", exc)
            break

        hits = data.get("hits", [])
        if not hits:
            logger.info("No more hits from Algolia (page %d)", page)
            break

        for hit in hits:
            if limit is not None and total_saved >= limit:
                break

            name = hit.get("name")
            if not name:
                continue

            # Only Active companies
            status = hit.get("status", "")
            if status != "Active":
                continue

            slug = hit.get("slug", "")
            source_url = f"https://www.ycombinator.com/companies/{slug}" if slug else None
            if not source_url:
                continue

            team_size = hit.get("team_size")
            employee_count = _parse_team_size(str(team_size)) if team_size else None

            saved = await save_startup(
                db=db,
                source_name="Y Combinator",
                source_url=source_url,
                entity_name=name,
                employee_count=employee_count,
            )

            if saved:
                total_saved += 1
                if total_saved % 50 == 0 or total_saved <= 5:
                    logger.info(
                        "[YC] Saved #%d: %s (employees: %s, status: %s)",
                        total_saved, name, employee_count, status,
                    )

        # Check pagination
        nb_pages = data.get("nbPages", 0)
        page += 1
        if page >= nb_pages:
            logger.info("Reached last Algolia page (%d)", nb_pages)
            break

        # Rate limit between Algolia requests
        await asyncio.sleep(client._rate_limit_delay)

    logger.info("Y Combinator scraping complete: %d startups saved", total_saved)
    return total_saved
