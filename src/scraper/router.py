"""
Source router — decides which client (HTTP or Browser) to use per source.

Reads config.yaml and creates/caches client instances with the correct
concurrency limits and rate-limit delays for each source.
"""

import logging
from pathlib import Path
from typing import Union

import yaml

from src.scraper.http_client import HttpClient
from src.scraper.browser_client import BrowserClient

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    """Load and return the YAML config."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class Router:
    """Creates and caches per-source client instances."""

    def __init__(self, config: dict = None):
        if config is None:
            config = load_config()
        self._config = config
        self._clients: dict[str, Union[HttpClient, BrowserClient]] = {}

    def get_source_config(self, source_name: str) -> dict:
        """Return the config block for a specific source."""
        sources = self._config.get("sources", {})
        if source_name not in sources:
            raise ValueError(
                f"Unknown source '{source_name}'. "
                f"Available: {list(sources.keys())}"
            )
        return sources[source_name]

    def get_client(self, source_name: str) -> Union[HttpClient, BrowserClient]:
        """
        Get or create the appropriate client for a source.

        Returns a cached instance if one already exists for this source.
        """
        if source_name in self._clients:
            return self._clients[source_name]

        source_cfg = self.get_source_config(source_name)
        client_type = source_cfg.get("client_type", "http")
        concurrency = source_cfg.get("concurrency_limit", 5)
        delay = source_cfg.get("rate_limit_delay", 1.0)

        if client_type == "browser":
            client = BrowserClient(
                concurrency_limit=concurrency,
                rate_limit_delay=delay,
            )
        else:
            client = HttpClient(
                concurrency_limit=concurrency,
                rate_limit_delay=delay,
            )

        self._clients[source_name] = client
        logger.info(
            "Created %s client for '%s' (concurrency=%d, delay=%.1fs)",
            client_type, source_name, concurrency, delay,
        )
        return client

    def get_global_config(self) -> dict:
        """Return the global config block."""
        return self._config.get("global", {})

    def get_enabled_sources(self) -> list[str]:
        """Return list of source names that are enabled."""
        return [
            name
            for name, cfg in self._config.get("sources", {}).items()
            if cfg.get("enabled", True)
        ]

    async def close_all(self):
        """Close all cached clients."""
        for name, client in self._clients.items():
            try:
                await client.close()
                logger.info("Closed client for '%s'", name)
            except Exception as exc:
                logger.warning("Error closing client for '%s': %s", name, exc)
        self._clients.clear()
