"""
Entity Resolution Orchestrator.

Implements the complete Phase IV resolution pipeline:
1. Scoped entity ingestion from SQLite (startups.entity_name, products.startup_name, jobs.company).
2. Normalization (legal suffix and domain stripping).
3. Candidate blocking via inverted index (O(N log N) scale).
4. Multi-tier matching cascade (Tier 0 Seed -> Tier 1 Exact -> Tier 2 Fuzzy -> Tier 3 LLM).
5. Transitive clustering via Union-Find and 5-level canonical selection hierarchy.
6. Persistence into entity_mapping_log table.
"""

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import logging
from typing import Any, Optional
import aiosqlite

from src.resolver.normalizer import normalize_entity
from src.resolver.seed_data import lookup_seed_startup
from src.resolver.blocking import CandidateBlocker
from src.resolver.matcher import (
    check_tier0_seed,
    check_tier1_exact,
    check_tier2_fuzzy,
    check_tier3_llm,
)
from src.resolver.clustering import UnionFind, select_canonical_name
from src.resolver.storage import (
    init_entity_mapping_table,
    save_entity_mappings,
    get_entity_mapping_summary,
)
from src.extraction.llm_client import LLMClient

logger = logging.getLogger(__name__)


async def fetch_scoped_entities(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """Fetch raw entity records scoped strictly to Startups, Products, and Jobs.

    Scope definition (per user requirement):
    - startups.entity_name (entity_type: 'STARTUP')
    - products.startup_name (entity_type: 'PRODUCT')
    - jobs.company (entity_type: 'COMPANY' - employers)
    """
    entities = []

    # 1. Startups
    cur = await db.execute("SELECT entity_name, source_name, source_url FROM startups")
    for row in await cur.fetchall():
        name, s_name, s_url = row
        if name and name.strip():
            entities.append({
                "raw_name": name.strip(),
                "entity_type": "STARTUP",
                "source_name": s_name or "Y Combinator",
                "source_url": s_url or "",
            })

    # 2. Products
    cur = await db.execute("SELECT startup_name, source_name, source_url FROM products")
    for row in await cur.fetchall():
        name, s_name, s_url = row
        if name and name.strip():
            entities.append({
                "raw_name": name.strip(),
                "entity_type": "PRODUCT",
                "source_name": s_name or "SaaSHub",
                "source_url": s_url or "",
            })

    # 3. Jobs
    cur = await db.execute("SELECT company, source_name, job_url FROM jobs")
    for row in await cur.fetchall():
        name, s_name, j_url = row
        if name and name.strip():
            entities.append({
                "raw_name": name.strip(),
                "entity_type": "COMPANY",
                "source_name": s_name or "Job Board",
                "source_url": j_url or "",
            })

    logger.info("Loaded %d scoped raw entity records across tables", len(entities))
    return entities


async def resolve_all_entities(
    db_path: str = "data/records.db",
    llm_client: Optional[LLMClient] = None,
) -> dict[str, Any]:
    """Execute the full Entity Resolution pipeline over the database.

    Args:
        db_path: Path to the SQLite database.
        llm_client: Optional existing LLMClient instance (reused from Phase III).

    Returns:
        Summary dict containing resolution metrics, method breakdowns, and clusters.
    """
    logger.info("Starting Phase IV Entity Resolution Pipeline")

    async with aiosqlite.connect(db_path) as db:
        await init_entity_mapping_table(db)
        raw_records = await fetch_scoped_entities(db)

        if not raw_records:
            logger.warning("No entities found to resolve.")
            return {}

        # 1. Pre-process and normalize
        entities: list[dict[str, Any]] = []
        blocker = CandidateBlocker()

        for idx, rec in enumerate(raw_records):
            raw = rec["raw_name"]
            clean, match_key = normalize_entity(raw)
            seed_canonical = check_tier0_seed(raw, clean)

            item = {
                "id": idx,
                "raw_name": raw,
                "clean_name": clean,
                "match_key": match_key,
                "seed_canonical": seed_canonical,
                "entity_type": rec["entity_type"],
                "source_name": rec["source_name"],
                "source_url": rec["source_url"],
            }
            entities.append(item)
            blocker.add_entity(idx, item)

        num_entities = len(entities)
        logger.info("Normalized %d entities. Pre-indexed into candidate blocker.", num_entities)

        # 2. Candidate generation via blocking
        candidate_pairs = blocker.generate_candidate_pairs()
        logger.info(
            "Candidate blocking generated %d pairs (vs %d naive all-pairs comparisons, %.1f%% reduction)",
            len(candidate_pairs),
            num_entities * (num_entities - 1) // 2,
            (1 - len(candidate_pairs) / max(num_entities * (num_entities - 1) // 2, 1)) * 100,
        )

        # 3. Multi-tier Matching
        uf = UnionFind(num_entities)
        pair_match_details: dict[tuple[int, int], tuple[str, float, str]] = {}

        # First pass: Tier 0 Seed matches
        # Entities matching the same seed canonical are merged immediately
        seed_groups = defaultdict(list)
        for idx, e in enumerate(entities):
            if e["seed_canonical"]:
                seed_groups[e["seed_canonical"]].append(idx)

        for seed_canonical, group in seed_groups.items():
            if len(group) > 1:
                first = group[0]
                for other in group[1:]:
                    pair = (min(first, other), max(first, other))
                    uf.union(first, other)
                    pair_match_details[pair] = (
                        "SEED_LIST_MATCH",
                        1.0,
                        f"Matched Tier 0 Seed List canonical '{seed_canonical}'",
                    )

        # Second pass: Candidate pairs evaluation
        # Manage LLM client lifecycle if needed
        close_llm_when_done = False
        llm = llm_client
        if not llm:
            try:
                llm = LLMClient()
                close_llm_when_done = True
            except Exception as e:
                logger.warning("Could not initialize LLMClient for Tier 3 arbitration: %s", e)

        for id_a, id_b in candidate_pairs:
            # Skip if already in same component
            if uf.find(id_a) == uf.find(id_b):
                continue

            ea = entities[id_a]
            eb = entities[id_b]

            # Tier 1: Exact Normalized Match
            if check_tier1_exact(ea["match_key"], eb["match_key"]):
                uf.union(id_a, id_b)
                pair_match_details[(id_a, id_b)] = (
                    "EXACT_NORMALIZED",
                    1.0,
                    f"Exact match on normalized key '{ea['match_key']}'",
                )
                continue

            # Tier 2: High-Confidence Fuzzy Rule
            is_fuzzy, fuzz_score = check_tier2_fuzzy(ea["clean_name"], eb["clean_name"])
            if is_fuzzy:
                uf.union(id_a, id_b)
                pair_match_details[(id_a, id_b)] = (
                    "FUZZY_CONFIDENT",
                    0.95,
                    f"High-confidence fuzzy match (score: {fuzz_score:.2f})",
                )
                continue

            # Tier 3: Borderline Pair -> LLM Arbitration (75% <= score < 98%)
            if 0.75 <= fuzz_score < 0.98 and llm:
                # Only arbitrate if neither is empty
                decision = await check_tier3_llm(llm, ea, eb)
                await asyncio.sleep(0.5)
                if decision.is_same_entity:
                    uf.union(id_a, id_b)
                    pair_match_details[(id_a, id_b)] = (
                        "LLM_ARBITRATION",
                        decision.confidence,
                        f"LLM confirmed same entity ({decision.explanation})",
                    )
                else:
                    logger.debug(
                        "[Resolver-Tier3] LLM rejected merge for '%s' vs '%s': %s",
                        ea["clean_name"], eb["clean_name"], decision.explanation,
                    )

        if close_llm_when_done and llm:
            await llm.close()

        # 4. Canonical clustering & name assignment
        clusters = defaultdict(list)
        for idx in range(num_entities):
            root = uf.find(idx)
            clusters[root].append(entities[idx])

        now_utc = datetime.now(timezone.utc).isoformat()
        mapping_records = []

        for root, members in clusters.items():
            canonical_name = select_canonical_name(members)

            if len(members) == 1:
                # Singleton
                m = members[0]
                if m.get("seed_canonical"):
                    method = "SEED_LIST_MATCH"
                    conf = 1.0
                    exp = f"Matched Tier 0 AI Startup Seed List canonical '{m['seed_canonical']}'"
                else:
                    method = "CANONICAL_SINGLETON"
                    conf = 1.0
                    exp = "Unique entity representation"

                mapping_records.append({
                    "raw_name": m["raw_name"],
                    "canonical_name": canonical_name,
                    "entity_type": m["entity_type"],
                    "match_method": method,
                    "confidence": conf,
                    "source_name": m["source_name"],
                    "source_url": m["source_url"],
                    "explanation": exp,
                    "resolved_at": now_utc,
                })
            else:
                # Multi-member cluster
                for m in members:
                    # Determine primary reason/method
                    if m.get("seed_canonical"):
                        method = "SEED_LIST_MATCH"
                        conf = 1.0
                        exp = f"Merged into Seed List canonical '{canonical_name}'"
                    elif m["clean_name"].lower() == canonical_name.lower():
                        method = "EXACT_NORMALIZED"
                        conf = 1.0
                        exp = f"Identical to canonical entity name '{canonical_name}'"
                    else:
                        method = "FUZZY_CONFIDENT"
                        conf = 0.95
                        exp = f"Resolved to canonical entity '{canonical_name}' via cluster similarity"

                    mapping_records.append({
                        "raw_name": m["raw_name"],
                        "canonical_name": canonical_name,
                        "entity_type": m["entity_type"],
                        "match_method": method,
                        "confidence": conf,
                        "source_name": m["source_name"],
                        "source_url": m["source_url"],
                        "explanation": exp,
                        "resolved_at": now_utc,
                    })

        # 5. Save to database
        await save_entity_mappings(db, mapping_records)
        summary = await get_entity_mapping_summary(db)

        logger.info(
            "Phase IV Entity Resolution Complete: %d raw entities -> %d canonical entities (compression: %s)",
            summary["total_raw_entities"],
            summary["distinct_canonical_entities"],
            summary["compression_ratio"],
        )

        return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(resolve_all_entities("data/records.db"))

