"""
The Verge AI news crawler.

Fetches The Verge AI Atom feed, evaluates 24-hour publication freshness,
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

FEED_URL = "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"
SOURCE_NAME = "The Verge AI"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


async def scrape_theverge(
    client: HttpClient,
    db: aiosqlite.Connection,
    limit: Optional[int] = 30,
) -> dict:
    """
    Scrape fresh AI news articles from The Verge.

    Returns:
        Dict with metrics: raw_fetched, survived_24h, saved.
    """
    logger.info("Fetching The Verge AI feed from %s", FEED_URL)
    raw_fetched = 0
    survived_24h = 0
    saved = 0

    try:
        xml_text = await client.fetch(FEED_URL)
        root = ET.fromstring(xml_text)
        entries = root.findall("atom:entry", ATOM_NS)
    except Exception as exc:
        logger.error("Failed to fetch/parse The Verge feed: %s", exc)
        return {"raw_fetched": 0, "survived_24h": 0, "saved": 0}

    logger.info("Discovered %d raw entries in The Verge feed", len(entries))

    for entry in entries:
        if limit is not None and saved >= limit:
            break

        raw_fetched += 1

        title_elem = entry.find("atom:title", ATOM_NS)
        link_elem = entry.find("atom:link[@rel='alternate']", ATOM_NS)
        if link_elem is None:
            link_elem = entry.find("atom:link", ATOM_NS)
        id_elem = entry.find("atom:id", ATOM_NS)
        pub_elem = entry.find("atom:published", ATOM_NS)
        if pub_elem is None:
            pub_elem = entry.find("atom:updated", ATOM_NS)
        author_elem = entry.find("atom:author/atom:name", ATOM_NS)
        content_elem = entry.find("atom:content", ATOM_NS)

        url = link_elem.get("href") if link_elem is not None and link_elem.get("href") else None
        if not url and id_elem is not None and id_elem.text and id_elem.text.startswith("http"):
            url = id_elem.text.strip()

        if title_elem is None or not title_elem.text or not url:
            continue

        title = title_elem.text.strip()
        raw_date = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else None
        author = author_elem.text.strip() if author_elem is not None and author_elem.text else None

        # Clean summary
        summary = None
        if content_elem is not None and content_elem.text:
            soup_c = BeautifulSoup(content_elem.text, "lxml")
            summary = soup_c.get_text(separator=" ", strip=True)

        # 24-hour freshness check
        is_fresh, norm_date = await evaluate_date_and_freshness(raw_date, url, db)
        if not is_fresh or not norm_date:
            logger.debug("[The Verge] Stale article (>24h or missing date): '%s' (%s)", title[:40], raw_date)
            continue

        survived_24h += 1

        # Full-text content crawler
        full_text = None
        content_type = "FULL_TEXT"
        try:
            article_html = await client.fetch(url)
            art_soup = BeautifulSoup(article_html, "lxml")
            content_div = (
                art_soup.find("div", class_=lambda c: c and ("entry-content" in c or "duet--article" in c))
                or art_soup.find("article")
            )
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
            logger.warning("[The Verge] Could not fetch full text for '%s': %s", url, exc)
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
                "[The Verge #%d] Saved: %s (date: %s, type: %s, len: %d)",
                saved, title[:50], norm_date, content_type, len(full_text or "")
            )

    logger.info(
        "The Verge complete: fetched %d raw, %d survived 24h filter, %d saved",
        raw_fetched, survived_24h, saved
    )
    return {"raw_fetched": raw_fetched, "survived_24h": survived_24h, "saved": saved}
