"""Orchestrator paper-forward integration tests.

Drive Orchestrator._run_paper_forward with a stub paper_runner that
returns canned PaperResult instances; assert state transitions,
artifact persistence, and idempotency.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from alpha_forge.app.domain.states import FamilyState
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.orchestrator import Orchestrator
from alpha_forge.engine.paper_runner import PaperResult
from tests.conftest import make_family


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_orchestrator(
    markdown_store: MarkdownStore,
    artifact_store: ArtifactStore,
    paper_runner_fn,
    *,
    holdout_end: str = "2026-03-01",
) -> Orchestrator:
    """Orchestrator wired with an injectable paper_forward_runner +
    minimal splits.yaml so _run_paper_forward can resolve the window."""
    configs_dir = markdown_store.root / "configs"
    configs_dir.mkdir(parents=True, exist_ok=True)
    (configs_dir / "splits.yaml").write_text(
        f"holdout:\n  end: '{holdout_end}'\n  start: '2025-07-24'\n"
        "train:\n  end: '2024-12-17'\n  start: '2023-03-01'\n"
        "validation:\n  end: '2025-07-24'\n  start: '2024-12-17'\n"
    )
    return Orchestrator(
        store=markdown_store,
        artifact_store=artifact_store,
        configs_dir=configs_dir,
        client=_StubLLMClient(),
        paper_forward_runner=paper_runner_fn,
    )


class _StubLLMClient:
    """Orchestrator constructor needs a client; nothing in paper-forward
    actually invokes it."""


def _seed_family_at_promote_to_paper(
    markdown_store: MarkdownStore,
    artifact_store: ArtifactStore,
    family_id: str = "fam_test_paper",
) -> None:
    """Drop a family at the PROMOTE_TO_PAPER state with a research/ tree
    that export_strategy.py can build a bundle from."""
    family = make_family(
        family_id=family_id,
        seed_id="seed_paper",
        state=FamilyState.PROMOTE_TO_PAPER,
    )
    markdown_store.write_family(family)
    # Minimal research/ tree
    research = markdown_store.root / "families" / family_id / "research"
    research.mkdir(parents=True, exist_ok=True)
    (research / "features.py").write_text(
        "import pandas as pd\n"
        "def compute_features(bars: pd.DataFrame) -> pd.DataFrame:\n"
        "    return bars[['close']].rename(columns={'close': 'feat'})\n"
    )
    (research / "labels.py").write_text(
        "import pandas as pd\n"
        "def compute_labels(bars: pd.DataFrame) -> pd.Series:\n"
        "    return bars['close'].pct_change()\n"
    )
    (research / "model_config.py").write_text(
        "MODEL_CONFIG = {\n"
        "    'timeframe': '1h',\n"
        "    'universe': ['ETHUSDT'],\n"
        "}\n"
    )
    (research / "signal_combiner.py").write_text(
        "import pandas as pd\n"
        "def combine_signals(features: pd.DataFrame, config: dict) -> pd.Series:\n"
        "    return pd.Series(0.0, index=features.index)\n"
    )


def _passing_result(output_dir: Path) -> PaperResult:
    payload = {
        "schema_version": 1, "verdict": "PASS", "reasons": [],
        "metrics": {"sharpe": 1.5, "max_drawdown": 0.04, "trade_count": 5,
                    "net_return": 0.07},
    }
    return PaperResult.from_result_json(output_dir, payload)


def _failing_result(output_dir: Path, reason: str = "sharpe 0.10 < min 0.4") -> PaperResult:
    payload = {
        "schema_version": 1, "verdict": "FAIL", "reasons": [reason],
        "metrics": {"sharpe": 0.10, "max_drawdown": 0.05, "trade_count": 3,
                    "net_return": 0.005},
    }
    return PaperResult.from_result_json(output_dir, payload)


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_paper_forward_pass_advances_family_to_human_review(
    markdown_store: MarkdownStore, artifact_store: ArtifactStore,
):
    _seed_family_at_promote_to_paper(markdown_store, artifact_store)

    captured: dict[str, Any] = {}

    def fake_runner(**kwargs):
        captured.update(kwargs)
        return _passing_result(kwargs["output_dir"])

    orch = _make_orchestrator(markdown_store, artifact_store, fake_runner)
    out = orch._run_paper_forward("fam_test_paper")

    family = markdown_store.read_family("fam_test_paper")
    assert family.state == FamilyState.HUMAN_REVIEW, family.state
    assert out["verdict"] == "PASS"
    assert out["status"] == "pass"

    saved = artifact_store.load_paper_forward_result("fam_test_paper")
    assert saved is not None and saved["verdict"] == "PASS"

    # Right things passed to paper_runner
    assert captured["family_id"] == "fam_test_paper"
    assert captured["universe"] == ["ETHUSDT"]
    assert captured["timeframe"] == "1h"


def test_paper_forward_fail_archives_family(
    markdown_store: MarkdownStore, artifact_store: ArtifactStore,
):
    _seed_family_at_promote_to_paper(markdown_store, artifact_store)

    def fake_runner(**kwargs):
        return _failing_result(kwargs["output_dir"])

    orch = _make_orchestrator(markdown_store, artifact_store, fake_runner)
    out = orch._run_paper_forward("fam_test_paper")

    family = markdown_store.read_family("fam_test_paper")
    assert family.state == FamilyState.ARCHIVED_REJECTED, family.state
    assert out["verdict"] == "FAIL"
    assert "sharpe" in out["reasons"][0]


def test_paper_forward_idempotent_on_existing_artifact(
    markdown_store: MarkdownStore, artifact_store: ArtifactStore,
):
    """A pre-existing paper_forward_result.json short-circuits the runner."""
    _seed_family_at_promote_to_paper(markdown_store, artifact_store)
    artifact_store.save_paper_forward_result(
        "fam_test_paper",
        {"verdict": "PASS", "reasons": [], "metrics": {"sharpe": 2.0}},
    )

    invocations = {"count": 0}

    def fake_runner(**kwargs):
        invocations["count"] += 1
        return _passing_result(kwargs["output_dir"])

    orch = _make_orchestrator(markdown_store, artifact_store, fake_runner)
    out = orch._run_paper_forward("fam_test_paper")

    assert invocations["count"] == 0, "runner must not be invoked on existing artifact"
    assert out["verdict"] == "PASS"
    family = markdown_store.read_family("fam_test_paper")
    assert family.state == FamilyState.HUMAN_REVIEW


def test_paper_forward_empty_window_synthesises_fail(
    markdown_store: MarkdownStore, artifact_store: ArtifactStore,
):
    """If holdout_end > now, no paper window exists → synthetic FAIL."""
    _seed_family_at_promote_to_paper(markdown_store, artifact_store)

    invoked = {"count": 0}

    def fake_runner(**kwargs):
        invoked["count"] += 1
        return _passing_result(kwargs["output_dir"])

    # Set holdout end far in the future
    orch = _make_orchestrator(
        markdown_store, artifact_store, fake_runner, holdout_end="2099-01-01",
    )
    out = orch._run_paper_forward("fam_test_paper")

    assert invoked["count"] == 0, "runner must not be invoked when window is empty"
    assert out["verdict"] == "FAIL"
    assert "empty_paper_window" in out["reasons"]
    family = markdown_store.read_family("fam_test_paper")
    assert family.state == FamilyState.ARCHIVED_REJECTED


def test_paper_forward_appends_history(
    markdown_store: MarkdownStore, artifact_store: ArtifactStore,
):
    _seed_family_at_promote_to_paper(markdown_store, artifact_store)

    def fake_runner(**kwargs):
        return _passing_result(kwargs["output_dir"])

    orch = _make_orchestrator(markdown_store, artifact_store, fake_runner)
    orch._run_paper_forward("fam_test_paper")

    history_path = markdown_store.root / "families" / "fam_test_paper" / "HISTORY.md"
    text = history_path.read_text() if history_path.exists() else ""
    assert "Paper forward PASS" in text
