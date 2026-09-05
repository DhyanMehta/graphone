"""
Hacker News Hiring crawler.

Queries the official HN Algolia API for real-time hiring posts in the monthly
'Ask HN: Who is hiring?' thread, extracts verifiable company and role details,
validates 24-hour freshness, and persists to SQLite with unique comment/job URLs.
"""

import html
import logging
import re
from typing import Optional
from bs4 import BeautifulSoup
import aiosqlite

from src.scraper.http_client import HttpClient
from src.scraper.date_normalizer import evaluate_date_and_freshness
from src.scraper.storage import save_job

logger = logging.getLogger(__name__)

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
SOURCE_NAME = "Hacker News Hiring"


def _is_url_or_domain(segment: str) -> bool:
    """Detect if a pipe segment is an explicit URL or bare domain."""
    s = segment.strip()
    if re.search(r"https?://|www\.", s, re.IGNORECASE):
        return True
    if re.match(r"^[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(/.*)?$", s):
        return True
    return False


def _is_reliable_title(candidate: Optional[str]) -> bool:
    """
    Sanity check on candidate title:
    - Must have a role-ish keyword, OR be a capitalized multi-word title phrase.
    - Single generic words (e.g. 'VoiceAI', 'Python', 'Remote') fail and return False.
    """
    if not candidate:
        return False
    c = candidate.strip()

    non_titles = {
        "remote", "onsite", "on-site", "hybrid", "full-time", "part-time",
        "contract", "intern", "internship", "full time", "part time",
        "fulltime", "parttime", "webrtc", "voiceai"
    }
    if c.lower() in non_titles:
        return False
    if re.search(r"\$?\d+k\b", c.lower()) or "usd" in c.lower() or "eur" in c.lower() or "gbp" in c.lower():
        return False

    role_signals = [
        r"\b(engineer|engineering|developer|dev|software|architect|infrastructure)\b",
        r"\b(lead|leader|manager|management|director|head|vp|chief)\b",
        r"\b(specialist|trainer|analyst|scientist|researcher)\b",
        r"\b(administrator|consultant|designer|counsel|intern|internship|fellow)\b",
        r"\b(executive|coordinator|recruiter|writer|marketer|advocate|sre|qa)\b",
        r"\b(technician|operator|officer|associate|strategist)\b",
    ]
    if any(re.search(pat, c, re.IGNORECASE) for pat in role_signals):
        return True

    words = c.split()
    if len(words) >= 2:
        capitalized = sum(1 for w in words if w and w[0].isupper() and w.isalpha())
        if capitalized >= 2:
            return True

    return False


def _parse_hn_comment(raw_html: str) -> Optional[tuple[str, Optional[str], bool, Optional[str], str]]:
    """
    Extract (company, title, is_remote, role_family, full_text) from an HN hiring post.
    HN posts conventionally use 'Company | Role | Location | Remote' format in the header.

    Strict rules:
    - Strips URLs/domains before positional mapping.
    - Company must be a real company name parsed from text; never the poster username.
    - Title must pass the sanity check; if unreliable/generic, title is set to None (null).
    - If no company can be reliably identified, return None (skip comment).
    """
    unescaped = html.unescape(raw_html)

    # The header line in HN hiring posts is before the first <p> or <br>
    first_chunk = re.split(r"<p>|<br\s*/?>", unescaped, flags=re.IGNORECASE)[0]
    soup_header = BeautifulSoup(first_chunk, "lxml")
    header_text = soup_header.get_text(separator=" ", strip=True)

    if "|" not in header_text:
        return None

    raw_parts = [p.strip() for p in header_text.split("|") if p.strip()]
    if len(raw_parts) < 2:
        return None

    # Step 2: Strip any segment that's clearly a URL or bare domain before positional mapping
    parts = []
    for p in raw_parts:
        clean_p = re.sub(r"<[^>]+>", "", p).strip()
        if clean_p and not _is_url_or_domain(clean_p):
            parts.append(clean_p)

    if not parts:
        return None

    company = parts[0]
    greeting_patterns = [
        r"^hi\b", r"^hello\b", r"^we\s+are\b", r"^i\s+am\b", r"^i\s+run\b",
        r"^seeking\b", r"^looking\b", r"^ask\s+hn\b", r"^hiring\s*:\b"
    ]
    if any(re.search(pat, company, re.IGNORECASE) for pat in greeting_patterns):
        return None

    if len(company) > 60:
        return None

    soup_full = BeautifulSoup(unescaped, "lxml")
    full_text = soup_full.get_text(separator="\n", strip=True)
    is_remote = bool(re.search(r"\bremote\b", full_text, re.IGNORECASE))

    # Step 3: Find candidate title and apply sanity check
    candidate_title = None
    non_title_terms = {
        "remote", "onsite", "on-site", "hybrid", "full-time", "part-time",
        "contract", "intern", "internship", "full time", "part time",
        "fulltime", "parttime"
    }

    for p in parts[1:]:
        p_lower = p.lower()
        if p_lower in non_title_terms:
            continue
        if re.search(r"\$?\d+k\b", p_lower) or "usd" in p_lower or "eur" in p_lower or "gbp" in p_lower:
            continue
        # Apply sanity check
        if _is_reliable_title(p):
            candidate_title = p
            break

    # If no segment passes sanity check, candidate_title remains None (null)

    # Role family classification
    role_family = None
    comb = f"{candidate_title or ''} {full_text[:400]}".lower()
    if re.search(r"\b(engineer|engineering|developer|software|architect|infrastructure|devops|sre|systems|ml|ai|machine learning)\b", comb):
        role_family = "Engineering"
    elif re.search(r"\b(data scientist|data analyst|data science|analytics)\b", comb):
        role_family = "Data"
    elif re.search(r"\b(product manager|product management|pm)\b", comb):
        role_family = "Product"
    elif re.search(r"\b(designer|ui/ux|ux/ui|\bux\b|\bui\b|design)\b", comb):
        role_family = "Design"
    elif re.search(r"\b(sales|account executive|bdr|sdr)\b", comb):
        role_family = "Sales"
    elif re.search(r"\b(marketing|growth|content)\b", comb):
        role_family = "Marketing"

    return company, candidate_title, is_remote, role_family, full_text


