"""
Date Normalizer for Phase II Signal Ingestion.

Handles:
- Tier 1: Standard ISO-8601, RFC-2822 / RFC-822, and Unix timestamps.
- Tier 2: Relative dates ("2 hours ago", "45 minutes ago", "today", "yesterday").
- Tier 3: Heuristic for sources with missing date metadata via persistent SQLite seen_urls table.
- 24-hour freshness enforcement against current UTC time.
"""

import logging
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional, Tuple
import aiosqlite

logger = logging.getLogger(__name__)


def parse_datetime_to_utc(raw_date: str | int | float) -> Optional[datetime]:
    """
    Parse a variety of timestamp formats into an offset-aware UTC datetime.
    Returns None if unparseable.
    """
    if raw_date is None:
        return None

    # 1. Numeric / Unix epoch timestamp (int, float, or numeric string)
    if isinstance(raw_date, (int, float)):
        try:
            return datetime.fromtimestamp(raw_date, tz=timezone.utc)
        except Exception:
            return None

    s = str(raw_date).strip()
    if not s:
        return None

    if s.isdigit():
        try:
            return datetime.fromtimestamp(int(s), tz=timezone.utc)
        except Exception:
            pass

    # 2. Relative dates (Tier 2)
    s_lower = s.lower()
    now_utc = datetime.now(timezone.utc)

    # "X hours/minutes/seconds/days ago"
    match_ago = re.match(
        r"(\d+)\s+(second|sec|minute|min|hour|hr|day|week|month)s?\s+ago",
        s_lower,
    )
    if match_ago:
        val = int(match_ago.group(1))
        unit = match_ago.group(2)
        if unit in ("second", "sec"):
            return now_utc - timedelta(seconds=val)
        elif unit in ("minute", "min"):
            return now_utc - timedelta(minutes=val)
        elif unit in ("hour", "hr"):
            return now_utc - timedelta(hours=val)
        elif unit == "day":
            return now_utc - timedelta(days=val)
        elif unit == "week":
            return now_utc - timedelta(weeks=val)
        elif unit == "month":
            return now_utc - timedelta(days=val * 30)

    if s_lower in ("just now", "moments ago", "active today", "today"):
        return now_utc
    if s_lower == "yesterday":
        return now_utc - timedelta(days=1)

    # 3. RFC-2822 / RFC-822 (standard in RSS feeds, e.g. 'Fri, 04 Sep 2026 17:18:00 +0000')
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # 4. ISO-8601 (e.g. '2026-09-04T13:51:35-04:00' or '2026-09-04T13:51:35Z')
    clean_iso = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(clean_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # 5. Fallback for date-only 'YYYY-MM-DD'
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        try:
            dt = datetime.strptime(s, "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass

    # 6. Fallback for 'YYYY-MM-DD HH:MM:SS'
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass

    logger.debug("Could not parse date: '%s'", raw_date)
    return None


def format_iso_utc(dt: datetime) -> str:
    """Format a datetime as ISO-8601 UTC string (YYYY-MM-DDTHH:MM:SSZ)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def is_within_24_hours(
    dt: datetime,
    now_utc: Optional[datetime] = None,
    max_future_seconds: int = 3600,
) -> bool:
    """
    Returns True if dt is within the last 24 hours (dt >= now - 24h).
    Rejects dates too far in the future (> 1 hour ahead to tolerate slight clock skew).
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    cutoff = now_utc - timedelta(hours=24)
    future_limit = now_utc + timedelta(seconds=max_future_seconds)

    return cutoff <= dt <= future_limit


async def evaluate_date_and_freshness(
    raw_date: Optional[str | int | float],
    url: str,
    db: Optional[aiosqlite.Connection] = None,
    now_utc: Optional[datetime] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Evaluates date and 24-hour freshness.

    Returns:
        (is_fresh: bool, normalized_iso_date: Optional[str])

    - If raw_date is present and parsable:
        Evaluates against the 24-hour window.
    - If raw_date is missing/unparseable and db is provided (Tier 3 heuristic):
        Checks persistent seen_urls. First time seen -> marked fresh with current time.
        Previously seen -> checked if first_seen_at was within 24h.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    # If an explicit date is provided, parse it
    if raw_date is not None:
        dt = parse_datetime_to_utc(raw_date)
        if dt is not None:
            is_fresh = is_within_24_hours(dt, now_utc)
            return is_fresh, format_iso_utc(dt)

    # Tier 3: Heuristic for missing dates
    if db is not None and url:
        return await _tier3_seen_url_heuristic(db, url, now_utc)

    return False, None


async def _tier3_seen_url_heuristic(
    db: aiosqlite.Connection,
    url: str,
    now_utc: datetime,
) -> Tuple[bool, str]:
    """
    Persistent seen_urls heuristic for listings missing publication dates.
    First time seen: inserted with now_utc and deemed fresh.
    Previously seen: checked against first_seen_at.
    """
    now_iso = format_iso_utc(now_utc)
    try:
        cursor = await db.execute(
            "SELECT first_seen_at FROM seen_urls WHERE url = ?", (url,)
        )
        row = await cursor.fetchone()
        if row:
            first_seen_iso = row[0]
            first_seen_dt = parse_datetime_to_utc(first_seen_iso)
            if first_seen_dt and is_within_24_hours(first_seen_dt, now_utc):
                return True, first_seen_iso
            return False, first_seen_iso

        # New URL: record as first seen now
        await db.execute(
            "INSERT OR IGNORE INTO seen_urls (url, first_seen_at) VALUES (?, ?)",
            (url, now_iso),
        )
        await db.commit()
        return True, now_iso
    except Exception as exc:
        logger.warning("Error in seen_urls heuristic for '%s': %s", url, exc)
        return True, now_iso
