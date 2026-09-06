"""
Provider-agnostic LLM client with Groq → Gemini → OpenRouter fallback chain.

Architecture:
  - All 3 providers expose OpenAI-compatible chat completion APIs.
  - A single `openai.AsyncOpenAI` instance per provider, differentiated
    only by base_url, api_key, and model name.
  - On provider failure (auth error, 5xx, timeout, network): log explicit
    fallback trigger message and try the next provider in the chain.
  - On 429 rate limit: exponential backoff + jitter via tenacity before
    retrying the same provider (up to max_retries). If still 429 after
    retries, fall back to next provider.
  - If all 3 providers fail: raise LLMExtractionError.

Retry config (visible in logs):
  - Base delay: 1 second
  - Multiplier: 2x (exponential)
  - Max retries per provider: 3
  - Jitter: ±0.5s (random additive)
  - Max single wait: 16 seconds
"""

import json
import logging
import os
import time
import random
from dataclasses import dataclass, field
from typing import Any, Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError, APITimeoutError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception_type,
    before_sleep_log,
)

load_dotenv()

logger = logging.getLogger(__name__)


class LLMExtractionError(Exception):
    """Raised when all providers in the fallback chain have failed."""
    pass


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""
    name: str
    base_url: str
    api_key: str
    model: str
    max_tokens: int = 1024
    # Provider-specific headers (e.g. OpenRouter requires HTTP-Referer)
    extra_headers: dict = field(default_factory=dict)


def _build_provider_chain() -> list[ProviderConfig]:
    """Build the ordered provider chain from environment variables.

    Only includes providers whose API keys are actually set in .env.
    Warns (but doesn't crash) if a key is missing — the chain degrades
    gracefully to whatever providers are available.
    """
    chain: list[ProviderConfig] = []

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_key:
        chain.append(ProviderConfig(
            name="Groq",
            base_url="https://api.groq.com/openai/v1",
            api_key=groq_key,
            model="qwen/qwen3.8-27b",
            # Set to 800 to stay under Groq free-tier 1000 OTPM (output tokens per minute) limit
            max_tokens=800,
        ))
    else:
        logger.warning("[LLM] GROQ_API_KEY not set — Groq will be skipped in fallback chain")

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        chain.append(ProviderConfig(
            name="Gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=gemini_key,
            model="gemini-3.6-flash",
            # Gemini 3.6-flash is a thinking model: internal reasoning tokens
            # consume part of the max_tokens budget (~100-200 tokens), so we
            # set this higher than the other providers to leave room.
            max_tokens=2048,
        ))
    else:
        logger.warning("[LLM] GEMINI_API_KEY not set — Gemini will be skipped in fallback chain")

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if openrouter_key:
        chain.append(ProviderConfig(
            name="OpenRouter",
            base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_key,
            model="deepseek/deepseek-chat-v3-0324",
            max_tokens=1024,
            extra_headers={
                "HTTP-Referer": "https://github.com/DhyanMehta/graphone",
                "X-Title": "Graphone Pipeline",
            },
        ))
    else:
        logger.warning("[LLM] OPENROUTER_API_KEY not set — OpenRouter will be skipped in fallback chain")

    if not chain:
        logger.error("[LLM] No LLM provider keys found in .env — LLM extraction will be unavailable")

    return chain


