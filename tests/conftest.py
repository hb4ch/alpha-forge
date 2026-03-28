"""Shared test fixtures for alpha-forge test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from alpha_forge.app.domain.models import (
    AllowedMutations,
    BacktestResultSummary,
    IdeaFamily,
    MutationBudget,
    RobustnessResult,
    RobustnessTest,
)
from alpha_forge.app.domain.states import FamilyState
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore


def make_family(**overrides) -> IdeaFamily:
    """Factory for IdeaFamily with sensible defaults."""
    defaults = {
        "family_id": "fam_001",
        "seed_id": "seed_001",
        "base_hypothesis": "Test hypothesis",
        "mechanism": "Test mechanism",
        "state": FamilyState.QUEUED,
        "allowed_mutations": AllowedMutations(
            horizon=["5min", "15min"],
            venue=["binance", "okx"],
            representation=["ema"],
            combination=["equal_weight"],
            regime_filter=["volatility"],
        ),
        "mutation_budget": MutationBudget(),
    }
    defaults.update(overrides)
    return IdeaFamily(**defaults)


def make_result(**overrides) -> BacktestResultSummary:
    """Factory for BacktestResultSummary with sensible defaults."""
    defaults = {
        "symbol": "ETHUSDT",
        "timeframe": "5min",
        "sharpe": 1.5,
        "sortino": 2.0,
        "max_drawdown": -0.15,
        "total_return": 0.25,
        "calmar": 1.67,
        "turnover": 0.5,
        "win_rate": 0.55,
        "profit_factor": 1.8,
        "total_trades": 200,
        "exposure_pct": 0.6,
    }
    defaults.update(overrides)
    return BacktestResultSummary(**defaults)


def make_robustness(tests: list[tuple[str, bool, float]] | None = None) -> RobustnessResult:
    """Factory for RobustnessResult from list of (name, passed, degradation) tuples."""
    if tests is None:
        tests = [
            ("cost_2x", True, 0.05),
            ("cost_3x", True, 0.12),
            ("slippage_2x", True, 0.03),
        ]
    return RobustnessResult(
        tests=[
            RobustnessTest(test_name=name, passed=passed, degradation_pct=deg)
            for name, passed, deg in tests
        ]
    )


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Create a workspace directory structure for testing."""
    ws = tmp_path / "alpha_research"
    for subdir in [
        "families",
        "seeds/inbox",
        "seeds/pending",
        "seeds/accepted",
        "seeds/rejected",
        "reports",
        "ledger",
    ]:
        (ws / subdir).mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def markdown_store(tmp_workspace: Path) -> MarkdownStore:
    """MarkdownStore pointed at tmp workspace."""
    return MarkdownStore(tmp_workspace)


@pytest.fixture
def artifact_store(tmp_workspace: Path) -> ArtifactStore:
    """ArtifactStore pointed at tmp workspace."""
    return ArtifactStore(tmp_workspace)
