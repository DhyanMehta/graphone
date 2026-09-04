"""
SQLite storage layer for scraped records.

Each record type gets its own table. Deduplication:
  - research_papers: UNIQUE on paper_url (content.paper_url)
  - startups:        UNIQUE on (source_url, entity_name)
  - products:        UNIQUE on (source_url, startup_name)
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"


async def init_db(db_path: str) -> aiosqlite.Connection:
    """
    Open (or create) the SQLite database and ensure all tables exist.
    Returns the open connection.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    db = await aiosqlite.connect(str(path))
    # Enable WAL mode for better concurrent read performance
    await db.execute("PRAGMA journal_mode=WAL")

    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS research_papers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_version  TEXT    NOT NULL DEFAULT '1.0',
            record_type     TEXT    NOT NULL DEFAULT 'RESEARCH_PAPER',
            title           TEXT,
            authors         TEXT,           -- JSON array of strings
            paper_url       TEXT    NOT NULL UNIQUE,
            github_url      TEXT,
            github_stars    INTEGER,
            published_date  TEXT,           -- ISO-8601
            source_name     TEXT,
            collected_at    TEXT    NOT NULL -- ISO-8601
        );

        CREATE TABLE IF NOT EXISTS startups (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_version  TEXT    NOT NULL DEFAULT '1.0',
            record_type     TEXT    NOT NULL DEFAULT 'STARTUP',
            source_name     TEXT,
            source_url      TEXT    NOT NULL,
            entity_name     TEXT,
            employee_count  INTEGER,
            collected_at    TEXT    NOT NULL,
            UNIQUE(source_url, entity_name)
        );

        CREATE TABLE IF NOT EXISTS products (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_version  TEXT    NOT NULL DEFAULT '1.0',
            record_type     TEXT    NOT NULL DEFAULT 'PRODUCT',
            source_name     TEXT,
            source_url      TEXT    NOT NULL,
            startup_name    TEXT,
            pricing_model   TEXT,           -- FREE, FREEMIUM, PAID, ENTERPRISE, or NULL
            collected_at    TEXT    NOT NULL,
            UNIQUE(source_url, startup_name)
        );

        CREATE TABLE IF NOT EXISTS news (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_version  TEXT    NOT NULL DEFAULT '1.0',
            record_type     TEXT    NOT NULL DEFAULT 'NEWS',
            source_name     TEXT    NOT NULL,
            source_url      TEXT    NOT NULL,
            title           TEXT    NOT NULL,
            url             TEXT    NOT NULL UNIQUE,
            published_date  TEXT    NOT NULL,       -- ISO-8601 UTC
            author          TEXT,
            summary         TEXT,
            full_text       TEXT    NOT NULL,
            content_type    TEXT    NOT NULL DEFAULT 'FULL_TEXT', -- FULL_TEXT or SUMMARY
            collected_at    TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_version  TEXT    NOT NULL DEFAULT '1.0',
            record_type     TEXT    NOT NULL DEFAULT 'JOB',
            source_name     TEXT    NOT NULL,
            source_url      TEXT    NOT NULL,
            title           TEXT,           -- Nullable if source lacks a reliable title
            company         TEXT    NOT NULL,
            published_date  TEXT    NOT NULL,       -- ISO-8601 UTC
            is_remote       INTEGER NOT NULL DEFAULT 0,
            role_family     TEXT,
            job_url         TEXT    NOT NULL UNIQUE,
            description     TEXT,
            collected_at    TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS seen_urls (
            url             TEXT    PRIMARY KEY,
            first_seen_at   TEXT    NOT NULL
        );
        """
    )
    await db.commit()
    logger.info("Database initialized at %s", db_path)
    return db


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


async def save_research_paper(
    db: aiosqlite.Connection,
    title: Optional[str],
    authors: Optional[list[str]],
    paper_url: str,
    github_url: Optional[str] = None,
    github_stars: Optional[int] = None,
    published_date: Optional[str] = None,
    source_name: Optional[str] = None,
) -> bool:
    """
    Insert a research paper record. Returns True if inserted, False if
    duplicate (same paper_url already exists).
    """
    # Normalize paper_url: strip arxiv version suffixes (e.g. v1, v2)
    # This prevents the same paper getting two records if one source
    # has the version and the other doesn't.
    if paper_url and "arxiv.org/abs/" in paper_url:
        paper_url = re.sub(r"v\d+$", "", paper_url)
        
    # Normalize published_date to full ISO-8601 if it's just YYYY-MM-DD
    if published_date and len(published_date) == 10 and re.match(r"^\d{4}-\d{2}-\d{2}$", published_date):
        published_date = f"{published_date}T00:00:00Z"

    try:
        await db.execute(
            """
            INSERT OR IGNORE INTO research_papers
                (schema_version, record_type, title, authors, paper_url,
                 github_url, github_stars, published_date, source_name,
                 collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SCHEMA_VERSION,
                "RESEARCH_PAPER",
                title,
                json.dumps(authors) if authors else None,
                paper_url,
                github_url,
                github_stars,
                published_date,
                source_name,
                _now_iso(),
            ),
        )
        await db.commit()
        if db.total_changes > 0:
            return True
        return False
    except Exception as exc:
        logger.error("Failed to save research paper '%s': %s", paper_url, exc)
        return False


