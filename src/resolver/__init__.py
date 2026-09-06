"""
Entity Resolution module for GraphOne pipeline.
Handles canonicalization of Startups and Products across multiple scraped sources.
"""

from src.resolver.resolver import resolve_all_entities

__all__ = ["resolve_all_entities"]
