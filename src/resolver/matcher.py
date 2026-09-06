"""
Multi-tier Entity Matcher.

Tier 0: Seed List Match (Exact/alias lookup against 50 top AI startups) -> 1.0
Tier 1: Exact Normalized Match (Matching keys after legal suffix/domain strip) -> 1.0
Tier 2: High-Confidence Fuzzy (RapidFuzz token_sort_ratio >= 95%) -> 0.95
Tier 3: LLM Arbitration (75% <= similarity < 95%, evaluated via LLMClient)
Tier 4: Distinct Entities (Rejected / Singleton) -> 1.0
"""

import json
import logging
from typing import Optional, Tuple
from rapidfuzz import fuzz

from src.resolver.seed_data import lookup_seed_startup
from src.resolver.schemas import SameEntityDecision
from src.extraction.llm_client import LLMClient, LLMExtractionError

logger = logging.getLogger(__name__)

_LLM_ARBITRATION_SYSTEM_PROMPT = """You are a precision entity resolution adjudicator for companies, startups, and products.

Determine whether the two entity names below refer to the SAME real-world organization, company, or parent brand ecosystem, or if they are two distinct/unrelated entities or competitors.

Return ONLY valid JSON matching this schema:
{
  "is_same_entity": true/false,
  "canonical_name": "clean authoritative name",
  "confidence": 0.0 to 1.0,
  "explanation": "concise rationale"
}

Critical Rules:
1. False Friends: Do NOT merge distinct entities or competitors that happen to share words, roots, or sounds.
   - "Salesforce" and "Salesflare" are DIFFERENT entities (competitors).
   - "NimbleRx" (digital pharmacy) and "Nimble" (CRM) are DIFFERENT entities.
   - "Deepgram" (voice AI) and "Telegram" (messaging) are DIFFERENT entities.
   - "ShipBob" (3PL fulfillment) and "Shippo" (shipping API) are DIFFERENT entities.
   - "Click" and "ClickUp" are DIFFERENT entities.
2. Product & Brand Extensions: DO merge when one entity is a direct product edition, brand extension, or module of the other parent organization/company.
   - "monday.com" and "monday CRM" are the SAME entity (canonical: "monday.com").
   - "HubSpot" and "HubSpot CRM" are the SAME entity (canonical: "HubSpot").
3. True Variations: DO merge spelling variants, punctuation differences, and legal forms.
   - "Demandbase, Inc." and "Demandbase" are the SAME entity.
   - "LiveKit" and "LiveKit Inc" are the SAME entity.
4. Return ONLY valid JSON, no markdown formatting, no commentary."""


def check_tier0_seed(raw_name: str, clean_name: str) -> Optional[str]:
    """Tier 0: Check against the curated seed list of 50 AI startups."""
    # Check raw name
    canonical = lookup_seed_startup(raw_name)
    if canonical:
        return canonical
    # Check clean name
    canonical = lookup_seed_startup(clean_name)
    if canonical:
        return canonical
    return None


def check_tier1_exact(key_a: str, key_b: str) -> bool:
    """Tier 1: Exact match on normalized match keys."""
    if not key_a or not key_b:
        return False
    return key_a == key_b


def check_tier2_fuzzy(clean_a: str, clean_b: str) -> Tuple[bool, float]:
    """Tier 2: High-confidence fuzzy match using RapidFuzz.

    Returns (is_match, score).
    """
    if not clean_a or not clean_b:
        return False, 0.0

    score_ts = fuzz.token_sort_ratio(clean_a, clean_b)
    score_ratio = fuzz.ratio(clean_a, clean_b)

    # Ultra-strict criteria for Tier 2 automatic merge without LLM:
    # Requires >= 98% (reserved strictly for minor typos in very long strings).
    # Near-miss 75-97% pairs (including 1-letter suffix differences like Pipeliner vs Pipeline at 96%)
    # must be routed to Tier 3 LLM arbitration.
    if score_ts >= 98 and score_ratio >= 98:
        return True, score_ts / 100.0

    return False, score_ts / 100.0


async def check_tier3_llm(
    llm: LLMClient,
    entity_a: dict,
    entity_b: dict,
) -> SameEntityDecision:
    """Tier 3: LLM arbitration for borderline pairs (75% <= similarity < 95%).

    Reuses existing src.extraction.llm_client.LLMClient fallback chain.
    """
    prompt = (
        f"Entity A: '{entity_a['raw_name']}' (Source: {entity_a.get('source_name', 'Unknown')})\n"
        f"Entity B: '{entity_b['raw_name']}' (Source: {entity_b.get('source_name', 'Unknown')})\n\n"
        f"Are Entity A and Entity B the exact same organization/company/product?"
    )

    try:
        response = await llm.complete(
            system_prompt=_LLM_ARBITRATION_SYSTEM_PROMPT,
            user_prompt=prompt,
            response_format={"type": "json_object"},
        )
        # Parse JSON
        from src.extraction.fallback import _clean_json_response
        data = _clean_json_response(response)
        if "explanation" in data and isinstance(data["explanation"], str):
            data["explanation"] = data["explanation"][:990]
        decision = SameEntityDecision.model_validate(data)
        return decision
    except Exception as exc:
        logger.warning(
            "[Resolver-LLM] Arbitration failed between '%s' and '%s': %s",
            entity_a["raw_name"], entity_b["raw_name"], exc,
        )
        # Safe fallback on arbitration failure: do NOT merge
        return SameEntityDecision(
            is_same_entity=False,
            canonical_name=entity_a["clean_name"],
            confidence=0.5,
            explanation=f"LLM arbitration failed ({exc}); conservatively kept distinct.",
        )