async def save_startup(
    db: aiosqlite.Connection,
    source_name: str,
    source_url: str,
    entity_name: Optional[str],
    employee_count: Optional[int] = None,
) -> bool:
    """Insert a startup record. Returns True if inserted, False if duplicate."""
    try:
        await db.execute(
            """
            INSERT OR IGNORE INTO startups
                (schema_version, record_type, source_name, source_url,
                 entity_name, employee_count, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SCHEMA_VERSION,
                "STARTUP",
                source_name,
                source_url,
                entity_name,
                employee_count,
                _now_iso(),
            ),
        )
        await db.commit()
        return True
    except Exception as exc:
        logger.error("Failed to save startup '%s': %s", entity_name, exc)
        return False


async def save_product(
    db: aiosqlite.Connection,
    source_name: str,
    source_url: str,
    startup_name: Optional[str],
    pricing_model: Optional[str] = None,
) -> bool:
    """Insert a product record. Returns True if inserted, False if duplicate."""
    # Validate pricing_model
    valid_models = {"FREE", "FREEMIUM", "PAID", "ENTERPRISE", None}
    if pricing_model not in valid_models:
        logger.warning(
            "Invalid pricing_model '%s' for '%s' — setting to null",
            pricing_model, startup_name,
        )
        pricing_model = None

    try:
        await db.execute(
            """
            INSERT OR IGNORE INTO products
                (schema_version, record_type, source_name, source_url,
                 startup_name, pricing_model, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SCHEMA_VERSION,
                "PRODUCT",
                source_name,
                source_url,
                startup_name,
                pricing_model,
                _now_iso(),
            ),
        )
        await db.commit()
        return True
    except Exception as exc:
        logger.error("Failed to save product '%s': %s", startup_name, exc)
        return False


async def save_news(
    db: aiosqlite.Connection,
    source_name: str,
    source_url: str,
    title: str,
    url: str,
    published_date: str,
    author: Optional[str] = None,
    summary: Optional[str] = None,
    full_text: Optional[str] = None,
    content_type: str = "FULL_TEXT",
) -> bool:
    """Insert a news article. Returns True if inserted, False if duplicate."""
    try:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO news
                (schema_version, record_type, source_name, source_url,
                 title, url, published_date, author, summary, full_text,
                 content_type, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SCHEMA_VERSION,
                "NEWS",
                source_name,
                source_url,
                title,
                url,
                published_date,
                author,
                summary,
                full_text if full_text else (summary or ""),
                content_type,
                _now_iso(),
            ),
        )
        await db.commit()
        return cursor.rowcount > 0
    except Exception as exc:
        logger.error("Failed to save news '%s': %s", url, exc)
        return False


