"""
Intelligent content truncation for LLM payloads.

Goal: Keep payloads under provider token limits (avoid 413 errors) while
preserving the most semantically dense content for extraction quality.

Hard ceiling: ~4000 tokens of input text per call. At ~4 chars/token for
English, that's ~16,000 characters. We use 12,000 as a conservative limit
to leave room for system prompt + response tokens.

Strategy per content type (commented inline):
"""

import re
from typing import Optional

# Conservative character limit (~3000 tokens of content, leaving headroom
# for system prompt ~500 tokens and response ~500 tokens)
MAX_CONTENT_CHARS = 12_000

# Minimum useful content length — below this, extraction is unlikely
# to produce meaningful results
MIN_CONTENT_CHARS = 30


def chunk_news_article(full_text: str, title: Optional[str] = None) -> str:
    """Truncate a news article for LLM summarization.

    Strategy: Inverted-pyramid journalism structure. News articles
    front-load the most important facts in the opening paragraphs
    (lede + context). The conclusion/final paragraph often contains
    a forward-looking statement or summary. Middle content is the
    least information-dense (supporting quotes, background).

    Approach:
      1. Keep all content if it fits within the limit.
      2. If too long: keep first 3 paragraphs + last paragraph,
         insert a "[...content truncated...]" marker in between.
    """
    if not full_text or len(full_text) < MIN_CONTENT_CHARS:
        return full_text or ""

    # Prepend title as context if available
    text = f"Title: {title}\n\n{full_text}" if title else full_text

    if len(text) <= MAX_CONTENT_CHARS:
        return text

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) <= 4:
        # Few paragraphs — just hard-truncate
        return text[:MAX_CONTENT_CHARS] + "\n[...truncated...]"

    # Keep first 3 paragraphs (lede + context) + last paragraph (conclusion)
    head = "\n\n".join(paragraphs[:3])
    tail = paragraphs[-1]
    result = f"{head}\n\n[...content truncated for brevity...]\n\n{tail}"

    # If still too long (very long paragraphs), hard-truncate
    if len(result) > MAX_CONTENT_CHARS:
        result = result[:MAX_CONTENT_CHARS] + "\n[...truncated...]"

    return result


def chunk_hn_comment(raw_html: str) -> str:
    """Truncate an HN hiring comment for LLM extraction.

    Strategy: HN hiring comments follow a consistent structure:
      Line 1: Company | Title | Location | Remote (the header — CRITICAL)
      Body: Role description, requirements, compensation, apply link

    The header contains all structured fields we need. The body provides
    context for role_family classification but diminishes in value after
    the first ~500 chars (requirements/stack section).

    Approach:
      1. Always preserve the full header line intact.
      2. Keep first 500 chars of the body (requirements/stack/compensation).
      3. Truncate the remainder.
    """
    if not raw_html or len(raw_html) < MIN_CONTENT_CHARS:
        return raw_html or ""

    # Strip HTML tags for cleaner LLM input
    text = re.sub(r"<[^>]+>", "\n", raw_html)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) <= MAX_CONTENT_CHARS:
        return text

    # Split into header (first line/paragraph) and body
    parts = text.split("\n", 1)
    header = parts[0]
    body = parts[1] if len(parts) > 1 else ""

    # Keep header + first 500 chars of body
    body_limit = max(500, MAX_CONTENT_CHARS - len(header) - 50)
    if len(body) > body_limit:
        body = body[:body_limit] + "\n[...truncated...]"

    return f"{header}\n{body}"


def chunk_generic_html(raw_html: str) -> str:
    """Truncate generic HTML/text for LLM fallback extraction.

    Strategy: Strip all HTML tags, collapse whitespace, and take the
    first N characters. This is the least sophisticated strategy,
    used only when source-specific chunking doesn't apply.

    No semantic prioritization — we simply trust that the field we're
    looking for appears early in the document (usually true for
    structured pages like job boards and product listings).
    """
    if not raw_html or len(raw_html) < MIN_CONTENT_CHARS:
        return raw_html or ""

    # Strip HTML tags
    text = re.sub(r"<[^>]+>", " ", raw_html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) <= MAX_CONTENT_CHARS:
        return text

    # Hard truncate at character limit
    return text[:MAX_CONTENT_CHARS] + " [...]"
