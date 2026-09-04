"""
Arxiv scraper — fetches research papers via the official Atom export API.

Uses export.arxiv.org/api/query with high max_results per batch so the
15-second crawl-delay is amortized across many papers per API call.
Papers from Arxiv have no GitHub links, so github_url and github_stars
are always null.
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Optional

import aiosqlite

from src.scraper.http_client import HttpClient
from src.scraper.storage import save_research_paper

logger = logging.getLogger(__name__)

# Atom/OpenSearch namespaces used in Arxiv API responses
ATOM_NS = "http://www.w3.org/2005/Atom"
OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"
ARXIV_NS = "http://arxiv.org/schemas/atom"

API_BASE = "https://export.arxiv.org/api/query"


def _parse_entries(xml_text: str) -> tuple[list[dict], int]:
    """
    Parse Arxiv Atom XML response and extract paper entries.

    Returns:
        (papers, total_results): list of paper dicts and total count
    """
    root = ET.fromstring(xml_text)

    # Get total results for pagination
    total_el = root.find(f"{{{OPENSEARCH_NS}}}totalResults")
    total_results = int(total_el.text) if total_el is not None else 0

    papers = []
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        # Title
        title_el = entry.find(f"{{{ATOM_NS}}}title")
        title = title_el.text.strip().replace("\n", " ") if title_el is not None else None

        # Authors
        authors = []
        for author_el in entry.findall(f"{{{ATOM_NS}}}author"):
            name_el = author_el.find(f"{{{ATOM_NS}}}name")
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        # Published date (ISO-8601)
        pub_el = entry.find(f"{{{ATOM_NS}}}published")
        published_date = None
        if pub_el is not None and pub_el.text:
            # Arxiv returns full ISO datetime, extract date portion
            published_date = pub_el.text.strip()

        # Paper URL — the abs link
        paper_url = None
        id_el = entry.find(f"{{{ATOM_NS}}}id")
        if id_el is not None and id_el.text:
            paper_url = id_el.text.strip()
            # Normalize to https abs URL without version suffix
            paper_url = paper_url.replace("http://", "https://")

        if paper_url:
            papers.append(
                {
                    "title": title,
                    "authors": authors if authors else None,
                    "paper_url": paper_url,
                    "published_date": published_date,
                    # Arxiv doesn't link GitHub repos — always null
                    "github_url": None,
                    "github_stars": None,
                }
            )

    return papers, total_results


async def scrape_arxiv(
    client: HttpClient,
    db: aiosqlite.Connection,
    categories: list[str],
    batch_size: int = 200,
    limit: Optional[int] = None,
) -> int:
    """
    Scrape papers from Arxiv export API.

    Args:
        client: HttpClient configured with Arxiv rate limits.
        db: Open database connection.
        categories: List of Arxiv category codes (e.g., ["cs.AI", "cs.LG"]).
        batch_size: Number of papers per API call (high = fewer round trips,
                    so the 15s delay is per-batch not per-paper).
        limit: Optional max total papers to fetch (for testing). None = no limit.

    Returns:
        Total number of papers saved.
    """
    total_saved = 0

    for category in categories:
        logger.info("Scraping Arxiv category: %s", category)
        start = 0
        category_saved = 0

        while True:
            # Check global limit
            if limit is not None and total_saved >= limit:
                logger.info("Reached limit of %d papers, stopping", limit)
                return total_saved

            # How many to fetch this batch
            fetch_count = batch_size
            if limit is not None:
                remaining = limit - total_saved
                fetch_count = min(batch_size, remaining)

            params = {
                "search_query": f"cat:{category}",
                "start": str(start),
                "max_results": str(fetch_count),
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }

            logger.info(
                "Fetching Arxiv batch: category=%s, start=%d, max_results=%d",
                category, start, fetch_count,
            )

            try:
                xml_text = await client.fetch(API_BASE, params=params)
                papers, total_results = _parse_entries(xml_text)
            except Exception as exc:
                logger.error(
                    "Failed to fetch Arxiv batch (category=%s, start=%d): %s",
                    category, start, exc,
                )
                break

            if not papers:
                logger.info(
                    "No more papers in category %s (fetched %d total)",
                    category, category_saved,
                )
                break

            # Save each paper
            for paper in papers:
                saved = await save_research_paper(
                    db=db,
                    title=paper["title"],
                    authors=paper["authors"],
                    paper_url=paper["paper_url"],
                    github_url=paper["github_url"],
                    github_stars=paper["github_stars"],
                    published_date=paper["published_date"],
                    source_name="Arxiv",
                )
                if saved:
                    category_saved += 1
                    total_saved += 1

            logger.info(
                "Arxiv %s: saved %d papers this batch (total: %d, API total: %d)",
                category, len(papers), category_saved, total_results,
            )

            start += len(papers)

            # If we got fewer papers than requested, we've hit the end
            if len(papers) < fetch_count:
                break

            # Pagination: if we've fetched all available results
            if start >= total_results:
                break

        logger.info(
            "Finished Arxiv category %s: %d papers saved", category, category_saved
        )

    logger.info("Arxiv scraping complete: %d total papers saved", total_saved)
    return total_saved
