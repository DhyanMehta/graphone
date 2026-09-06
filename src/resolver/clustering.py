"""
Clustering and Canonical Selection module using Disjoint Set (Union-Find).

Implements transitive clustering (A=B, B=C => A=B=C) and applies the
5-level canonical selection hierarchy:
1. Seed list canonical name (if matched)
2. YC Startup directory name
3. SaaSHub Product startup name
4. Shortest clean name without legal suffixes
5. Most frequent form
"""

from collections import Counter
from typing import Any, Optional


class UnionFind:
    """Disjoint Set with path compression and rank optimization."""

    def __init__(self, size: int):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, i: int) -> int:
        if self.parent[i] == i:
            return i
        self.parent[i] = self.find(self.parent[i])
        return self.parent[i]

    def union(self, i: int, j: int) -> None:
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i != root_j:
            if self.rank[root_i] < self.rank[root_j]:
                self.parent[root_i] = root_j
            elif self.rank[root_i] > self.rank[root_j]:
                self.parent[root_j] = root_i
            else:
                self.parent[root_j] = root_i
                self.rank[root_i] += 1


def select_canonical_name(members: list[dict[str, Any]]) -> str:
    """Select the authoritative canonical name for a cluster of matching entities.

    Hierarchy:
    1. Seed list canonical name if matched in any member
    2. YC Startup directory name (entity_type == 'STARTUP' and source == 'Y Combinator')
    3. SaaSHub Product startup name (entity_type == 'PRODUCT' and source == 'SaaSHub')
    4. Shortest clean name without legal suffixes
    5. Most frequent form
    """
    # 1. Seed list canonical name
    for m in members:
        if m.get("seed_canonical"):
            return m["seed_canonical"]

    # 2. YC Startup name
    for m in members:
        if m.get("source_name") == "Y Combinator" and m.get("clean_name"):
            return m["clean_name"]

    # 3. SaaSHub Product name
    for m in members:
        if m.get("source_name") == "SaaSHub" and m.get("clean_name"):
            return m["clean_name"]

    # 4 & 5. Frequency and length of clean name
    name_counts = Counter(m.get("clean_name", "") for m in members if m.get("clean_name"))
    if not name_counts:
        return members[0].get("raw_name", "")

    # Sort by frequency (descending), then length (ascending), then alphabetically
    sorted_names = sorted(
        name_counts.keys(),
        key=lambda n: (-name_counts[n], len(n), n),
    )
    return sorted_names[0]
