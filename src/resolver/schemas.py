"""
Pydantic v2 schemas for Entity Resolution decisions and payloads.
"""

from pydantic import BaseModel, Field


class SameEntityDecision(BaseModel):
    """Structured LLM arbitration decision for a borderline pair of entities."""

    is_same_entity: bool = Field(
        ...,
        description="True if both entity names refer to the exact same organization/startup/product, False if distinct.",
    )
    canonical_name: str = Field(
        ...,
        description="The clean, canonical name to use if they are the same entity, or the first entity's clean name if distinct.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0.",
    )
    explanation: str = Field(
        ...,
        max_length=1000,
        description="Brief 1-2 sentence explanation of why they are or are not the same entity.",
    )
