"""Tests for _extract_json helper in llm_client."""

import pytest

from alpha_forge.app.agents.llm_client import _extract_json


class TestExtractJson:
    """Tests for JSON extraction from raw LLM responses."""

    def test_parses_raw_json(self):
        result = _extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_extracts_from_json_fenced_block(self):
        text = '```json\n{"key": "value"}\n```'
        result = _extract_json(text)
        assert result == {"key": "value"}

    def test_extracts_from_bare_fenced_block(self):
        text = '```\n{"key": "value"}\n```'
        result = _extract_json(text)
        assert result == {"key": "value"}

    def test_raises_value_error_on_plain_text(self):
        with pytest.raises(ValueError, match="Could not extract JSON"):
            _extract_json("no json here")

    def test_handles_leading_trailing_whitespace(self):
        result = _extract_json('  {"key": "value"}  ')
        assert result == {"key": "value"}

    def test_handles_json_with_text_before_code_block(self):
        text = 'Here is the result:\n```json\n{"key": "value"}\n```'
        result = _extract_json(text)
        assert result == {"key": "value"}
