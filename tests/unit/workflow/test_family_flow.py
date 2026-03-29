"""Workflow regression tests for FamilyFlow iteration semantics."""
from __future__ import annotations

from pathlib import Path

from alpha_forge.app.domain.models import (
    CompositeScore,
    GuardResult,
    Iteration,
    JudgeOutput,
    SeedCard,
)
from alpha_forge.app.domain.states import FamilyState, Verdict
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.family_flow import FamilyFlow
from alpha_forge.app.workflow.orchestrator import Orchestrator
from tests.conftest import make_family, make_result, make_robustness


def _write_seed(store: MarkdownStore, seed_id: str) -> None:
    store.write_seed(
        SeedCard(
            seed_id=seed_id,
            seed_type="paper",
            source_title="source",
            raw_claim="claim",
            market="crypto_spot",
            horizon="5min",
            mechanism="mechanism",
            testable_hypothesis="hypothesis",
        ),
        stage="accepted",
    )


def _approved_output(judge_type: str) -> JudgeOutput:
    return JudgeOutput(judge_type=judge_type, verdict=Verdict.APPROVE)


def test_iterate_starts_new_cycle_and_updates_state_on_guard_failure(
    markdown_store: MarkdownStore,
    artifact_store: ArtifactStore,
    monkeypatch,
) -> None:
    family = make_family(
        family_id="fam_iter",
        seed_id="seed_iter",
        state=FamilyState.ITERATE,
        current_iteration=1,
    )
    markdown_store.write_family(family)
    _write_seed(markdown_store, "seed_iter")

    flow = FamilyFlow(markdown_store, artifact_store)
    monkeypatch.setattr(
        flow.researcher,
        "draft_plan",
        lambda *args, **kwargs: "drafted plan",
    )
    monkeypatch.setattr(
        flow.researcher,
        "write_code",
        lambda *args, **kwargs: {
            "features.py": "def compute_features(bars):\n    return bars\n",
            "model_config.py": "MODEL_CONFIG = {}\n",
            "signal_combiner.py": "def combine_signals(features, config):\n    return features['close'] * 0\n",
            "labels.py": "def compute_labels(bars):\n    return bars['close']\n",
        },
    )

    def fake_run_tier(tier: int, context: dict, client) -> list[JudgeOutput]:
        if tier == 1:
            return [_approved_output("leakage")]
        if tier == 2:
            return [_approved_output("code")]
        raise AssertionError(f"unexpected tier {tier}")

    monkeypatch.setattr("alpha_forge.app.workflow.family_flow.run_tier", fake_run_tier)
    monkeypatch.setattr(
        "alpha_forge.app.workflow.family_flow.run_all_guards",
        lambda *args, **kwargs: [
            GuardResult(guard_name="time_integrity", passed=False, violations=["guard failure"])
        ],
    )

    iteration = flow.run_iteration("fam_iter")
    updated_family = markdown_store.read_family("fam_iter")
    global_state = markdown_store.read_global_state()

    assert iteration.iteration_id == "iter_2"
    assert updated_family.current_iteration == 2
    assert updated_family.state == FamilyState.QUEUED
    assert global_state["current_iteration"] == 2


def test_code_revision_reuses_iteration_and_skips_plan_draft(
    markdown_store: MarkdownStore,
    artifact_store: ArtifactStore,
    monkeypatch,
) -> None:
    family = make_family(
        family_id="fam_code",
        seed_id="seed_code",
        state=FamilyState.CODE_REVISION_REQUIRED,
        current_iteration=2,
    )
    markdown_store.write_family(family)
    markdown_store.write_iteration(
        Iteration(iteration_id="iter_2", family_id="fam_code", plan="existing plan")
    )

    flow = FamilyFlow(markdown_store, artifact_store)
    monkeypatch.setattr(
        flow.researcher,
        "draft_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("draft_plan should not be called")),
    )
    monkeypatch.setattr(
        flow.researcher,
        "write_code",
        lambda *args, **kwargs: {
            "features.py": "def compute_features(bars):\n    return bars\n",
            "model_config.py": "MODEL_CONFIG = {}\n",
            "signal_combiner.py": "def combine_signals(features, config):\n    return features['close'] * 0\n",
            "labels.py": "def compute_labels(bars):\n    return bars['close']\n",
        },
    )

    def fake_run_tier(tier: int, context: dict, client) -> list[JudgeOutput]:
        if tier == 2:
            return [_approved_output("code")]
        raise AssertionError(f"unexpected tier {tier}")

    monkeypatch.setattr("alpha_forge.app.workflow.family_flow.run_tier", fake_run_tier)
    monkeypatch.setattr(
        "alpha_forge.app.workflow.family_flow.run_all_guards",
        lambda *args, **kwargs: [
            GuardResult(guard_name="time_integrity", passed=False, violations=["guard failure"])
        ],
    )

    iteration = flow.run_iteration("fam_code")
    updated_family = markdown_store.read_family("fam_code")
    global_state = markdown_store.read_global_state()

    assert iteration.iteration_id == "iter_2"
    assert iteration.plan == "existing plan"
    assert updated_family.current_iteration == 2
    assert updated_family.state == FamilyState.QUEUED
    assert global_state["current_iteration"] == 2