class LLMClient:
    """Provider-agnostic LLM client with automatic fallback chain.

    Usage:
        client = LLMClient()
        result = await client.complete(
            system_prompt="Extract structured data...",
            user_prompt="Raw content here...",
        )
    """

    def __init__(self, provider_chain: Optional[list[ProviderConfig]] = None):
        self._chain = provider_chain or _build_provider_chain()
        self._clients: dict[str, AsyncOpenAI] = {}

        for provider in self._chain:
            self._clients[provider.name] = AsyncOpenAI(
                api_key=provider.api_key,
                base_url=provider.base_url,
                default_headers=provider.extra_headers or None,
                timeout=30.0,
            )

        logger.info(
            "[LLM] Initialized with provider chain: %s",
            " → ".join(p.name for p in self._chain) or "(empty)"
        )

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: Optional[dict[str, Any]] = None,
    ) -> str:
        """Send a chat completion request through the provider chain.

        Tries each provider in order. On failure, logs the reason and
        falls back to the next provider. Returns the raw response text.

        Args:
            system_prompt: System-level instructions for the LLM.
            user_prompt: The actual content/query to process.
            response_format: Optional response format hint (e.g. {"type": "json_object"}).

        Returns:
            The LLM's response text content.

        Raises:
            LLMExtractionError: If all providers in the chain fail.
        """
        if not self._chain:
            raise LLMExtractionError("No LLM providers configured — check .env for API keys")

        last_error: Optional[Exception] = None

        for provider in self._chain:
            try:
                result = await self._call_provider(provider, system_prompt, user_prompt, response_format)
                return result
            except Exception as exc:
                last_error = exc
                # Determine the next provider for the log message
                idx = self._chain.index(provider)
                if idx + 1 < len(self._chain):
                    next_name = self._chain[idx + 1].name
                    logger.warning(
                        "[LLM] Provider '%s' failed: %s. Falling back to '%s'",
                        provider.name, exc, next_name,
                    )
                else:
                    logger.error(
                        "[LLM] Provider '%s' failed: %s. No more providers in chain.",
                        provider.name, exc,
                    )

        raise LLMExtractionError(
            f"All {len(self._chain)} providers failed. Last error: {last_error}"
        )

    async def _call_provider(
        self,
        provider: ProviderConfig,
        system_prompt: str,
        user_prompt: str,
        response_format: Optional[dict[str, Any]] = None,
    ) -> str:
        """Make a single call to a specific provider, with retry on 429.

        Retries only on rate limit (429) errors using tenacity's
        exponential backoff + jitter. All other errors propagate
        immediately to trigger the provider fallback.
        """
        client = self._clients[provider.name]
        start_time = time.monotonic()

        # Define retry-decorated inner function for rate limit handling
        @retry(
            retry=retry_if_exception_type(RateLimitError),
            wait=wait_exponential_jitter(
                initial=1,       # Base delay: 1 second
                max=16,          # Max single wait: 16 seconds
                jitter=1,        # Jitter range: up to ±0.5s effectively
            ),
            stop=stop_after_attempt(3),  # Max 3 retries per provider
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        async def _do_call():
            kwargs: dict[str, Any] = {
                "model": provider.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": provider.max_tokens,
                "temperature": 0.1,  # Low temperature for structured extraction
            }
            if response_format:
                kwargs["response_format"] = response_format

            if provider.name == "Groq":
                # Explicitly disable thinking/reasoning mode on Groq (Qwen models)
                # to guarantee direct JSON instruct generation without consuming output budget.
                kwargs["extra_body"] = {"reasoning_effort": "none"}

            response = await client.chat.completions.create(**kwargs)
            return response

        try:
            response = await _do_call()
        except RateLimitError as exc:
            # All retries exhausted for rate limiting on this provider
            logger.warning(
                "[LLM] Provider '%s' rate-limited after 3 retries: %s",
                provider.name, exc,
            )
            raise
        except (APIError, APIConnectionError, APITimeoutError) as exc:
            # Non-retriable provider errors — propagate to trigger fallback
            raise
        except Exception as exc:
            # Unexpected errors — propagate to trigger fallback
            raise

        elapsed = time.monotonic() - start_time

        # Extract response content
        content = ""
        if response.choices and response.choices[0].message:
            content = response.choices[0].message.content or ""

        # Log call metadata
        usage = response.usage
        tokens_str = ""
        if usage:
            tokens_str = f", tokens: {usage.prompt_tokens}+{usage.completion_tokens}={usage.total_tokens}"

        logger.info(
            "[LLM] %s (%s) completed in %.2fs%s",
            provider.name, provider.model, elapsed, tokens_str,
        )

        return content

    async def test_provider(self, provider_name: str) -> dict[str, Any]:
        """Test connectivity to a specific provider with a trivial prompt.

        Returns a dict with: success, provider, model, response, error, latency_ms.
        Used for pre-flight verification.
        """
        provider = None
        for p in self._chain:
            if p.name == provider_name:
                provider = p
                break

        if not provider:
            return {
                "success": False,
                "provider": provider_name,
                "error": f"Provider '{provider_name}' not in chain (key missing?)",
            }

        start_time = time.monotonic()
        try:
            client = self._clients[provider.name]
            response = await client.chat.completions.create(
                model=provider.model,
                messages=[
                    {"role": "system", "content": "You are a test assistant. Respond with valid JSON."},
                    {"role": "user", "content": 'Return exactly: {"status": "ok", "provider": "<your model name>"}'},
                ],
                # Use provider's configured max_tokens — Gemini 3.6-flash
                # is a thinking model whose internal reasoning tokens
                # consume part of this budget.
                max_tokens=provider.max_tokens,
                temperature=0,
            )
            elapsed_ms = (time.monotonic() - start_time) * 1000
            content = response.choices[0].message.content if response.choices else ""
            usage = response.usage

            return {
                "success": True,
                "provider": provider_name,
                "model": provider.model,
                "response_raw": content,
                "latency_ms": round(elapsed_ms, 1),
                "tokens": {
                    "prompt": usage.prompt_tokens if usage else None,
                    "completion": usage.completion_tokens if usage else None,
                    "total": usage.total_tokens if usage else None,
                },
            }
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            return {
                "success": False,
                "provider": provider_name,
                "model": provider.model,
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": round(elapsed_ms, 1),
            }

    async def close(self):
        """Close all underlying HTTP clients."""
        for name, client in self._clients.items():
            try:
                await client.close()
            except Exception:
                pass
