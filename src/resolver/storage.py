"""
SQLite storage module for entity_mapping_log.

Backs Tab 6 of the Google Sheet deliverable:
'Entity Mapping Log (Raw vs Canonical names)'.
"""

import aiosqlite
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def init_entity_mapping_table(db: aiosqlite.Connection) -> None:
    """Initialize entity_mapping_log table and indexes."""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_mapping_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_name TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            match_method TEXT NOT NULL,
            confidence REAL NOT NULL,
            source_name TEXT,
            source_url TEXT,
            explanation TEXT,
            resolved_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_entity_mapping_raw ON entity_mapping_log(raw_name)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_entity_mapping_canonical ON entity_mapping_log(canonical_name)"
    )
    await db.commit()


async def save_entity_mappings(
    db: aiosqlite.Connection,
    mappings: list[dict[str, Any]],
) -> int:
    """Persist a batch of entity resolution records into entity_mapping_log.

    Returns:
        Number of records inserted.
    """
    if not mappings:
        return 0

    # Clear previous run's log to avoid duplicating on re-runs
    await db.execute("DELETE FROM entity_mapping_log")

    insert_sql = """
        INSERT INTO entity_mapping_log (
            raw_name, canonical_name, entity_type, match_method,
            confidence, source_name, source_url, explanation, resolved_at
        ) VALUES (
            :raw_name, :canonical_name, :entity_type, :match_method,
            :confidence, :source_name, :source_url, :explanation, :resolved_at
        )
    """

    await db.executemany(insert_sql, mappings)
    await db.commit()
    logger.info("Persisted %d entity mapping records to SQLite", len(mappings))
    return len(mappings)


async def get_entity_mapping_summary(db: aiosqlite.Connection) -> dict[str, Any]:
    """Retrieve statistical summary of entity resolution results."""
    cur = await db.execute(
        """
        SELECT match_method, count(*), avg(confidence)
        FROM entity_mapping_log
        GROUP BY match_method
        ORDER BY count(*) DESC
        """
    )
    methods = await cur.fetchall()

    cur = await db.execute("SELECT count(DISTINCT canonical_name) FROM entity_mapping_log")
    distinct_canonicals = (await cur.fetchone())[0]

    cur = await db.execute("SELECT count(*) FROM entity_mapping_log")
    total_raw = (await cur.fetchone())[0]

    # Find clusters with multiple raw variants
    cur = await db.execute(
        """
        SELECT canonical_name, count(*) as variant_count, group_concat(raw_name, ' | ')
        FROM entity_mapping_log
        GROUP BY canonical_name
        HAVING variant_count > 1
        ORDER BY variant_count DESC
        LIMIT 20
        """
    )
    multi_variant_clusters = await cur.fetchall()

    return {
        "total_raw_entities": total_raw,
        "distinct_canonical_entities": distinct_canonicals,
        "compression_ratio": f"{(1 - distinct_canonicals / max(total_raw, 1)) * 100:.2f}%",
        "methods_breakdown": [
            {"method": r[0], "count": r[1], "avg_confidence": round(r[2], 3)}
            for r in methods
        ],
        "multi_variant_clusters": [
            {"canonical": r[0], "count": r[1], "variants": r[2]}
            for r in multi_variant_clusters
        ],
    }
