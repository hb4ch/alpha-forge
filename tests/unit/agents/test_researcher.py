"""Tests for researcher prompt assembly."""

from __future__ import annotations

from alpha_forge.app.agents.researcher import ResearcherAgent
from alpha_forge.app.domain.models import SeedCard
from tests.conftest import make_family


class DummyResearcherClient:
    """Capture researcher prompt payloads."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def call(self, system: str, user_prompt: str, *args, **kwargs) -> str:
        self.calls.append(("call", system, user_prompt))
        return "drafted plan"

    def call_json(self, system: str, user_prompt: str, *args, **kwargs) -> dict[str, str]:
        self.calls.append(("call_json", system, user_prompt))
        return {
            "features.py": "def compute_features(bars):\n    return bars\n",
            "model_config.py": "MODEL_CONFIG = {}\n",
            "signal_combiner.py": "def combine_signals(features, config):\n    return features['close'] * 0\n",
            "labels.py": "def compute_labels(bars):\n    return bars['close']\n",
        }


def test_draft_plan_includes_family_seed_feedback_and_constraints() -> None:
    client = DummyResearcherClient()
    agent = ResearcherAgent(client)
    family = make_family(family_id="fam_prompt", current_iteration=3, strike_count=2)
    seed = SeedCard(
        seed_id="seed_prompt",
        seed_type="paper",
        source_title="source",
        raw_claim="raw claim",
        market="crypto_spot",
        horizon="15min",
        mechanism="test mechanism",
        testable_hypothesis="test hypothesis",
    )

    result = agent.draft_plan(family, seed, prior_feedback=["history item"])

    assert result == "drafted plan"
    _, system, user_prompt = client.calls[0]
    assert "Draft a concrete implementation plan" in system
    assert "- ID: fam_prompt" in user_prompt
    assert "- Claim: raw claim" in user_prompt
    assert "history item" in user_prompt
    assert "features.py, labels.py, model_config.py, signal_combiner.py" in user_prompt
    assert "No forward-looking operations" in user_prompt


def test_write_code_revision_includes_existing_code_context() -> None:
    client = DummyResearcherClient()
    agent = ResearcherAgent(client)
    family = make_family(current_iteration=2)
    existing_code = {
        "features.py": "def compute_features(bars):\n    return bars[['close']]\n",
        "labels.py": "def compute_labels(bars):\n    return bars['close']\n",
    }

    result = agent.write_code(
        family,
        "existing plan",
        prior_feedback=["fix signal threshold"],
        existing_code=existing_code,
    )

    assert sorted(result) == ["features.py", "labels.py", "model_config.py", "signal_combiner.py"]
    _, system, user_prompt = client.calls[0]
    assert "Generate Python code for the 4 research files" in system
    assert "existing plan" in user_prompt
    assert "fix signal threshold" in user_prompt
    assert "# === features.py ===" in user_prompt
    assert "Revise the existing implementation in place." in user_prompt
    assert "- signal_combiner.py" in user_prompt
