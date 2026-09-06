"""
Tier 0 Seed List: Curated database of 50 well-known AI startups.

Each entry defines:
  - canonical_name: The authoritative, verified company name.
  - domain: Primary website domain.
  - aliases: Realistic known spelling, corporate suffix, and domain variants.

Satisfies assessment requirement:
"map extracted entities against a seed list... you may mock a small database of 50 known AI startups"
"""

import re
from typing import Optional

SEED_AI_STARTUPS: list[dict[str, any]] = [
    {
        "canonical_name": "OpenAI",
        "domain": "openai.com",
        "aliases": ["OpenAI, Inc.", "Open AI", "OpenAI LLC", "OpenAI Global LLC", "openai.com"],
    },
    {
        "canonical_name": "Anthropic",
        "domain": "anthropic.com",
        "aliases": ["Anthropic PBC", "Anthropic, Inc.", "Anthropic AI", "anthropic.com"],
    },
    {
        "canonical_name": "Cohere",
        "domain": "cohere.com",
        "aliases": ["Cohere Inc.", "Cohere AI", "cohere.com"],
    },
    {
        "canonical_name": "Mistral AI",
        "domain": "mistral.ai",
        "aliases": ["Mistral", "Mistral AI SAS", "Mistral.ai", "mistral.ai"],
    },
    {
        "canonical_name": "Scale AI",
        "domain": "scale.com",
        "aliases": ["Scale", "Scale AI, Inc.", "Scale, Inc.", "scale.com"],
    },
    {
        "canonical_name": "Hugging Face",
        "domain": "huggingface.co",
        "aliases": ["HuggingFace", "Hugging Face, Inc.", "HuggingFace Inc", "huggingface.co"],
    },
    {
        "canonical_name": "Perplexity AI",
        "domain": "perplexity.ai",
        "aliases": ["Perplexity", "Perplexity, Inc.", "Perplexity AI, Inc.", "perplexity.ai"],
    },
    {
        "canonical_name": "Midjourney",
        "domain": "midjourney.com",
        "aliases": ["Midjourney, Inc.", "Midjourney Inc", "midjourney.com"],
    },
    {
        "canonical_name": "Runway",
        "domain": "runwayml.com",
        "aliases": ["RunwayML", "Runway AI, Inc.", "Runway AI", "runwayml.com"],
    },
    {
        "canonical_name": "ElevenLabs",
        "domain": "elevenlabs.io",
        "aliases": ["Eleven Labs", "ElevenLabs, Inc.", "ElevenLabs Inc", "elevenlabs.io"],
    },
    {
        "canonical_name": "Character.ai",
        "domain": "character.ai",
        "aliases": ["Character AI", "Character Technologies, Inc.", "c.ai", "character.ai"],
    },
    {
        "canonical_name": "Glean",
        "domain": "glean.com",
        "aliases": ["Glean Technologies, Inc.", "Glean Work AI", "glean.com"],
    },
    {
        "canonical_name": "Pinecone",
        "domain": "pinecone.io",
        "aliases": ["Pinecone Systems, Inc.", "Pinecone Vector Database", "pinecone.io"],
    },
    {
        "canonical_name": "Weaviate",
        "domain": "weaviate.io",
        "aliases": ["Weaviate B.V.", "Weaviate Cloud", "weaviate.io"],
    },
    {
        "canonical_name": "LangChain",
        "domain": "langchain.com",
        "aliases": ["LangChain, Inc.", "LangChain Inc", "LangChain AI", "langchain.com"],
    },
    {
        "canonical_name": "LlamaIndex",
        "domain": "llamaindex.ai",
        "aliases": ["Llama Index", "LlamaIndex, Inc.", "llamaindex.ai"],
    },
    {
        "canonical_name": "Together AI",
        "domain": "together.ai",
        "aliases": ["Together Computer, Inc.", "Together", "together.ai"],
    },
    {
        "canonical_name": "Groq",
        "domain": "groq.com",
        "aliases": ["Groq, Inc.", "Groq Inc", "groq.com"],
    },
    {
        "canonical_name": "Cerebras Systems",
        "domain": "cerebras.net",
        "aliases": ["Cerebras", "Cerebras Systems, Inc.", "cerebras.net"],
    },
    {
        "canonical_name": "Harvey",
        "domain": "harvey.ai",
        "aliases": ["Harvey AI", "Counsel AI Corp.", "harvey.ai"],
    },
    {
        "canonical_name": "Cursor",
        "domain": "cursor.com",
        "aliases": ["Anysphere, Inc.", "Anysphere", "Cursor AI", "cursor.com", "cursor.sh"],
    },
    {
        "canonical_name": "LiveKit",
        "domain": "livekit.io",
        "aliases": ["LiveKit, Inc.", "LiveKit Inc", "livekit.io"],
    },
    {
        "canonical_name": "DeepL",
        "domain": "deepl.com",
        "aliases": ["DeepL SE", "DeepL GmbH", "deepl.com"],
    },
    {
        "canonical_name": "Synthesia",
        "domain": "synthesia.io",
        "aliases": ["Synthesia Ltd.", "Synthesia Limited", "synthesia.io"],
    },
    {
        "canonical_name": "HeyGen",
        "domain": "heygen.com",
        "aliases": ["HeyGen, Inc.", "Movio", "heygen.com"],
    },
    {
        "canonical_name": "Pika",
        "domain": "pika.art",
        "aliases": ["Pika Labs, Inc.", "Pika Labs", "pika.art"],
    },
    {
        "canonical_name": "Suno",
        "domain": "suno.ai",
        "aliases": ["Suno AI", "Suno, Inc.", "suno.ai", "suno.com"],
    },
    {
        "canonical_name": "Udio",
        "domain": "udio.com",
        "aliases": ["Uncharted Labs, Inc.", "Udio AI", "udio.com"],
    },
    {
        "canonical_name": "Abridge",
        "domain": "abridge.com",
        "aliases": ["Abridge AI, Inc.", "abridge.com"],
    },
    {
        "canonical_name": "Covariant",
        "domain": "covariant.ai",
        "aliases": ["Covariant.ai", "Covariant AI", "covariant.ai"],
    },
    {
        "canonical_name": "Figure AI",
        "domain": "figure.ai",
        "aliases": ["Figure", "Figure Technologies AI", "figure.ai"],
    },
    {
        "canonical_name": "Shield AI",
        "domain": "shield.ai",
        "aliases": ["Shield AI, Inc.", "shield.ai"],
    },
    {
        "canonical_name": "Adept AI",
        "domain": "adept.ai",
        "aliases": ["Adept", "Adept AI Labs, Inc.", "adept.ai"],
    },
    {
        "canonical_name": "Inflection AI",
        "domain": "inflection.ai",
        "aliases": ["Inflection", "Inflection AI, Inc.", "Pi AI", "inflection.ai"],
    },
    {
        "canonical_name": "Stability AI",
        "domain": "stability.ai",
        "aliases": ["Stability AI Ltd.", "Stability AI, Inc.", "stability.ai"],
    },
    {
        "canonical_name": "Replit",
        "domain": "replit.com",
        "aliases": ["Replit, Inc.", "Repl.it", "replit.com"],
    },
    {
        "canonical_name": "Weights & Biases",
        "domain": "wandb.ai",
        "aliases": ["W&B", "Wandb", "Weights and Biases, Inc.", "wandb.ai"],
    },
    {
        "canonical_name": "Anyscale",
        "domain": "anyscale.com",
        "aliases": ["Anyscale, Inc.", "Ray AI", "anyscale.com"],
    },
    {
        "canonical_name": "Modal",
        "domain": "modal.com",
        "aliases": ["Modal Labs, Inc.", "Modal Labs", "modal.com"],
    },
    {
        "canonical_name": "Baseten",
        "domain": "baseten.co",
        "aliases": ["Baseten, Inc.", "baseten.co"],
    },
    {
        "canonical_name": "Lamini",
        "domain": "lamini.ai",
        "aliases": ["Lamini AI", "Lamini, Inc.", "lamini.ai"],
    },
    {
        "canonical_name": "Writer",
        "domain": "writer.com",
        "aliases": ["Writer, Inc.", "Writer AI", "writer.com"],
    },
    {
        "canonical_name": "Jasper",
        "domain": "jasper.ai",
        "aliases": ["Jasper AI", "Jasper AI, Inc.", "Jarvis.ai", "jasper.ai"],
    },
    {
        "canonical_name": "Copy.ai",
        "domain": "copy.ai",
        "aliases": ["Copy AI", "CopyAI, Inc.", "copy.ai"],
    },
    {
        "canonical_name": "Descript",
        "domain": "descript.com",
        "aliases": ["Descript, Inc.", "descript.com"],
    },
    {
        "canonical_name": "Otter.ai",
        "domain": "otter.ai",
        "aliases": ["Otter AI", "AISense, Inc.", "otter.ai"],
    },
    {
        "canonical_name": "Notion",
        "domain": "notion.so",
        "aliases": ["Notion Labs, Inc.", "Notion AI", "notion.so", "notion.com"],
    },
    {
        "canonical_name": "CodiumAI",
        "domain": "qodo.ai",
        "aliases": ["Qodo", "Codium", "Codium AI, Inc.", "codium.ai"],
    },
    {
        "canonical_name": "Tabnine",
        "domain": "tabnine.com",
        "aliases": ["Codota Dot Com Ltd.", "TabNine", "tabnine.com"],
    },
    {
        "canonical_name": "Poolside",
        "domain": "poolside.ai",
        "aliases": ["Poolside AI", "Poolside, Inc.", "poolside.ai"],
    },
]


