"""
TechCrunch AI news crawler.

Fetches the AI category RSS feed, evaluates 24-hour publication freshness,
crawls full-text article content, and persists to SQLite.
"""

import logging
import xml.etree.ElementTree as ET
from typing import Optional
from bs4 import BeautifulSoup
import aiosqlite

from src.scraper.http_client import HttpClient
from src.scraper.date_normalizer import evaluate_date_and_freshness
from src.scraper.storage import save_news

logger = logging.getLogger(__name__)

FEED_URL = "https://techcrunch.com/category/artificial-intelligence/feed/"
SOURCE_NAME = "TechCrunch AI"


async def scrape_techcrunch(
    client: HttpClient,
    db: aiosqlite.Connection,
    limit: Optional[int] = 30,
) -> dict:
    """
    Scrape fresh AI news articles from TechCrunch.

    Returns:
        Dict with metrics: raw_fetched, survived_24h, saved.
    """
    logger.info("Fetching TechCrunch AI feed from %s", FEED_URL)
    raw_fetched = 0
    survived_24h = 0
    saved = 0

    try:
        xml_text = await client.fetch(FEED_URL)
        root = ET.fromstring(xml_text)
        items = root.findall(".//item")
    except Exception as exc:
        logger.error("Failed to fetch/parse TechCrunch feed: %s", exc)
        return {"raw_fetched": 0, "survived_24h": 0, "saved": 0}

    logger.info("Discovered %d raw items in TechCrunch feed", len(items))

    for item in items:
        if limit is not None and saved >= limit:
            break

        raw_fetched += 1

        title_elem = item.find("title")
        link_elem = item.find("link")
        pub_date_elem = item.find("pubDate")
        creator_elem = item.find("{http://purl.org/dc/elements/1.1/}creator")
        desc_elem = item.find("description")

        if title_elem is None or not title_elem.text or link_elem is None or not link_elem.text:
            continue

        title = title_elem.text.strip()
        url = link_elem.text.strip()
        raw_date = pub_date_elem.text.strip() if pub_date_elem is not None and pub_date_elem.text else None
        author = creator_elem.text.strip() if creator_elem is not None and creator_elem.text else None

        # Clean description/summary
        summary = None
        if desc_elem is not None and desc_elem.text:
            soup_desc = BeautifulSoup(desc_elem.text, "lxml")
            summary = soup_desc.get_text(separator=" ", strip=True)

        # 24-hour freshness check
        is_fresh, norm_date = await evaluate_date_and_freshness(raw_date, url, db)
        if not is_fresh or not norm_date:
            logger.debug("[TechCrunch] Stale article (>24h or missing date): '%s' (%s)", title[:40], raw_date)
            continue

        survived_24h += 1

        # Full-text content crawler
        full_text = None
        content_type = "FULL_TEXT"
        try:
            article_html = await client.fetch(url)
            art_soup = BeautifulSoup(article_html, "lxml")
            content_div = art_soup.find("div", class_="entry-content")
            if content_div:
                paragraphs = [
                    p.get_text(strip=True)
                    for p in content_div.find_all("p")
                    if len(p.get_text(strip=True)) > 20
                ]
                if paragraphs:
                    full_text = "\n\n".join(paragraphs)

            if not full_text or len(full_text) < 100:
                content_type = "SUMMARY"
                full_text = summary or title
        except Exception as exc:
            logger.warning("[TechCrunch] Could not fetch full text for '%s': %s", url, exc)
            content_type = "SUMMARY"
            full_text = summary or title

        did_save = await save_news(
            db=db,
            source_name=SOURCE_NAME,
            source_url=FEED_URL,
            title=title,
            url=url,
            published_date=norm_date,
            author=author,
            summary=summary,
            full_text=full_text,
            content_type=content_type,
        )

        if did_save:
            saved += 1
            logger.info(
                "[TechCrunch #%d] Saved: %s (date: %s, type: %s, len: %d)",
                saved, title[:50], norm_date, content_type, len(full_text or "")
            )

    logger.info(
        "TechCrunch complete: fetched %d raw, %d survived 24h filter, %d saved",
        raw_fetched, survived_24h, saved
    )
    return {"raw_fetched": raw_fetched, "survived_24h": survived_24h, "saved": saved}
