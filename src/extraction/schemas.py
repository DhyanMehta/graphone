"""
Pydantic v2 models for every LLM extraction output shape.

Design decisions:
  - Per-field-type models instead of a single generic EnrichmentField, so
    typed fields (int, strict enum) genuinely fail validation on malformed
    LLM output rather than silently accepting unvalidated strings.
  - RoleFamily is a Literal union derived from the 5 values that already
    exist across the 46 verified job records in the database. The LLM is
    constrained to this exact set — semantically-duplicate categories
    (e.g. "Software Development" vs "Engineering") are rejected at the
    Pydantic layer.
  - strict=True is NOT set globally. Rationale: LLMs frequently return
    technically-correct but loosely-typed values (e.g. "true" as a string
    instead of a JSON boolean for is_remote). Using strict mode would
    reject these as validation failures even though the answer is correct.
    Instead, we use Pydantic's default coercion mode and constrain at the
    type/enum level where it matters (role_family, pricing_model).
    If during testing we observe LLM extractions failing validation for
    coercion-related reasons rather than genuinely wrong answers, this is
    the documented rationale for the design choice.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Canonical enum sets — derived from existing verified data
# ---------------------------------------------------------------------------

# From the 46 existing job records: Engineering(13), Sales(6), Product(4),
# Marketing(3), Data(3), Design(0 in DB but exists in regex parser as a
# valid category). Keeping Design in the enum because the HN parser and
# Jobicy parser both have it as a valid output — it simply hasn't appeared
# in the current small dataset.
RoleFamilyType = Literal[
    "Engineering",
    "Data",
    "Product",
    "Design",
    "Sales",
    "Marketing",
]

PricingModelType = Literal[
    "FREE",
    "FREEMIUM",
    "PAID",
    "ENTERPRISE",
]


# ---------------------------------------------------------------------------
# Strategy B.1 — HN Hiring comment extraction
# ---------------------------------------------------------------------------

class HNJobExtraction(BaseModel):
    """Structured extraction from a Hacker News hiring comment.

    The LLM must return JSON matching this shape exactly. Fields that
    cannot be determined from the comment text should be null.
    """
    company: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Company name extracted from the comment header.",
    )
    title: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Job title. Null if no single clear title in the header.",
    )
    location: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Location string (e.g. 'NYC', 'Remote', 'London').",
    )
    is_remote: bool = Field(
        default=False,
        description="True if the job is remote or allows remote work.",
    )
    role_family: Optional[RoleFamilyType] = Field(
        default=None,
        description=(
            "Canonical role family. Must be exactly one of: "
            "Engineering, Data, Product, Design, Sales, Marketing. "
            "Null if the role doesn't fit any of these categories."
        ),
    )


# ---------------------------------------------------------------------------
# Strategy B.2 — News summary generation
# ---------------------------------------------------------------------------

class NewsSummary(BaseModel):
    """LLM-generated structured summary of a news article."""
    summary: str = Field(
        ...,
        min_length=20,
        max_length=500,
        description="2-3 sentence factual summary of the article.",
    )


# ---------------------------------------------------------------------------
# Strategy A — Per-field enrichment models for deterministic fallback
# ---------------------------------------------------------------------------

class EmployeeCountEnrichment(BaseModel):
    """LLM-extracted employee count from raw source content."""
    employee_count: Optional[int] = Field(
        default=None,
        ge=1,
        description="Number of employees. Null if not determinable.",
    )


class PricingModelEnrichment(BaseModel):
    """LLM-extracted pricing model from raw source content."""
    pricing_model: Optional[PricingModelType] = Field(
        default=None,
        description=(
            "Pricing tier. Must be exactly one of: FREE, FREEMIUM, PAID, "
            "ENTERPRISE. Null if not determinable from the source."
        ),
    )


class RoleFamilyEnrichment(BaseModel):
    """LLM-extracted role family from a job title and description."""
    role_family: Optional[RoleFamilyType] = Field(
        default=None,
        description=(
            "Canonical role family. Must be exactly one of: "
            "Engineering, Data, Product, Design, Sales, Marketing. "
            "Null if the role doesn't fit any category."
        ),
    )


class TitleEnrichment(BaseModel):
    """LLM-extracted job title from raw source content."""
    title: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Job title. Null if not determinable.",
    )


class AuthorEnrichment(BaseModel):
    """LLM-extracted article author from raw HTML content."""
    author: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Author name. Null if not determinable.",
    )


# ---------------------------------------------------------------------------
# Schema registry — maps (source_type, field_name) to the correct model
# ---------------------------------------------------------------------------

# Used by fallback.py to look up the right Pydantic model for a given
# field that deterministic parsing failed on.
ENRICHMENT_SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "employee_count": EmployeeCountEnrichment,
    "pricing_model": PricingModelEnrichment,
    "role_family": RoleFamilyEnrichment,
    "title": TitleEnrichment,
    "author": AuthorEnrichment,
}
