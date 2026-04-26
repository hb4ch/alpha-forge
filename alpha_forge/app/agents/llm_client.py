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
NETWORK_RETRIES = 10
BACKOFF_BASE = 5


class LLMClient:
    """Multi-provider LLM client for judge and researcher calls."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        provider: str = "anthropic",
        base_url: str | None = None,
        api_key: str | None = None,
        stream_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self.stream_callback = stream_callback

        if provider not in ("anthropic",):
            # OpenAI-compatible providers (openai, bigmodel, dgx_spark, etc.)
            import openai

            kwargs: dict[str, Any] = {}
            if base_url:
                kwargs["base_url"] = base_url
            if api_key:
                kwargs["api_key"] = api_key
            kwargs["timeout"] = 300.0  # 5min timeout for slow reasoning models
            self.openai_client = openai.OpenAI(**kwargs)
            self.anthropic_client = None
        else:
            kwargs_a: dict[str, Any] = {}
            if api_key:
                kwargs_a["api_key"] = api_key
            self.anthropic_client = anthropic.Anthropic(**kwargs_a)
            self.openai_client = None

    def call(
        self,
        system: str,
        user_prompt: str,
        max_tokens: int = 65536,
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
        if self.provider == "anthropic":
            return self._call_anthropic(system, user_prompt, max_tokens, temperature, cb)
        return self._call_openai(system, user_prompt, max_tokens, temperature, cb)

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
        # Always use streaming for OpenAI-compatible providers to prevent
        # HTTP timeouts during long reasoning/thinking phases.
        stream = self.openai_client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=msgs,
            stream=True,
        )
        reasoning_chunks: list[str] = []
        content_chunks: list[str] = []
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                if cb:
                    cb(delta.content)
                content_chunks.append(delta.content)
            elif getattr(delta, "reasoning_content", None):
                reasoning_chunks.append(delta.reasoning_content)
        # Prefer content; fall back to reasoning for models that only emit reasoning
        return "".join(content_chunks) or "".join(reasoning_chunks)

    def _retry_call(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        """Wrap a call with exponential backoff retry on retriable errors."""
        for attempt in range(NETWORK_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                if attempt == NETWORK_RETRIES or not self._is_retriable(e):
                    raise
                wait = BACKOFF_BASE
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
        """Check if an exception is retriable (network / rate-limit / timeout errors)."""
        err_str = str(e).lower()
        if isinstance(e, (ConnectionError, TimeoutError)):
            return True
        if "timeout" in err_str or "timed out" in err_str:
            return True
        if "429" in err_str or "500" in err_str or "502" in err_str or "503" in err_str:
            return True
        # Mid-stream drops from upstream (observed with OpenRouter + Cloudflare):
        # httpcore.RemoteProtocolError surfaces as "peer closed connection without
        # sending complete message body (incomplete chunked read)" — transient,
        # not a client error, safe to retry.
        if "incomplete chunked read" in err_str or "peer closed connection" in err_str:
            return True
        if "remoteprotocolerror" in type(e).__name__.lower():
            return True
        return False

    def call_json(
        self,
        system: str,
        user_prompt: str,
        max_tokens: int = 65536,
        temperature: float = 0.0,
        stream_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Make an API call and parse the response as JSON.

        Retries up to MAX_RETRIES times on parse failure.
        """
        for attempt in range(MAX_RETRIES + 1):
            raw = self.call(system, user_prompt, max_tokens, temperature, stream_callback=stream_callback)
            logger.debug("LLM raw response (%s): %s", self.model, raw)
            try:
                return _extract_json(raw)
            except (json.JSONDecodeError, ValueError) as e:
                if attempt == MAX_RETRIES:
                    logger.error("Failed to parse JSON after %d retries: %s", MAX_RETRIES, e)
                    raise
                logger.warning("JSON parse failed (attempt %d), retrying: %s", attempt + 1, e)
        raise RuntimeError("Unreachable")

    def call_json_validated(
        self,
        system: str,
        user_prompt: str,
        output_model: type,
        max_tokens: int = 65536,
        temperature: float = 0.0,
        stream_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Make an API call, parse JSON, and validate against a Pydantic model.

        On schema mismatch, sends the validation error back to the LLM as a
        correction prompt and retries. This forces the LLM to produce conforming
        output regardless of whether the provider supports structured output.
        """
        cb = stream_callback or self.stream_callback
        msgs = self._build_messages(system, user_prompt)

        for attempt in range(MAX_RETRIES + 1):
            raw_text = self._call_with_messages(msgs, max_tokens, temperature, cb)
            logger.debug("LLM raw response (%s): %s", self.model, raw_text)

            try:
                parsed = _extract_json(raw_text)
            except (json.JSONDecodeError, ValueError) as e:
                if attempt == MAX_RETRIES:
                    logger.error("Failed to parse JSON after %d retries: %s", MAX_RETRIES, e)
                    raise
                logger.warning("JSON parse failed (attempt %d), retrying with correction", attempt + 1)
                msgs.append({"role": "assistant", "content": raw_text})
                msgs.append({"role": "user", "content": (
                    f"Your response is not valid JSON. Error: {e}\n"
                    "Please respond with ONLY the corrected JSON object, no other text."
                )})
                continue

            # Validate against Pydantic model
            try:
                output_model.model_validate(parsed)
                return parsed
            except Exception as validation_err:
                if attempt == MAX_RETRIES:
                    logger.error(
                        "Schema validation failed after %d retries: %s",
                        MAX_RETRIES, validation_err,
                    )
                    raise
                logger.warning(
                    "Schema validation failed (attempt %d), retrying with correction: %s",
                    attempt + 1, validation_err,
                )
                msgs.append({"role": "assistant", "content": raw_text})
                msgs.append({"role": "user", "content": (
                    f"Your JSON does not match the required schema. Validation error:\n"
                    f"{validation_err}\n\n"
                    f"Please fix ONLY the invalid fields and respond with the complete "
                    f"corrected JSON object. Do not change the verdict or reasoning."
                )})

        raise RuntimeError("Unreachable")

    def _build_messages(
        self, system: str, user_prompt: str
    ) -> list[dict[str, str]]:
        """Build initial message list for multi-turn correction."""
        if self.provider == "anthropic":
            # Anthropic uses system param separately; store it for later
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ]
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]

    def _call_with_messages(
        self,
        msgs: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        cb: Callable[[str], None] | None,
    ) -> str:
        """Call the LLM with a full message list (for multi-turn correction)."""
        return self._retry_call(
            self._do_call_messages, msgs, max_tokens, temperature, cb
        )

    def _do_call_messages(
        self,
        msgs: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        cb: Callable[[str], None] | None,
    ) -> str:
        if self.provider == "anthropic":
            system = msgs[0]["content"] if msgs[0]["role"] == "system" else ""
            api_msgs = [m for m in msgs if m["role"] != "system"]
            if cb:
                with self.anthropic_client.messages.stream(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                    messages=api_msgs,
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
                    messages=api_msgs,
                )
                return response.content[0].text
        else:
            api_msgs = [m for m in msgs if m["role"] != "system"]
            system_msgs = [m for m in msgs if m["role"] == "system"]
            all_msgs = system_msgs + api_msgs
            stream = self.openai_client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=all_msgs,
                stream=True,
            )
            reasoning_chunks: list[str] = []
            content_chunks: list[str] = []
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    if cb:
                        cb(delta.content)
                    content_chunks.append(delta.content)
                elif getattr(delta, "reasoning_content", None):
                    reasoning_chunks.append(delta.reasoning_content)
            return "".join(content_chunks) or "".join(reasoning_chunks)


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
