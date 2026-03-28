"""Tests for LLM streaming and retry behavior."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from alpha_forge.app.agents.llm_client import LLMClient


class TestStreamCallback:
    @patch("alpha_forge.app.agents.llm_client.anthropic")
    def test_streaming_calls_callback(self, mock_anthropic) -> None:
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.text_stream = iter(["Hello", " world"])
        mock_stream.get_final_text.return_value = "Hello world"

        mock_client = MagicMock()
        mock_client.messages.stream.return_value = mock_stream
        mock_anthropic.Anthropic.return_value = mock_client

        tokens = []
        client = LLMClient(stream_callback=lambda t: tokens.append(t))
        result = client.call("system", "user")

        assert result == "Hello world"
        assert tokens == ["Hello", " world"]

    @patch("alpha_forge.app.agents.llm_client.anthropic")
    def test_no_callback_uses_batch(self, mock_anthropic) -> None:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="batch response")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.Anthropic.return_value = mock_client

        client = LLMClient()
        result = client.call("system", "user")

        assert result == "batch response"
        mock_client.messages.create.assert_called_once()