async def scrape_hn_hiring(
    client: HttpClient,
    db: aiosqlite.Connection,
    limit: Optional[int] = 30,
) -> dict:
    """
    Scrape fresh hiring posts from Hacker News.

    Finds the active monthly 'Ask HN: Who is hiring?' thread, queries recent
    comments, evaluates 24-hour freshness, parses company and title without
    fallbacks, and saves to SQLite.

    Returns:
        Dict with metrics: raw_fetched, survived_24h, saved.
    """
    story_url = f"{ALGOLIA_SEARCH_URL}?tags=story,author_whoishiring&query=Ask+HN:+Who+is+hiring&hitsPerPage=1"
    logger.info("Discovering latest 'Ask HN: Who is hiring?' thread from %s", story_url)

    story_id = None
    try:
        story_data = await client.fetch_json(story_url)
        hits = story_data.get("hits", []) if isinstance(story_data, dict) else []
        if hits:
            story_id = str(hits[0].get("objectID"))
            logger.info("Found official Who is hiring thread: ID %s ('%s')", story_id, hits[0].get("title"))
    except Exception as exc:
        logger.warning("Could not fetch official story ID: %s; falling back to keyword search", exc)

    if story_id:
        query_url = f"{ALGOLIA_SEARCH_URL}?tags=comment,story_{story_id}&hitsPerPage=100"
    else:
        query_url = f"{ALGOLIA_SEARCH_URL}?tags=comment&query=hiring&hitsPerPage=50"

    logger.info("Fetching Hacker News comments from %s", query_url)
    raw_fetched = 0
    survived_24h = 0
    saved = 0

    try:
        data = await client.fetch_json(query_url)
        comments = data.get("hits", []) if isinstance(data, dict) else []
    except Exception as exc:
        logger.error("Failed to fetch Hacker News comments: %s", exc)
        return {"raw_fetched": 0, "survived_24h": 0, "saved": 0}

    logger.info("Discovered %d raw comments from HN Algolia API", len(comments))

    for hit in comments:
        if limit is not None and saved >= limit:
            break

        object_id = str(hit.get("objectID", "")).strip()
        if not object_id:
            continue

        if story_id and str(hit.get("parent_id")) != story_id:
            continue

        raw_fetched += 1

        job_url = f"https://news.ycombinator.com/item?id={object_id}"
        raw_date = hit.get("created_at") or hit.get("created_at_i")
        raw_comment = hit.get("comment_text", "")

        if not raw_comment or len(raw_comment) < 30:
            continue

        is_fresh, norm_date = await evaluate_date_and_freshness(raw_date, job_url, db)
        if not is_fresh or not norm_date:
            logger.debug("[HN Hiring] Stale post (>24h or missing date): ID %s (%s)", object_id, raw_date)
            continue

        survived_24h += 1

        parsed = _parse_hn_comment(raw_comment)
        if not parsed:
            logger.debug("[HN Hiring] Skipped comment %s: no reliable company/title", object_id)
            continue

        company, title, is_remote, role_family, description = parsed

        did_save = await save_job(
            db=db,
            source_name=SOURCE_NAME,
            source_url=job_url,
            title=title,
            company=company,
            published_date=norm_date,
            is_remote=is_remote,
            job_url=job_url,
            role_family=role_family,
            description=description,
        )

        if did_save:
            saved += 1
            logger.info(
                "[HN Hiring #%d] Saved: '%s' at '%s' (date: %s, role: %s, remote: %s)",
                saved, title, company, norm_date, role_family, is_remote
            )

    logger.info(
        "HN Hiring complete: fetched %d raw, %d survived 24h filter, %d saved",
        raw_fetched, survived_24h, saved
    )
    return {"raw_fetched": raw_fetched, "survived_24h": survived_24h, "saved": saved}
