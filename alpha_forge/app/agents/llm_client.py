"""Thin wrapper around the Anthropic API for judge and researcher calls."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

import anthropic

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_RETRIES = 2
NETWORK_RETRIES = 3
BACKOFF_BASE = 1


class LLMClient:
    """Multi-provider LLM client for judge and researcher calls."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        provider: str = "anthropic",
        base_url: str | None = None,
        stream_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self.stream_callback = stream_callback

        if provider == "openai":
            import openai

            kwargs: dict[str, Any] = {}
            if base_url:
                kwargs["base_url"] = base_url
            self.openai_client = openai.OpenAI(**kwargs)
            self.anthropic_client = None
        else:
            self.anthropic_client = anthropic.Anthropic()
            self.openai_client = None

    def call(
        self,
        system: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        stream_callback: Callable[[str], None] | None = None,
    ) -> str:
        """Make a raw API call and return the text response."""
        cb = stream_callback or self.stream_callback
        return self._retry_call(
            self._do_call, system, user_prompt, max_tokens, temperature, cb
        )

    def _do_call(
        self,
        system: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        cb: Callable[[str], None] | None,
    ) -> str:
        if self.provider == "openai":
            return self._call_openai(system, user_prompt, max_tokens, temperature, cb)
        return self._call_anthropic(system, user_prompt, max_tokens, temperature, cb)

    def _call_anthropic(
        self,
        system: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        cb: Callable[[str], None] | None,
    ) -> str:
        if cb:
            with self.anthropic_client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            ) as stream:
                for token in stream.text_stream:
                    cb(token)
                return stream.get_final_text()
        else:
            response = self.anthropic_client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text

    def _call_openai(
        self,
        system: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        cb: Callable[[str], None] | None,
    ) -> str:
        msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        if cb:
            stream = self.openai_client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=msgs,
                stream=True,
            )
            chunks: list[str] = []
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    cb(token)
                    chunks.append(token)
            return "".join(chunks)
        else:
            response = self.openai_client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=msgs,
            )
            return response.choices[0].message.content

    def _retry_call(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        """Wrap a call with exponential backoff retry on retriable errors."""
        for attempt in range(NETWORK_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                if attempt == NETWORK_RETRIES or not self._is_retriable(e):
                    raise
                wait = BACKOFF_BASE * (4**attempt)
                logger.warning(
                    "LLM call failed (attempt %d), retrying in %ds: %s",
                    attempt + 1,
                    wait,
                    e,
                )
                time.sleep(wait)
        raise RuntimeError("Unreachable")

    @staticmethod
    def _is_retriable(e: Exception) -> bool:
        """Check if an exception is retriable (network / rate-limit errors)."""
        err_str = str(e).lower()
        if isinstance(e, (ConnectionError, TimeoutError)):
            return True
        if "429" in err_str or "500" in err_str or "502" in err_str or "503" in err_str:
            return True
        return False

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