def _normalize_seed_key(text: str) -> str:
    """Normalize text for exact/fuzzy dictionary lookup."""
    s = text.lower().strip()
    # Strip URL prefixes and trailing paths
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("/")[0]
    # Strip common legal suffixes
    s = re.sub(r"\b(inc|incorporated|llc|pbc|ltd|limited|corp|corporation|gmbh|sas|se|co|company)\b", "", s)
    # Strip non-alphanumeric
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


# Build fast pre-indexed lookup mapping: normalized_key -> canonical_name
_SEED_LOOKUP: dict[str, str] = {}

for entry in SEED_AI_STARTUPS:
    c_name = entry["canonical_name"]
    # Map canonical name itself
    _SEED_LOOKUP[_normalize_seed_key(c_name)] = c_name
    # Map domain
    _SEED_LOOKUP[_normalize_seed_key(entry["domain"])] = c_name
    # Map all aliases
    for alias in entry.get("aliases", []):
        key = _normalize_seed_key(alias)
        if key:
            _SEED_LOOKUP[key] = c_name


def lookup_seed_startup(raw_name: str) -> Optional[str]:
    """Check if raw_name matches any known AI startup in the Tier 0 seed list.

    Returns:
        The canonical company name if matched, else None.
    """
    if not raw_name or not raw_name.strip():
        return None
    key = _normalize_seed_key(raw_name)
    return _SEED_LOOKUP.get(key)
