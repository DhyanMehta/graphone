"""
SaaSHub product scraper.

Discovers product slugs from category listing pages, then scrapes each
product's detail page for product name, company name, and pricing model.

Products with no clear associated company name are skipped per user
requirement (never guess a company name).
"""

import asyncio
import json
import logging
import re
from typing import Optional

import aiosqlite
from bs4 import BeautifulSoup

from src.scraper.http_client import HttpClient
from src.scraper.storage import save_product

logger = logging.getLogger(__name__)

BASE_URL = "https://www.saashub.com"

# Categories to scrape for product discovery
CATEGORIES = [
    "productivity",
    "project-management",
    "developer-tools",
    "crm",
    "communication",
    "marketing-platform",
    "ecommerce",
    "design-tools",
    "finance",
    "monitoring-tools",
    "analytics",
    "cloud-computing",
    "note-taking",
    "automation",
    "data-science",
]

# Pricing model mapping — normalize labels from SaaSHub to our enum
PRICING_MAP = {
    "free": "FREE",
    "open source": "FREE",
    "open-source": "FREE",
    "freemium": "FREEMIUM",
    "free trial": "FREEMIUM",
    "free plan": "FREEMIUM",
    "paid": "PAID",
    "commercial": "PAID",
    "subscription": "PAID",
    "enterprise": "ENTERPRISE",
}


def _normalize_pricing(raw: Optional[str]) -> Optional[str]:
    """
    Normalize a raw pricing string to our enum.
    Returns None if the pricing model can't be determined.
    """
    if not raw:
        return None

    raw_lower = raw.strip().lower()

    # Direct mapping
    if raw_lower in PRICING_MAP:
        return PRICING_MAP[raw_lower]

    # Substring matching
    for key, value in PRICING_MAP.items():
        if key in raw_lower:
            return value

    # Can't determine — return None (never guess)
    return None


async def _discover_products_from_category(
    client: HttpClient,
    category: str,
    max_pages: int = 5,
) -> list[str]:
    """
    Discover product slugs from a SaaSHub category listing page.

    Returns list of product slugs (e.g., ["notion", "slack", "asana"]).
    """
    slugs = []

    for page_num in range(1, max_pages + 1):
        if page_num == 1:
            url = f"{BASE_URL}/best-{category}-software"
        else:
            url = f"{BASE_URL}/best-{category}-software?page={page_num}"

        try:
            html = await client.fetch(url)
        except Exception as exc:
            logger.debug(
                "Failed to fetch category page %s (page %d): %s",
                category, page_num, exc,
            )
            break

        soup = BeautifulSoup(html, "lxml")

        # Find product links — they follow pattern /product-slug-alternatives
        # or direct product links
        found_any = False
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            # Match product links like /notion-alternatives or /notion
            # Category pages link to alternatives pages
            alt_match = re.match(r"^/([a-z0-9][\w.-]*)-alternatives$", href)
            if alt_match:
                slug = alt_match.group(1)
                if slug not in slugs and slug != "best":
                    slugs.append(slug)
                    found_any = True

        if not found_any:
            break

    logger.info(
        "Discovered %d product slugs from category '%s'", len(slugs), category
    )
    return slugs


def _extract_product_data(html: str, slug: str) -> Optional[dict]:
    """
    Extract product data from a SaaSHub product detail page.

    Returns dict with product_name, company_name, pricing_model, source_url
    or None if extraction fails.
    """
    soup = BeautifulSoup(html, "lxml")

    # Product name — from the context navbar or h1
    product_name = None

    # Look in the page title first
    title_tag = soup.find("title")
    if title_tag:
        title_text = title_tag.text.strip()
        # Pattern: "ProductName reviews. Is ProductName good? - SaaSHub"
        # or "ProductName Alternatives & Competitors - SaaSHub"
        match = re.match(r"^(.+?)\s+(?:reviews|alternatives)", title_text, re.IGNORECASE)
        if match:
            product_name = match.group(1).strip()

    # Fallback: look for the product name in context navbar
    if not product_name:
        navbar_name = soup.find("div", class_="font-bold", string=True)
        if navbar_name:
            product_name = navbar_name.text.strip()

    if not product_name:
        return None

    # Company name — SaaSHub typically uses the product name as the company
    # name since it's a product directory. We use the product name as the
    # company/startup name. If it's generic or unclear, skip it.
    company_name = product_name

    # Pricing model — look for pricing section
    pricing_model = None
    pricing_section = soup.find("span", string=re.compile(r"Pricing:", re.IGNORECASE))
    if pricing_section:
        parent = pricing_section.parent
        if parent:
            pricing_text = parent.get_text(separator=" ", strip=True)
            # Remove the "Pricing:" label
            pricing_text = re.sub(r"^Pricing:\s*", "", pricing_text, flags=re.IGNORECASE)
            # Look for pricing badges/labels
            for badge in parent.find_all(["span", "a", "div"]):
                badge_text = badge.get_text(strip=True)
                normalized = _normalize_pricing(badge_text)
                if normalized:
                    pricing_model = normalized
                    break

    # Also check for schema.org Offer pricing
    offers = soup.find(attrs={"itemtype": "http://schema.org/Offer"})
    if offers and not pricing_model:
        price_el = offers.find(attrs={"itemprop": "price"})
        if price_el:
            price_text = price_el.get_text(strip=True)
            if price_text == "0" or "free" in price_text.lower():
                pricing_model = "FREE"

    source_url = f"{BASE_URL}/{slug}"

    return {
        "product_name": product_name,
        "company_name": company_name,
        "pricing_model": pricing_model,
        "source_url": source_url,
    }


async def scrape_saashub(
    client: HttpClient,
    db: aiosqlite.Connection,
    limit: Optional[int] = None,
) -> int:
    """
    Scrape products from SaaSHub.

    Args:
        client: HttpClient configured with SaaSHub rate limits.
        db: Open database connection.
        limit: Max products to fetch (for testing). None = no limit.

    Returns:
        Total number of products saved.
    """
    total_saved = 0

    # Phase 1: Discover product slugs from category pages
    all_slugs = []
    for category in CATEGORIES:
        if limit is not None and len(all_slugs) >= limit * 2:
            break
        slugs = await _discover_products_from_category(
            client, category, max_pages=3
        )
        for slug in slugs:
            if slug not in all_slugs:
                all_slugs.append(slug)

    logger.info("Total unique product slugs discovered: %d", len(all_slugs))

    # Phase 2: Scrape individual product pages
    for slug in all_slugs:
        if limit is not None and total_saved >= limit:
            logger.info("Reached limit of %d products, stopping", limit)
            break

        url = f"{BASE_URL}/{slug}"
        try:
            html = await client.fetch(url)
        except Exception as exc:
            logger.debug("Failed to fetch product %s: %s", slug, exc)
            continue

        product = _extract_product_data(html, slug)
        if product is None:
            logger.debug("Could not extract data from product %s", slug)
            continue

        # Skip products with no clear company name (never guess)
        if not product.get("company_name"):
            logger.debug("Skipping product %s — no company name", slug)
            continue

        saved = await save_product(
            db=db,
            source_name="SaaSHub",
            source_url=product["source_url"],
            startup_name=product["company_name"],
            pricing_model=product.get("pricing_model"),
        )

        if saved:
            total_saved += 1
            if total_saved % 20 == 0 or total_saved <= 5:
                logger.info(
                    "[SaaSHub] Saved #%d: %s (pricing: %s)",
                    total_saved,
                    product["product_name"],
                    product.get("pricing_model", "unknown"),
                )

    logger.info("SaaSHub scraping complete: %d products saved", total_saved)
    return total_saved
