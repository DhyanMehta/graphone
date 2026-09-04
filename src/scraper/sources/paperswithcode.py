"""
Papers with Code scraper — extracts research papers from paperswithcode.co.

Uses sequential paper IDs (e.g., /paper/98456) and extracts structured data
from server-rendered HTML containing JSON-LD and meta tags. Enriches each
paper with live GitHub star counts via the GitHub API.
"""

import asyncio
import json
import logging
import re
from typing import Optional

import aiosqlite
from bs4 import BeautifulSoup

from src.scraper.http_client import HttpClient
from src.scraper.sources.github_api import GitHubAPI
from src.scraper.storage import save_research_paper

logger = logging.getLogger(__name__)

BASE_URL = "https://paperswithcode.co"


def _extract_from_jsonld(html: str) -> Optional[dict]:
    """
    Extract paper metadata from the JSON-LD script tag.

    The JSON-LD contains a @graph with a ScholarlyArticle object that has:
    - headline (title)
    - author[].name (authors)
    - datePublished
    - codeRepository[] (GitHub URLs)
    - url (paper URL on PwC)
    - sameAs[] (external links, e.g., ArXiv)
    """
    soup = BeautifulSoup(html, "lxml")

    # Try JSON-LD first — most reliable structured data
    for script_tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script_tag.string)
        except (json.JSONDecodeError, TypeError):
            continue

        graph = data.get("@graph", [data])
        for item in graph:
            if item.get("@type") == "ScholarlyArticle":
                return _parse_scholarly_article(item)

    return None


def _parse_scholarly_article(item: dict) -> dict:
    """Parse a ScholarlyArticle JSON-LD object into our paper format."""
    title = item.get("headline")

    # Authors — may be list of dicts or list of strings
    raw_authors = item.get("author", [])
    authors = []
    for a in raw_authors:
        if isinstance(a, dict):
            name = a.get("name")
            if name:
                authors.append(name)
        elif isinstance(a, str):
            authors.append(a)

    # Published date
    published_date = item.get("datePublished")

    # Paper URL — prefer sameAs ArXiv link, fall back to PwC URL
    paper_url = item.get("url")
    same_as = item.get("sameAs", [])
    if isinstance(same_as, list):
        for sa in same_as:
            if isinstance(sa, str) and "arxiv.org" in sa:
                paper_url = sa
                break

    # GitHub repos — first one from codeRepository
    github_url = None
    repos = item.get("codeRepository", [])
    if isinstance(repos, list):
        for repo in repos:
            if isinstance(repo, str) and "github.com" in repo.lower():
                github_url = repo
                break

    return {
        "title": title,
        "authors": authors if authors else None,
        "paper_url": paper_url,
        "published_date": published_date,
        "github_url": github_url,
    }


def _extract_from_meta(html: str) -> Optional[dict]:
    """
    Fallback extraction using <meta> tags and page structure.
    Used when JSON-LD is not available or incomplete.
    """
    soup = BeautifulSoup(html, "lxml")

    # Title from citation_title meta or <title>
    title = None
    citation_title = soup.find("meta", {"name": "citation_title"})
    if citation_title:
        title = citation_title.get("content")
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.text.strip()
            # Remove " | Papers with Code" suffix
            title = re.sub(r"\s*\|\s*Papers with Code\s*$", "", title)

    # Authors from citation_author meta tags
    authors = []
    for meta in soup.find_all("meta", {"name": "citation_author"}):
        name = meta.get("content")
        if name:
            authors.append(name)

    # Published date from citation_publication_date
    published_date = None
    date_meta = soup.find("meta", {"name": "citation_publication_date"})
    if date_meta:
        raw = date_meta.get("content", "")
        # Convert YYYY/MM/DD to ISO-8601
        published_date = raw.replace("/", "-")

    # Paper URL — canonical link
    paper_url = None
    canonical = soup.find("link", {"rel": "canonical"})
    if canonical:
        paper_url = canonical.get("href")

    # GitHub links — look for github.com links in the page
    github_url = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "github.com" in href and "/issues" not in href:
            github_url = href
            break

    if not title and not paper_url:
        return None

    return {
        "title": title,
        "authors": authors if authors else None,
        "paper_url": paper_url,
        "published_date": published_date,
        "github_url": github_url,
    }


async def _scrape_single_paper(
    client: HttpClient,
    paper_id: int,
) -> Optional[dict]:
    """
    Fetch and parse a single paper page.
    Returns paper dict or None if the page doesn't exist / fails to parse.
    """
    url = f"{BASE_URL}/paper/{paper_id}"
    try:
        html = await client.fetch(url)
    except Exception as exc:
        logger.debug("Failed to fetch paper %d: %s", paper_id, exc)
        return None

    # Try JSON-LD first, then fall back to meta tags
    paper = _extract_from_jsonld(html)
    if paper is None:
        paper = _extract_from_meta(html)

    if paper is None:
        logger.debug("Could not extract data from paper %d", paper_id)
        return None

    # Ensure paper_url is set (use the PwC URL if nothing better found)
    if not paper.get("paper_url"):
        paper["paper_url"] = url

    paper["source_url"] = url
    return paper


async def scrape_paperswithcode(
    client: HttpClient,
    github_api: GitHubAPI,
    db: aiosqlite.Connection,
    start_id: int = 98456,
    limit: Optional[int] = None,
    direction: str = "backward",
) -> int:
    """
    Scrape papers from Papers with Code by iterating paper IDs.

    Args:
        client: HttpClient configured with PwC rate limits.
        github_api: GitHubAPI instance for star count enrichment.
        db: Open database connection.
        start_id: Paper ID to start from (defaults to a recent known paper).
        limit: Max papers to fetch (for testing). None = no limit.
        direction: "backward" (decrement IDs) or "forward" (increment IDs).

    Returns:
        Total number of papers saved.
    """
    total_saved = 0
    consecutive_misses = 0
    max_consecutive_misses = 20  # Stop after 20 consecutive 404s

    current_id = start_id
    step = -1 if direction == "backward" else 1

    while True:
        if limit is not None and total_saved >= limit:
            logger.info("Reached limit of %d papers, stopping", limit)
            break

        if consecutive_misses >= max_consecutive_misses:
            logger.info(
                "Hit %d consecutive misses, stopping (last ID: %d)",
                max_consecutive_misses, current_id,
            )
            break

        paper = await _scrape_single_paper(client, current_id)

        if paper is None:
            consecutive_misses += 1
            current_id += step
            continue

        consecutive_misses = 0

        # Enrich with GitHub stars if a repo URL was found
        github_stars = None
        if paper.get("github_url"):
            github_stars = await github_api.get_stars(paper["github_url"])

        saved = await save_research_paper(
            db=db,
            title=paper["title"],
            authors=paper["authors"],
            paper_url=paper["paper_url"],
            github_url=paper.get("github_url"),
            github_stars=github_stars,
            published_date=paper.get("published_date"),
            source_name="Papers with Code",
        )

        if saved:
            total_saved += 1
            logger.info(
                "[PwC #%d] Saved: %s (GitHub: %s, stars: %s)",
                current_id,
                paper.get("title", "")[:60],
                paper.get("github_url", "none"),
                github_stars,
            )

        current_id += step

    logger.info("Papers with Code scraping complete: %d papers saved", total_saved)
    return total_saved
