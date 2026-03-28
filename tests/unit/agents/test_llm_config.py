"""Tests for tiered LLM configuration."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from alpha_forge.app.agents.llm_config import LLMConfig, get_client_for_role


class TestLLMConfig:
    def test_loads_from_yaml(self, tmp_path: Path) -> None:
        config_data = {
            "providers": {
                "anthropic": {"api_key_env": "ANTHROPIC_API_KEY"},
            },
            "tiers": {
                "heavy": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
            },
            "roles": {
                "researcher": "heavy",
            },
        }
        path = tmp_path / "llm.yaml"
        path.write_text(yaml.dump(config_data))

        config = LLMConfig.from_yaml(path)
        assert config.roles["researcher"] == "heavy"
        assert config.tiers["heavy"].model == "claude-sonnet-4-20250514"

    def test_get_tier_for_role(self, tmp_path: Path) -> None:
        config_data = {
            "providers": {
                "anthropic": {"api_key_env": "ANTHROPIC_API_KEY"},
                "openai": {"api_key_env": "OPENAI_API_KEY", "base_url": "https://api.openai.com/v1"},
            },
            "tiers": {
                "heavy": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
                "light": {"provider": "openai", "model": "gpt-4o-mini"},
            },
            "roles": {
                "researcher": "heavy",
                "leakage_judge": "light",
            },
        }
        path = tmp_path / "llm.yaml"
        path.write_text(yaml.dump(config_data))

        config = LLMConfig.from_yaml(path)
        heavy = config.get_tier("researcher")
        light = config.get_tier("leakage_judge")
        assert heavy.provider == "anthropic"
        assert light.provider == "openai"
        assert light.model == "gpt-4o-mini"

    def test_fallback_when_no_config(self) -> None:
        config = LLMConfig.default()
        tier = config.get_tier("researcher")
        assert tier.provider == "anthropic"
        assert "claude" in tier.model


class TestGetClientForRole:
    def test_returns_client_with_correct_model(self, tmp_path: Path, monkeypatch) -> None:
        config_data = {
            "providers": {
                "anthropic": {"api_key_env": "ANTHROPIC_API_KEY"},
            },
            "tiers": {
                "heavy": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
            },
            "roles": {"researcher": "heavy"},
        }
        path = tmp_path / "llm.yaml"
        path.write_text(yaml.dump(config_data))

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        client = get_client_for_role("researcher", config_path=path)
        assert client.model == "claude-sonnet-4-20250514"
        assert client.provider == "anthropic"
