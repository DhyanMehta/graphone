"""
Graphone scraper — main entry point.

Usage:
    python -m src.scraper.main --sources arxiv --limit 30
    python -m src.scraper.main --sources paperswithcode --limit 30
    python -m src.scraper.main --sources arxiv,paperswithcode --limit 50
    python -m src.scraper.main  # all enabled sources, no limit
"""

import argparse
import asyncio
import json
import logging
import sys

from src.scraper.router import Router
from src.scraper.storage import (
    init_db,
    get_record_count,
    get_all_records,
    record_to_schema,
)
from src.scraper.sources.arxiv import scrape_arxiv
from src.scraper.sources.github_api import GitHubAPI
from src.scraper.sources.paperswithcode import scrape_paperswithcode
from src.scraper.sources.ycombinator import scrape_ycombinator
from src.scraper.sources.saashub import scrape_saashub

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def run_arxiv(router: Router, db, limit=None):
    """Run the Arxiv scraper."""
    source_cfg = router.get_source_config("arxiv")
    client = router.get_client("arxiv")

    categories = source_cfg.get("categories", ["cs.AI"])
    batch_size = source_cfg.get("batch_size", 200)

    count = await scrape_arxiv(
        client=client,
        db=db,
        categories=categories,
        batch_size=batch_size,
        limit=limit,
    )
    return count


async def run_paperswithcode(router: Router, db, limit=None):
    """Run the Papers with Code scraper."""
    client = router.get_client("paperswithcode")
    github_client = router.get_client("github")
    github_api = GitHubAPI(github_client)

    count = await scrape_paperswithcode(
        client=client,
        github_api=github_api,
        db=db,
        limit=limit,
    )
    return count


async def run_ycombinator(router: Router, db, limit=None):
    """Run the Y Combinator scraper."""
    client = router.get_client("ycombinator")
    count = await scrape_ycombinator(
        client=client,
        db=db,
        limit=limit,
    )
    return count


async def run_saashub(router: Router, db, limit=None):
    """Run the SaaSHub scraper."""
    client = router.get_client("saashub")
    count = await scrape_saashub(
        client=client,
        db=db,
        limit=limit,
    )
    return count


async def main():
    parser = argparse.ArgumentParser(description="Graphone Phase I Scraper")
    parser.add_argument(
        "--sources",
        type=str,
        default=None,
        help="Comma-separated source names to scrape (default: all enabled)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max records per source (for testing). Default: no limit.",
    )
    parser.add_argument(
        "--show-records",
        type=int,
        default=0,
        help="Number of records to display after scraping (default: 0)",
    )
    args = parser.parse_args()

    router = Router()
    global_cfg = router.get_global_config()
    db_path = global_cfg.get("db_path", "data/records.db")

    db = await init_db(db_path)

    # Determine which sources to run
    if args.sources:
        sources = [s.strip() for s in args.sources.split(",")]
    else:
        sources = router.get_enabled_sources()

    logger.info("Starting scraper for sources: %s", sources)
    if args.limit:
        logger.info("Limit per source: %d", args.limit)

    # Map source names to runner functions
    runners = {
        "arxiv": run_arxiv,
        "paperswithcode": run_paperswithcode,
        "ycombinator": run_ycombinator,
        "saashub": run_saashub,
    }

    try:
        for source in sources:
            if source not in runners:
                logger.warning("No scraper implemented for '%s', skipping", source)
                continue

            logger.info("=== Starting %s scraper ===", source)
            count = await runners[source](router, db, limit=args.limit)
            logger.info("=== %s complete: %d records saved ===", source, count)

        # Show summary
        for table in ["research_papers", "startups", "products"]:
            count = await get_record_count(db, table)
            if count > 0:
                logger.info("Total %s in database: %d", table, count)

        # Show records if requested
        if args.show_records > 0:
            for table, rtype in [
                ("research_papers", "RESEARCH_PAPER"),
                ("startups", "STARTUP"),
                ("products", "PRODUCT"),
            ]:
                count = await get_record_count(db, table)
                if count == 0:
                    continue
                records = await get_all_records(db, table, args.show_records)
                print("\n" + "=" * 80)
                print(f"LAST {min(args.show_records, len(records))} {rtype} RECORDS:")
                print("=" * 80)
                for r in records:
                    schema_rec = record_to_schema(r, rtype)
                    print(json.dumps(schema_rec, indent=2))
                    print("-" * 40)

    finally:
        await router.close_all()
        await db.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
