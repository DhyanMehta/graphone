"""
Candidate Blocking module for Scale Thinking (O(N log N)).

At large scale (500k+ entities), all-pairs comparison (N^2 / 2) is computationally
infeasible (250 billion comparisons). This module partitions entities into
candidate blocks using inverted token indices and 3-character prefix keys,
ensuring only plausible matches are compared.
"""

from collections import defaultdict
import re
from typing import Any, Iterable


def _extract_blocking_tokens(match_key: str, clean_name: str) -> set[str]:
    """Extract blocking keys for an entity."""
    tokens = set()

    # 1. Full match key (groups exact normalized matches)
    if match_key:
        tokens.add(f"key:{match_key}")

    # 2. Words longer than 2 characters
    words = re.findall(r"[a-z0-9]{3,}", clean_name.lower())
    for w in words:
        tokens.add(f"tok:{w}")

    # 3. 3-gram prefix of match key if long enough
    if len(match_key) >= 3:
        tokens.add(f"pref:{match_key[:3]}")

    return tokens


class CandidateBlocker:
    """Inverted index blocker that generates candidate comparison pairs."""

    def __init__(self):
        self.index: dict[str, list[int]] = defaultdict(list)
        self.entities: list[dict[str, Any]] = []

    def add_entity(self, entity_id: int, entity_data: dict[str, Any]) -> None:
        """Add an entity to the blocker index."""
        match_key = entity_data.get("match_key", "")
        clean_name = entity_data.get("clean_name", "")
        tokens = _extract_blocking_tokens(match_key, clean_name)

        for tok in tokens:
            self.index[tok].append(entity_id)

    def generate_candidate_pairs(self) -> set[tuple[int, int]]:
        """Generate all distinct (id_a, id_b) pairs that share at least one block."""
        pairs = set()
        for block_key, members in self.index.items():
            # If a block is too large (e.g. generic prefix with >500 members),
            # skip it to prevent explosion, unless it's an exact token block
            if len(members) > 500 and block_key.startswith("pref:"):
                continue

            n = len(members)
            for i in range(n):
                for j in range(i + 1, n):
                    id_a, id_b = members[i], members[j]
                    if id_a > id_b:
                        id_a, id_b = id_b, id_a
                    pairs.add((id_a, id_b))

        return pairs
