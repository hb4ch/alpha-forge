"""Thin wrapper around the Anthropic API for judge and researcher calls."""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_RETRIES = 2


class LLMClient:
    """Claude API client for structured judge/researcher calls."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.client = anthropic.Anthropic()
        self.model = model

    def call(
        self,
        system: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        """Make a raw API call and return the text response."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text

    def call_json(
        self,
        system: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Make an API call and parse the response as JSON.

        Retries up to MAX_RETRIES times on parse failure.
        """
        for attempt in range(MAX_RETRIES + 1):
            raw = self.call(system, user_prompt, max_tokens, temperature)
            try:
                return _extract_json(raw)
            except (json.JSONDecodeError, ValueError) as e:
                if attempt == MAX_RETRIES:
                    logger.error("Failed to parse JSON after %d retries: %s", MAX_RETRIES, e)
                    raise
                logger.warning("JSON parse failed (attempt %d), retrying: %s", attempt + 1, e)
        raise RuntimeError("Unreachable")


def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from a response that may contain markdown fences."""
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code blocks
    for marker in ("```json", "```"):
        if marker in text:
            start = text.index(marker) + len(marker)
            end = text.index("```", start)
            return json.loads(text[start:end].strip())

    raise ValueError(f"Could not extract JSON from response: {text[:200]}...")