async def save_job(
    db: aiosqlite.Connection,
    source_name: str,
    source_url: str,
    title: Optional[str],
    company: str,
    published_date: str,
    is_remote: bool,
    job_url: str,
    role_family: Optional[str] = None,
    description: Optional[str] = None,
) -> bool:
    """Insert a job posting. Returns True if inserted, False if duplicate."""
    try:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO jobs
                (schema_version, record_type, source_name, source_url,
                 title, company, published_date, is_remote, role_family,
                 job_url, description, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SCHEMA_VERSION,
                "JOB",
                source_name,
                source_url,
                title,
                company,
                published_date,
                1 if is_remote else 0,
                role_family,
                job_url,
                description,
                _now_iso(),
            ),
        )
        await db.commit()
        return cursor.rowcount > 0
    except Exception as exc:
        logger.error("Failed to save job '%s': %s", job_url, exc)
        return False


async def get_record_count(db: aiosqlite.Connection, table: str) -> int:
    """Return the number of records in the given table."""
    async with db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_all_records(
    db: aiosqlite.Connection, table: str, limit: int = 50
) -> list[dict]:
    """Fetch records from a table as a list of dicts (for display/debugging)."""
    async with db.execute(
        f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)
    ) as cursor:
        columns = [desc[0] for desc in cursor.description]
        rows = await cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]


def record_to_schema(record: dict, record_type: str) -> dict:
    """
    Convert a DB row dict into the exact output schema format.
    Used for display and export.
    """
    if record_type == "RESEARCH_PAPER":
        authors = record.get("authors")
        if isinstance(authors, str):
            try:
                authors = json.loads(authors)
            except json.JSONDecodeError:
                authors = None

        return {
            "schemaVersion": record.get("schema_version", SCHEMA_VERSION),
            "recordType": "RESEARCH_PAPER",
            "content": {
                "title": record.get("title"),
                "authors": authors,
                "paper_url": record.get("paper_url"),
                "github_url": record.get("github_url"),
                "github_stars": record.get("github_stars"),
                "published_date": record.get("published_date"),
            },
        }
    elif record_type == "STARTUP":
        return {
            "schemaVersion": record.get("schema_version", SCHEMA_VERSION),
            "recordType": "STARTUP",
            "source": {
                "name": record.get("source_name"),
                "url": record.get("source_url"),
            },
            "content": {
                "entityName": record.get("entity_name"),
                "data": {
                    "employeeCount": record.get("employee_count"),
                },
            },
            "collectedAt": record.get("collected_at"),
        }
    elif record_type == "PRODUCT":
        return {
            "schemaVersion": record.get("schema_version", SCHEMA_VERSION),
            "recordType": "PRODUCT",
            "source": {
                "name": record.get("source_name"),
                "url": record.get("source_url"),
            },
            "content": {
                "startupName": record.get("startup_name"),
                "pricingModel": record.get("pricing_model"),
            },
            "collectedAt": record.get("collected_at"),
        }
    elif record_type == "NEWS":
        return {
            "schemaVersion": record.get("schema_version", SCHEMA_VERSION),
            "recordType": "NEWS",
            "source": {
                "name": record.get("source_name"),
                "url": record.get("source_url"),
            },
            "content": {
                "title": record.get("title"),
                "url": record.get("url"),
                "published_date": record.get("published_date"),
                "author": record.get("author"),
                "summary": record.get("summary"),
                "full_text": record.get("full_text"),
                "content_type": record.get("content_type", "FULL_TEXT"),
            },
            "collectedAt": record.get("collected_at"),
        }
    elif record_type == "JOB":
        return {
            "schemaVersion": record.get("schema_version", SCHEMA_VERSION),
            "recordType": "JOB",
            "source": {
                "name": record.get("source_name"),
                "url": record.get("source_url"),
            },
            "content": {
                "title": record.get("title"),
                "company": record.get("company"),
                "date": record.get("published_date"),
                "is_remote": bool(record.get("is_remote")),
                "role_family": record.get("role_family"),
                "job_url": record.get("job_url"),
                "description": record.get("description"),
            },
            "collectedAt": record.get("collected_at"),
        }
    else:
        raise ValueError(f"Unknown record type: {record_type}")
