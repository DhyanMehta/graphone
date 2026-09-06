"""
LLM extraction orchestration layer.

Implements both strategies:
  A) LLM fallback for existing deterministic parsers — only triggered on
     genuine failure/empty result, never redundantly on successful parses.
  B) LLM as primary extraction:
     B.1) HN Hiring comment → structured job fields
     B.2) News full_text → LLM-generated summary

Every call ties back to a real source record (source_url parameter).
Every fallback trigger is logged with: source, field, reason, outcome.
"""

import json
import logging
import re
from typing import Any, Optional

from pydantic import ValidationError

from src.extraction.llm_client import LLMClient, LLMExtractionError
from src.extraction.chunking import (
    chunk_news_article,
    chunk_hn_comment,
    chunk_generic_html,
)
from src.extraction.schemas import (
    HNJobExtraction,
    NewsSummary,
    ENRICHMENT_SCHEMA_MAP,
)

logger = logging.getLogger(__name__)


def _clean_json_response(raw: str) -> dict:
    """Parse JSON from LLM output, handling markdown blocks or extraneous text."""
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Regex search for ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Find outermost { ... }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    return json.loads(text)


# ---------------------------------------------------------------------------
# Strategy B.1 — HN Comment Extraction (LLM primary)
# ---------------------------------------------------------------------------

_HN_SYSTEM_PROMPT = """You are a structured data extractor for Hacker News hiring posts.

Extract job information from the hiring comment below and return ONLY valid JSON matching this exact schema:
{
  "company": "Company Name",
  "title": "Job Title or null",
  "location": "Location string or null",
  "is_remote": true/false,
  "role_family": "one of: Engineering, Data, Product, Design, Sales, Marketing, or null"
}

Rules:
- company: The company name from the header. Never use the poster's username.
- title: The specific job title from the header. If multiple roles are listed in the body but no single title in the header, set to null.
- location: City/region from the header. Include "Remote" if applicable.
- is_remote: true if the word "remote" appears in the comment.
- role_family: Classify into EXACTLY one of these 6 categories based on the title and description: Engineering, Data, Product, Design, Sales, Marketing. If it doesn't fit any, set to null.
- Never invent information not present in the text.
- Return ONLY the JSON object, no markdown formatting, no explanation."""


async def extract_hn_job_llm(
    llm: LLMClient,
    raw_comment_html: str,
    source_url: str,
) -> Optional[HNJobExtraction]:
    """Extract structured job fields from an HN hiring comment using LLM.

    Args:
        llm: The LLM client instance.
        raw_comment_html: Raw HTML of the HN comment.
        source_url: The HN comment permalink (traceable source record).

    Returns:
        HNJobExtraction if successful, None if LLM or validation fails.
    """
    chunked = chunk_hn_comment(raw_comment_html)

    try:
        response = await llm.complete(
            system_prompt=_HN_SYSTEM_PROMPT,
            user_prompt=chunked,
            response_format={"type": "json_object"},
        )
    except LLMExtractionError as exc:
        logger.error(
            "[LLM-HN] All providers failed for %s: %s", source_url, exc
        )
        return None

    # Parse and validate response
    try:
        data = _clean_json_response(response)
        result = HNJobExtraction.model_validate(data)

        logger.info(
            "[LLM-HN] Extracted from %s: company='%s', title='%s', "
            "remote=%s, role='%s'",
            source_url, result.company, result.title,
            result.is_remote, result.role_family,
        )
        return result

    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning(
            "[LLM-HN] Validation failed for %s: %s. Raw response: %s",
            source_url, exc, response[:200],
        )
        return None


# ---------------------------------------------------------------------------
# Strategy B.2 — News Summary Generation (LLM primary)
# ---------------------------------------------------------------------------

_NEWS_SUMMARY_SYSTEM_PROMPT = """You are a news article summarizer. Generate a concise, factual summary.

Return ONLY valid JSON matching this exact schema:
{
  "summary": "2-3 sentence factual summary of the article."
}

Rules:
- Summarize the key facts, developments, and implications in 2-3 sentences.
- Stay strictly factual — do not add opinions, speculation, or information not in the article.
- Keep it concise: between 150 and 350 characters. Strictly do NOT exceed 450 characters under any circumstance.
- Return ONLY the JSON object, no markdown formatting, no explanation."""


