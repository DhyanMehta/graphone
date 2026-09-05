"""
MIT News AI news crawler.

Fetches the MIT News AI RSS feed, evaluates 24-hour publication freshness,
crawls full-text article content, and persists to SQLite.
"""

import logging
import xml.etree.ElementTree as ET
from typing import Optional
from bs4 import BeautifulSoup
import aiosqlite

from src.scraper.http_client import HttpClient
from src.scraper.browser_client import BrowserClient
from src.scraper.date_normalizer import evaluate_date_and_freshness
from src.scraper.storage import save_news

logger = logging.getLogger(__name__)

FEED_URL = "https://news.mit.edu/rss/topic/artificial-intelligence2"
SOURCE_NAME = "MIT News AI"


async def scrape_mit_news(
    client: HttpClient,
    db: aiosqlite.Connection,
    limit: Optional[int] = 30,
    browser_client: Optional[BrowserClient] = None,
) -> dict:
    """
    Scrape fresh AI news articles from MIT News.

    Returns:
        Dict with metrics: raw_fetched, survived_24h, saved.
    """
    logger.info("Fetching MIT News AI feed from %s", FEED_URL)
    raw_fetched = 0
    survived_24h = 0
    saved = 0

    items = []
    try:
        xml_text = await client.fetch(FEED_URL)
        root = ET.fromstring(xml_text)
        items = root.findall(".//item")
    except Exception as exc:
        logger.warning("aiohttp blocked or failed on MIT News (%s) — falling back to Playwright browser", exc)
        try:
            should_close = False
            if browser_client is None:
                browser_client = BrowserClient()
                should_close = True
            rendered = await browser_client.fetch(FEED_URL)
            soup = BeautifulSoup(rendered, "lxml")
            pre = soup.find("pre")
            raw_xml = pre.text if pre else rendered
            root = ET.fromstring(raw_xml)
            items = root.findall(".//item")
            if should_close:
                await browser_client.close()
        except Exception as b_exc:
            logger.error("Playwright also failed on MIT News: %s", b_exc)
            return {"raw_fetched": 0, "survived_24h": 0, "saved": 0}

    logger.info("Discovered %d raw items in MIT News feed", len(items))

    for item in items:
        if limit is not None and saved >= limit:
            break

        raw_fetched += 1

        title_elem = item.find("title")
        link_elem = item.find("link")
        pub_date_elem = item.find("pubDate")
        desc_elem = item.find("description")

        if title_elem is None or not title_elem.text or link_elem is None or not link_elem.text:
            continue

        title = title_elem.text.strip()
        url = link_elem.text.strip()
        raw_date = pub_date_elem.text.strip() if pub_date_elem is not None and pub_date_elem.text else None

        # Clean description/summary
        summary = None
        if desc_elem is not None and desc_elem.text:
            soup_desc = BeautifulSoup(desc_elem.text, "lxml")
            summary = soup_desc.get_text(separator=" ", strip=True)

        # 24-hour freshness check
        is_fresh, norm_date = await evaluate_date_and_freshness(raw_date, url, db)
        if not is_fresh or not norm_date:
            logger.debug("[MIT News] Stale article (>24h or missing date): '%s' (%s)", title[:40], raw_date)
            continue

        survived_24h += 1

        # Full-text content crawler
        full_text = None
        author = "MIT News Office"
        content_type = "FULL_TEXT"
        try:
            article_html = await client.fetch(url)
            art_soup = BeautifulSoup(article_html, "lxml")

            # Extract author if available
            author_tag = art_soup.find("span", class_="news-article--author")
            if author_tag:
                author = author_tag.get_text(strip=True)

            content_div = art_soup.find("div", class_="news-article--content") or art_soup.find("article")
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
            logger.warning("[MIT News] Could not fetch full text for '%s': %s", url, exc)
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
                "[MIT News #%d] Saved: %s (date: %s, type: %s, len: %d)",
                saved, title[:50], norm_date, content_type, len(full_text or "")
            )

    logger.info(
        "MIT News complete: fetched %d raw, %d survived 24h filter, %d saved",
        raw_fetched, survived_24h, saved
    )
    return {"raw_fetched": raw_fetched, "survived_24h": survived_24h, "saved": saved}
