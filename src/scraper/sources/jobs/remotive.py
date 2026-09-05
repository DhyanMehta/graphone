"""
Remotive AI jobs crawler.

Fetches the Remotive remote jobs API for AI roles, validates 24-hour freshness,
normalizes job schemas, and persists to SQLite.
"""

import logging
from typing import Optional
from bs4 import BeautifulSoup
import aiosqlite

from src.scraper.http_client import HttpClient
from src.scraper.date_normalizer import evaluate_date_and_freshness
from src.scraper.storage import save_job

logger = logging.getLogger(__name__)

API_URL = "https://remotive.com/api/remote-jobs?search=ai"
SOURCE_NAME = "Remotive"


def _infer_role_family(category: str, title: str) -> Optional[str]:
    """Map Remotive category and title to standard role family."""
    combined = f"{category.lower()} {title.lower()}"
    if any(w in combined for w in ["software", "development", "engineer", "developer", "ml", "ai"]):
        return "Engineering"
    if any(w in combined for w in ["data"]):
        return "Data"
    if any(w in combined for w in ["product"]):
        return "Product"
    if any(w in combined for w in ["design"]):
        return "Design"
    if any(w in combined for w in ["sales"]):
        return "Sales"
    if any(w in combined for w in ["marketing"]):
        return "Marketing"
    return None


async def scrape_remotive(
    client: HttpClient,
    db: aiosqlite.Connection,
    limit: Optional[int] = 30,
) -> dict:
    """
    Scrape fresh AI jobs from Remotive.

    Returns:
        Dict with metrics: raw_fetched, survived_24h, saved.
    """
    logger.info("Fetching Remotive AI jobs from %s", API_URL)
    raw_fetched = 0
    survived_24h = 0
    saved = 0

    try:
        data = await client.fetch_json(API_URL)
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
    except Exception as exc:
        logger.error("Failed to fetch Remotive jobs: %s", exc)
        return {"raw_fetched": 0, "survived_24h": 0, "saved": 0}

    logger.info("Discovered %d raw jobs from Remotive API", len(jobs))

    for job in jobs:
        if limit is not None and saved >= limit:
            break

        raw_fetched += 1

        title = str(job.get("title", "")).strip()
        company = str(job.get("company_name", "")).strip()
        job_url = str(job.get("url", "")).strip()
        raw_date = job.get("publication_date")
        category = str(job.get("category", "")).strip()

        if not title or not company or not job_url:
            continue

        # 24-hour freshness check
        is_fresh, norm_date = await evaluate_date_and_freshness(raw_date, job_url, db)
        if not is_fresh or not norm_date:
            logger.debug("[Remotive] Stale job (>24h or missing date): '%s' at '%s' (%s)", title[:30], company, raw_date)
            continue

        survived_24h += 1

        # Clean description HTML to readable text
        description = None
        raw_desc = job.get("description")
        if raw_desc:
            soup = BeautifulSoup(raw_desc, "lxml")
            description = soup.get_text(separator="\n", strip=True)

        role_family = _infer_role_family(category, title)

        did_save = await save_job(
            db=db,
            source_name=SOURCE_NAME,
            source_url=API_URL,
            title=title,
            company=company,
            published_date=norm_date,
            is_remote=True,  # Remotive is 100% remote
            job_url=job_url,
            role_family=role_family,
            description=description,
        )

        if did_save:
            saved += 1
            logger.info(
                "[Remotive #%d] Saved: %s at %s (date: %s, role: %s)",
                saved, title[:40], company, norm_date, role_family
            )

    logger.info(
        "Remotive complete: fetched %d raw, %d survived 24h filter, %d saved",
        raw_fetched, survived_24h, saved
    )
    return {"raw_fetched": raw_fetched, "survived_24h": survived_24h, "saved": saved}