async def generate_news_summary(
    llm: LLMClient,
    full_text: str,
    title: str,
    source_url: str,
) -> Optional[str]:
    """Generate an LLM summary of a news article.

    Only called when full_text is available. If LLM fails, returns None
    (caller should keep existing RSS summary — never lose data).

    Args:
        llm: The LLM client instance.
        full_text: The full article text.
        title: The article title (used as context for chunking).
        source_url: The article URL (traceable source record).

    Returns:
        Summary string if successful, None on failure.
    """
    chunked = chunk_news_article(full_text, title=title)

    try:
        response = await llm.complete(
            system_prompt=_NEWS_SUMMARY_SYSTEM_PROMPT,
            user_prompt=chunked,
            response_format={"type": "json_object"},
        )
    except LLMExtractionError as exc:
        logger.error(
            "[LLM-News] All providers failed for %s: %s", source_url, exc
        )
        return None

    # Parse and validate
    try:
        data = _clean_json_response(response)
        result = NewsSummary.model_validate(data)

        logger.info(
            "[LLM-News] Generated summary for '%s': %s...",
            title[:40], result.summary[:80],
        )
        return result.summary

    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning(
            "[LLM-News] Validation failed for '%s': %s. Raw: %s",
            title[:40], exc, response[:200],
        )
        return None


# ---------------------------------------------------------------------------
# Strategy A — LLM Fallback for Deterministic Parsers
# ---------------------------------------------------------------------------

_FALLBACK_SYSTEM_PROMPT_TEMPLATE = """You are a data extraction assistant. Extract the requested field from the raw source content below.

Return ONLY valid JSON matching this exact schema:
{schema_json}

Rules:
- Extract ONLY the "{field_name}" field from the source content.
- If the field genuinely cannot be determined from the content, set it to null.
- Never invent or guess values that are not present in the source.
- Return ONLY the JSON object, no markdown formatting, no explanation."""


async def try_llm_fallback_for_field(
    llm: LLMClient,
    source_name: str,
    field_name: str,
    raw_content: str,
    source_url: str,
    deterministic_error: str,
) -> Optional[Any]:
    """Attempt LLM extraction for a single field that deterministic parsing failed on.

    This is Strategy A: only called when the primary deterministic parser
    either threw an error or returned null for a field where raw content
    is available to try again with.

    Args:
        llm: The LLM client instance.
        source_name: Which source this record came from (for logging).
        field_name: The specific field that needs extraction.
        raw_content: The raw HTML/text content to extract from.
        source_url: The source record URL (for traceability).
        deterministic_error: Why deterministic parsing failed (for logging).

    Returns:
        The extracted value (typed correctly per the field's Pydantic model),
        or None if LLM also cannot extract it.
    """
    logger.info(
        "[LLM-Fallback] Triggered: source=%s, field=%s, reason=%s, url=%s",
        source_name, field_name, deterministic_error, source_url,
    )

    # Look up the correct Pydantic model for this field
    schema_cls = ENRICHMENT_SCHEMA_MAP.get(field_name)
    if not schema_cls:
        logger.warning(
            "[LLM-Fallback] No enrichment schema for field '%s' — skipping",
            field_name,
        )
        return None

    # Build the schema JSON for the prompt
    schema_json = json.dumps(
        {f.alias or name: f.description or "value" for name, f in schema_cls.model_fields.items()},
        indent=2,
    )

    system_prompt = _FALLBACK_SYSTEM_PROMPT_TEMPLATE.format(
        schema_json=schema_json,
        field_name=field_name,
    )

    chunked = chunk_generic_html(raw_content)

    try:
        response = await llm.complete(
            system_prompt=system_prompt,
            user_prompt=chunked,
            response_format={"type": "json_object"},
        )
    except LLMExtractionError as exc:
        logger.warning(
            "[LLM-Fallback] FAILED: All providers failed for field=%s source=%s: %s",
            field_name, source_name, exc,
        )
        return None

    # Parse and validate through the field-specific Pydantic model
    try:
        data = _clean_json_response(response)
        result = schema_cls.model_validate(data)
        value = getattr(result, field_name, None)

        if value is not None:
            logger.info(
                "[LLM-Fallback] RECOVERED: source=%s, field=%s, value=%s",
                source_name, field_name, value,
            )
        else:
            logger.info(
                "[LLM-Fallback] CONFIRMED NULL: source=%s, field=%s "
                "(LLM also could not extract — field stays null)",
                source_name, field_name,
            )

        return value

    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning(
            "[LLM-Fallback] FAILED: Validation error for field=%s source=%s: %s. "
            "Raw response: %s",
            field_name, source_name, exc, response[:200],
        )
        return None