def test_successful_promotion_keeps_iteration_and_artifact_numbers_aligned(
    markdown_store: MarkdownStore,
    artifact_store: ArtifactStore,
    monkeypatch,
) -> None:
    family = make_family(
        family_id="fam_success",
        seed_id="seed_success",
        state=FamilyState.QUEUED,
        current_iteration=0,
    )
    markdown_store.write_family(family)
    _write_seed(markdown_store, "seed_success")

    flow = FamilyFlow(markdown_store, artifact_store)
    monkeypatch.setattr(flow.researcher, "draft_plan", lambda *args, **kwargs: "drafted plan")
    monkeypatch.setattr(
        flow.researcher,
        "write_code",
        lambda *args, **kwargs: {
            "features.py": "def compute_features(bars):\n    return bars\n",
            "model_config.py": "MODEL_CONFIG = {}\n",
            "signal_combiner.py": "def combine_signals(features, config):\n    return features['close'] * 0\n",
            "labels.py": "def compute_labels(bars):\n    return bars['close']\n",
        },
    )

    def fake_run_tier(tier: int, context: dict, client) -> list[JudgeOutput]:
        if tier == 1:
            return [_approved_output("leakage")]
        if tier == 2:
            return [_approved_output("code")]
        if tier == 3:
            return [_approved_output("result")]
        raise AssertionError(f"unexpected tier {tier}")

    monkeypatch.setattr("alpha_forge.app.workflow.family_flow.run_tier", fake_run_tier)
    monkeypatch.setattr(
        "alpha_forge.app.workflow.family_flow.run_all_guards",
        lambda *args, **kwargs: [GuardResult(guard_name="ok", passed=True)],
    )
    monkeypatch.setattr(
        "alpha_forge.app.workflow.family_flow.run_backtest",
        lambda *args, **kwargs: [make_result()],
    )
    monkeypatch.setattr(
        "alpha_forge.app.workflow.family_flow.run_robustness_battery",
        lambda *args, **kwargs: make_robustness(),
    )
    monkeypatch.setattr(
        "alpha_forge.app.workflow.family_flow.compute_composite_score",
        lambda *args, **kwargs: CompositeScore(alpha_quality=1.5, stability_bonus=0.2),
    )
    monkeypatch.setattr(
        "alpha_forge.app.workflow.family_flow.is_qualified_improvement",
        lambda *args, **kwargs: True,
    )

    iteration = flow.run_iteration("fam_success")
    updated_family = markdown_store.read_family("fam_success")
    global_state = markdown_store.read_global_state()

    assert iteration.iteration_id == "iter_1"
    assert updated_family.current_iteration == 1
    assert updated_family.state == FamilyState.PROMOTE_TO_HOLDOUT
    assert global_state["current_iteration"] == 1
    assert artifact_store.load_backtest_result("fam_success", 1) is not None
    assert artifact_store.load_robustness_result("fam_success", 1) is not None


def test_holdout_artifact_uses_active_iteration_number(
    markdown_store: MarkdownStore,
    artifact_store: ArtifactStore,
    monkeypatch,
) -> None:
    family = make_family(
        family_id="fam_holdout",
        seed_id="seed_holdout",
        state=FamilyState.PROMOTE_TO_HOLDOUT,
        current_iteration=3,
    )
    markdown_store.write_family(family)

    orchestrator = Orchestrator(markdown_store, artifact_store)
    monkeypatch.setattr(
        "alpha_forge.app.workflow.orchestrator.run_holdout",
        lambda *args, **kwargs: [make_result()],
    )
    monkeypatch.setattr(
        "alpha_forge.app.domain.scoring.compute_composite_score",
        lambda results: CompositeScore(alpha_quality=1.0),
    )

    orchestrator._run_holdout("fam_holdout")

    assert (
        artifact_store.root
        / "reports"
        / "fam_holdout"
        / "iter_3_holdout.json"
    ).exists()
