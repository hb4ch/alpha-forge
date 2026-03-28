"""Tiered LLM configuration: maps agent roles to model providers."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "llm.yaml"
DEFAULT_MODEL = "claude-sonnet-4-20250514"


class ProviderConfig(BaseModel):
    api_key_env: str
    base_url: str | None = None


class TierConfig(BaseModel):
    provider: str
    model: str


class LLMConfig(BaseModel):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    tiers: dict[str, TierConfig] = Field(default_factory=dict)
    roles: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> LLMConfig:
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)

    @classmethod
    def default(cls) -> LLMConfig:
        return cls(
            providers={"anthropic": ProviderConfig(api_key_env="ANTHROPIC_API_KEY")},
            tiers={"heavy": TierConfig(provider="anthropic", model=DEFAULT_MODEL)},
            roles={
                "researcher": "heavy",
                "seed_judge": "heavy",
                "leakage_judge": "heavy",
                "overfit_judge": "heavy",
                "realism_judge": "heavy",
                "code_judge": "heavy",
                "result_judge": "heavy",
                "mutation_judge": "heavy",
            },
        )

    def get_tier(self, role: str) -> TierConfig:
        tier_name = self.roles.get(role, "heavy")
        return self.tiers.get(tier_name, TierConfig(provider="anthropic", model=DEFAULT_MODEL))

    def get_provider(self, role: str) -> ProviderConfig:
        tier = self.get_tier(role)
        return self.providers.get(tier.provider, ProviderConfig(api_key_env="ANTHROPIC_API_KEY"))


# Module-level singleton (loaded lazily)
_config: LLMConfig | None = None


def load_config(path: Path | None = None) -> LLMConfig:
    global _config
    if _config is not None and path is None:
        return _config
    config_path = path or DEFAULT_CONFIG_PATH
    if config_path.exists():
        _config = LLMConfig.from_yaml(config_path)
    else:
        _config = LLMConfig.default()
    return _config


def get_client_for_role(role: str, config_path: Path | None = None):
    """Factory: return an LLMClient configured for the given role."""
    from alpha_forge.app.agents.llm_client import LLMClient

    config = load_config(config_path)
    tier = config.get_tier(role)
    provider_config = config.get_provider(role)

    return LLMClient(
        model=tier.model,
        provider=tier.provider,
        base_url=provider_config.base_url,
    )
