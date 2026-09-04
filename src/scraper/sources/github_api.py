"""
GitHub API client — fetches live star counts for repositories.

Uses a personal access token from .env for authenticated requests
(5,000 requests/hour vs 60 unauthenticated).
"""

import asyncio
import logging
import os
import re
from typing import Optional

from dotenv import load_dotenv

from src.scraper.http_client import HttpClient

logger = logging.getLogger(__name__)

load_dotenv()

# Pattern to extract owner/repo from a GitHub URL
GITHUB_REPO_PATTERN = re.compile(
    r"github\.com/([^/]+)/([^/\?#]+)", re.IGNORECASE
)


class GitHubAPI:
    """Thin wrapper around the GitHub REST API for star count lookups."""

    def __init__(self, client: HttpClient):
        self._client = client
        self._token = os.getenv("GITHUB_TOKEN")
        if not self._token:
            logger.warning(
                "GITHUB_TOKEN not set in .env — API rate limit will be "
                "60 requests/hour (vs 5,000 with a token)"
            )

    def _headers(self) -> dict:
        """Build request headers with auth if token is available."""
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self._token:
            headers["Authorization"] = f"token {self._token}"
        return headers

    @staticmethod
    def parse_repo_from_url(url: str) -> Optional[tuple[str, str]]:
        """
        Extract (owner, repo) from a GitHub URL.
        Returns None if the URL doesn't match the expected pattern.
        """
        match = GITHUB_REPO_PATTERN.search(url)
        if not match:
            return None
        owner = match.group(1)
        repo = match.group(2)
        # Strip .git suffix if present
        if repo.endswith(".git"):
            repo = repo[:-4]
        return (owner, repo)

    async def get_stars(self, repo_url: str) -> Optional[int]:
        """
        Fetch the current stargazer count for a GitHub repository.

        Args:
            repo_url: Full GitHub URL (e.g., https://github.com/owner/repo)

        Returns:
            Star count as int, or None if the repo doesn't exist or request fails.
        """
        parsed = self.parse_repo_from_url(repo_url)
        if not parsed:
            logger.debug("Could not parse GitHub URL: %s", repo_url)
            return None

        owner, repo = parsed
        api_url = f"https://api.github.com/repos/{owner}/{repo}"

        try:
            data = await self._client.fetch_json(api_url, headers=self._headers())
            stars = data.get("stargazers_count")
            if stars is not None:
                logger.debug("%s/%s has %d stars", owner, repo, stars)
                return int(stars)
            return None
        except Exception as exc:
            logger.warning("Failed to get stars for %s/%s: %s", owner, repo, exc)
            return None

    async def get_stars_batch(
        self, repo_urls: list[str]
    ) -> dict[str, Optional[int]]:
        """
        Fetch star counts for multiple repos concurrently.
        Returns a dict mapping repo_url -> star_count (or None).
        """
        results = {}

        async def _fetch_one(url: str):
            stars = await self.get_stars(url)
            results[url] = stars

        tasks = [_fetch_one(url) for url in repo_urls if url]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return results
