"""
Entity Normalization module.

Strips legal suffixes, domain extensions, punctuation, and extraneous noise
to generate standardized clean names and matching keys.
"""

import re
import html
from typing import Tuple

# Common corporate/legal suffixes (case-insensitive)
_LEGAL_SUFFIX_PATTERN = re.compile(
    r"(?:,\s*|\s+)\b("
    r"inc\.?|incorporated|"
    r"llc\.?|l\.l\.c\.?|"
    r"corp\.?|corporation|"
    r"ltd\.?|limited|"
    r"gmbh|sas|s\.a\.s\.?|se|pbc|"
    r"pty\.?\s+ltd\.?|"
    r"co\.?|company|"
    r"technologies|technology|"
    r"holdings|group|"
    r"systems|software"
    r")\b\.?",
    flags=re.IGNORECASE,
)

# Common domain suffixes when entities are formatted as URLs/domains
_DOMAIN_SUFFIX_PATTERN = re.compile(
    r"\.(com|io|ai|co|org|net|so|app|sh|art|dev|tech|me|co\.uk|de|eu)$",
    flags=re.IGNORECASE,
)


def clean_display_name(raw: str) -> str:
    """Generate a clean, professional display name by stripping legal suffixes and URLs.

    Example:
        'Demandbase, Inc.' -> 'Demandbase'
        'https://livekit.io/' -> 'LiveKit'
        'Scale AI, Inc.' -> 'Scale AI'
    """
    if not raw or not raw.strip():
        return ""

    s = html.unescape(raw).strip()

    # Strip URL protocols and paths
    s = re.sub(r"^https?://", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^www\.", "", s, flags=re.IGNORECASE)
    s = s.split("/")[0].strip()

    # If it ends with a domain extension (e.g. livekit.io -> livekit)
    # only strip if it's a domain-like string
    if "." in s and not re.search(r"\s", s):
        s = _DOMAIN_SUFFIX_PATTERN.sub("", s)

    # Strip legal suffixes (with optional leading comma and trailing period)
    s = _LEGAL_SUFFIX_PATTERN.sub("", s).strip()

    # Clean up any leftover punctuation (commas, periods, dashes, pipes) at start or end
    s = re.sub(r"^[,\-_.|\s]+|[,\-_.|\s]+$", "", s).strip()
    s = re.sub(r"\s+", " ", s).strip()

    return s if s else raw.strip()


def build_match_key(clean_or_raw: str) -> str:
    """Generate a lowercase alphanumeric key for indexing and exact joins.

    Example:
        'Demandbase, Inc.' -> 'demandbase'
        'Scale AI' -> 'scaleai'
        '80,000 Hours' -> '80000hours'
    """
    clean = clean_display_name(clean_or_raw)
    # Strip all non-alphanumeric characters
    key = re.sub(r"[^a-zA-Z0-9]", "", clean).lower()
    return key


def normalize_entity(raw: str) -> Tuple[str, str]:
    """Return both (clean_display_name, match_key)."""
    clean = clean_display_name(raw)
    key = build_match_key(clean)
    return clean, key
