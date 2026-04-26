"""Workflow regression tests for FamilyFlow iteration semantics."""
from __future__ import annotations

from pathlib import Path
from typing import Any

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


class RecordingBus:
    """Capture workflow events for assertions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit_sync(self, event: str, data: dict[str, Any]) -> None:
        self.events.append((event, data))


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

    def fake_run_tier(tier: int, context: dict, client=None) -> list[JudgeOutput]:
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
    assert updated_family.state == FamilyState.CODE_REVISION_REQUIRED
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

    def fake_run_tier(tier: int, context: dict, client=None) -> list[JudgeOutput]:
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
    assert updated_family.state == FamilyState.CODE_REVISION_REQUIRED
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

    def fake_run_tier(tier: int, context: dict, client=None) -> list[JudgeOutput]:
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


def test_family_flow_does_not_forward_researcher_client_to_judges(
    markdown_store: MarkdownStore,
    artifact_store: ArtifactStore,
    monkeypatch,
) -> None:
    family = make_family(
        family_id="fam_routing",
        seed_id="seed_routing",
        state=FamilyState.QUEUED,
        current_iteration=0,
    )
    markdown_store.write_family(family)
    _write_seed(markdown_store, "seed_routing")

    researcher_client = object()
    flow = FamilyFlow(markdown_store, artifact_store, client=researcher_client)
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

    received_clients: list[object | None] = []

    def fake_run_tier(tier: int, context: dict, client=None) -> list[JudgeOutput]:
        received_clients.append(client)
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

    flow.run_iteration("fam_routing")

    assert received_clients == [None, None]


def test_code_revision_passes_existing_code_and_complete_tier2_context(
    markdown_store: MarkdownStore,
    artifact_store: ArtifactStore,
    monkeypatch,
) -> None:
    family = make_family(
        family_id="fam_revision",
        seed_id="seed_revision",
        state=FamilyState.CODE_REVISION_REQUIRED,
        current_iteration=2,
    )
    markdown_store.write_family(family)
    markdown_store.write_iteration(
        Iteration(iteration_id="iter_2", family_id="fam_revision", plan="existing plan")
    )

    research_dir = markdown_store.root / "families" / "fam_revision" / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    old_code = {
        "features.py": "def compute_features(bars):\n    return bars[['close']]\n",
        "model_config.py": "MODEL_CONFIG = {'lookback': 10}\n",
        "signal_combiner.py": "def combine_signals(features, config):\n    return features['close'] * 0\n",
        "labels.py": "def compute_labels(bars):\n    return bars['close']\n",
    }
    for filename, content in old_code.items():
        (research_dir / filename).write_text(content)
    artifact_store.save_code_snapshot("fam_revision", 2, old_code)

    bus = RecordingBus()
    flow = FamilyFlow(markdown_store, artifact_store, bus=bus)
    monkeypatch.setattr(
        flow.researcher,
        "draft_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("draft_plan should not be called")),
    )

    captured_existing_code: dict[str, str] | None = None

    def fake_write_code(*args, **kwargs):
        nonlocal captured_existing_code
        captured_existing_code = kwargs.get("existing_code")
        return {
            "features.py": "def compute_features(bars):\n    return bars[['close', 'volume']]\n",
            "model_config.py": "MODEL_CONFIG = {'lookback': 20}\n",
            "signal_combiner.py": "def combine_signals(features, config):\n    return features['volume'] * 0\n",
            "labels.py": "def compute_labels(bars):\n    return bars['close']\n",
        }

    monkeypatch.setattr(flow.researcher, "write_code", fake_write_code)

    captured_tier2_context: dict[str, Any] | None = None

    def fake_run_tier(tier: int, context: dict, client=None) -> list[JudgeOutput]:
        nonlocal captured_tier2_context
        if tier == 2:
            captured_tier2_context = context
            return [_approved_output("code")]
        raise AssertionError(f"unexpected tier {tier}")

    monkeypatch.setattr("alpha_forge.app.workflow.family_flow.run_tier", fake_run_tier)
    monkeypatch.setattr(
        "alpha_forge.app.workflow.family_flow.run_all_guards",
        lambda *args, **kwargs: [
            GuardResult(guard_name="time_integrity", passed=False, violations=["guard failure"])
        ],
    )

    flow.run_iteration("fam_revision")

    assert captured_existing_code == old_code
    assert captured_tier2_context is not None
    assert captured_tier2_context["plan"] == "existing plan"
    assert captured_tier2_context["changed_files"] == [
        "features.py",
        "labels.py",
        "model_config.py",
        "signal_combiner.py",
    ]
    assert captured_tier2_context["allowed_files"] == [
        "features.py",
        "labels.py",
        "model_config.py",
        "signal_combiner.py",
    ]
    assert "Any file outside families/<family_id>/research/" in captured_tier2_context["forbidden_files"]
    assert "--- a/features.py" in captured_tier2_context["diff"]
    assert "volume" in captured_tier2_context["code"]
    assert (
        "Tier-2 code review (Leakage, Code)"
        in [data["task"] for event, data in bus.events if event == "llm_start"]
    )


def test_code_judge_deadlock_escape_force_promotes_after_max_revisions(
    markdown_store: MarkdownStore,
    artifact_store: ArtifactStore,
    monkeypatch,
) -> None:
    """After MAX_CODE_REVISIONS consecutive REVISE verdicts the loop should promote
    the iteration to guards/backtest, recording the unresolved code-judge items as
    deferred warnings rather than bouncing back to CODE_REVISION_REQUIRED forever."""
    from alpha_forge.app.workflow.family_flow import MAX_CODE_REVISIONS

    family = make_family(
        family_id="fam_deadlock",
        seed_id="seed_deadlock",
        state=FamilyState.CODE_REVISION_REQUIRED,
        current_iteration=4,
    )
    markdown_store.write_family(family)
    # Iteration already had MAX-1 revisions; the increment in _prepare_iteration
    # will push it to MAX, triggering the deadlock escape on this run.
    markdown_store.write_iteration(
        Iteration(
            iteration_id="iter_4",
            family_id="fam_deadlock",
            plan="existing plan",
            code_revision_count=MAX_CODE_REVISIONS - 1,
        )
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

    guards_called: list[bool] = []

    def fake_run_tier(tier: int, context: dict, client=None) -> list[JudgeOutput]:
        if tier == 2:
            return [
                JudgeOutput(
                    judge_type="code",
                    verdict=Verdict.REVISE,
                    reasoning_summary="still wrong",
                    must_fix=["implement X correctly", "also Y"],
                )
            ]
        raise AssertionError(f"unexpected tier {tier}")

    def fake_guards(*args, **kwargs):
        guards_called.append(True)
        # Force a guard failure so the run terminates here without needing a real backtest.
        return [
            GuardResult(
                guard_name="time_integrity",
                passed=False,
                violations=["sentinel"],
            )
        ]

    monkeypatch.setattr("alpha_forge.app.workflow.family_flow.run_tier", fake_run_tier)
    monkeypatch.setattr("alpha_forge.app.workflow.family_flow.run_all_guards", fake_guards)

    iteration = flow.run_iteration("fam_deadlock")

    # Deadlock escape must have routed past tier-2 into the guard step.
    assert guards_called == [True], "expected guards to run after deadlock escape"
    # Iteration carries the unresolved code-judge items as deferred warnings.
    assert iteration.code_judge_deferred
    assert any("implement X correctly" in entry for entry in iteration.code_judge_deferred)
    assert any("[code]" in entry for entry in iteration.code_judge_deferred)
    # HISTORY recorded the escape so subsequent runs can see it.
    history = (markdown_store.root / "families" / "fam_deadlock" / "HISTORY.md").read_text()
    assert "code-judge deadlock" in history


def test_code_review_context_uses_fallback_diff_without_prior_snapshot(
    markdown_store: MarkdownStore,
    artifact_store: ArtifactStore,
) -> None:
    flow = FamilyFlow(markdown_store, artifact_store)

    context = flow._build_code_review_context(
        "plan",
        ["history"],
        None,
        {"features.py": "def compute_features(bars):\n    return bars\n"},
    )

    assert context["diff"] == "No prior snapshot available."


class TestDetectSimplificationNeeds:
    """Tests for _detect_simplification_needs reading prior judge outputs."""

    def test_returns_empty_when_no_prior_iteration(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        markdown_store.write_family(make_family(family_id="fam_simp"))
        flow = FamilyFlow(markdown_store, artifact_store)
        assert flow._detect_simplification_needs("fam_simp") == []

    def test_extracts_must_fix_from_high_dof_risk(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        markdown_store.write_family(make_family(family_id="fam_simp"))
        iteration = Iteration(
            iteration_id="iter_1",
            family_id="fam_simp",
            judge_outputs=[
                JudgeOutput(
                    judge_type="overfit",
                    verdict=Verdict.REVISE,
                    degrees_of_freedom_risk="high",
                    must_fix=["Remove unused regime filter"],
                ),
            ],
        )
        markdown_store.write_iteration(iteration)
        flow = FamilyFlow(markdown_store, artifact_store)
        result = flow._detect_simplification_needs("fam_simp")
        assert "Remove unused regime filter" in result

    def test_detects_dead_code_in_reasoning(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        markdown_store.write_family(make_family(family_id="fam_simp"))
        iteration = Iteration(
            iteration_id="iter_1",
            family_id="fam_simp",
            judge_outputs=[
                JudgeOutput(
                    judge_type="overfit",
                    verdict=Verdict.REVISE,
                    reasoning_summary="Short side has 0% short exposure, dead code in regime filter",
                ),
            ],
        )
        markdown_store.write_iteration(iteration)
        flow = FamilyFlow(markdown_store, artifact_store)
        result = flow._detect_simplification_needs("fam_simp")
        assert any("0% activation" in item or "regime" in item.lower() for item in result)


class TestModeRouting:
    """Tests for iteration mode routing (replan / revise_code / adjust_config)."""

    def test_revise_code_mode_skips_plan_phase(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore, monkeypatch,
    ) -> None:
        """When next_iteration_mode='revise_code', plan phase is skipped and existing plan is reused."""
        family = make_family(
            family_id="fam_mode",
            seed_id="seed_mode",
            state=FamilyState.ITERATE,
            current_iteration=2,
            next_iteration_mode="revise_code",
        )
        markdown_store.write_family(family)
        _write_seed(markdown_store, "seed_mode")
        # Write a previous iteration with a plan so it can be reused
        markdown_store.write_iteration(
            Iteration(iteration_id="iter_2", family_id="fam_mode", plan="existing plan from iter 2")
        )
        # Write research files so existing_code can be read
        research_dir = markdown_store.root / "families" / "fam_mode" / "research"
        research_dir.mkdir(parents=True, exist_ok=True)
        for fname in ("features.py", "model_config.py", "signal_combiner.py", "labels.py"):
            (research_dir / fname).write_text(f"# {fname}\n")

        flow = FamilyFlow(markdown_store, artifact_store)
        monkeypatch.setattr(
            flow.researcher,
            "draft_plan",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("draft_plan should NOT be called in revise_code mode")),
        )

        captured_kwargs: dict = {}

        def fake_write_code(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return {
                "features.py": "def compute_features(bars):\n    return bars\n",
                "model_config.py": "MODEL_CONFIG = {}\n",
                "signal_combiner.py": "def combine_signals(features, config):\n    return features['close'] * 0\n",
                "labels.py": "def compute_labels(bars):\n    return bars['close']\n",
            }

        monkeypatch.setattr(flow.researcher, "write_code", fake_write_code)

        def fake_run_tier(tier: int, context: dict, client=None) -> list[JudgeOutput]:
            if tier == 2:
                return [_approved_output("code")]
            raise AssertionError(f"unexpected tier {tier}")

        monkeypatch.setattr("alpha_forge.app.workflow.family_flow.run_tier", fake_run_tier)
        monkeypatch.setattr(
            "alpha_forge.app.workflow.family_flow.run_all_guards",
            lambda *args, **kwargs: [GuardResult(guard_name="ok", passed=False, violations=["fail"])],
        )

        iteration = flow.run_iteration("fam_mode")

        # Plan was reused, not re-drafted
        assert iteration.plan == "existing plan from iter 2"
        # Mode was passed to write_code
        assert captured_kwargs.get("mode") == "revise_code"
        # Existing code was passed for revision
        assert captured_kwargs.get("existing_code") is not None

    def test_adjust_config_mode_skips_plan_and_passes_existing_code(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore, monkeypatch,
    ) -> None:
        """adjust_config mode: skips plan, passes existing code, sends mode to researcher."""
        family = make_family(
            family_id="fam_adj",
            seed_id="seed_adj",
            state=FamilyState.ITERATE,
            current_iteration=1,
            next_iteration_mode="adjust_config",
        )
        markdown_store.write_family(family)
        _write_seed(markdown_store, "seed_adj")
        markdown_store.write_iteration(
            Iteration(iteration_id="iter_1", family_id="fam_adj", plan="previous plan")
        )
        research_dir = markdown_store.root / "families" / "fam_adj" / "research"
        research_dir.mkdir(parents=True, exist_ok=True)
        for fname in ("features.py", "model_config.py", "signal_combiner.py", "labels.py"):
            (research_dir / fname).write_text(f"# {fname}\n")

        flow = FamilyFlow(markdown_store, artifact_store)
        monkeypatch.setattr(
            flow.researcher, "draft_plan",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("draft_plan should NOT be called")),
        )

        captured_mode = [None]

        def fake_write_code(*args, **kwargs):
            captured_mode[0] = kwargs.get("mode")
            return {
                "features.py": "def compute_features(bars):\n    return bars\n",
                "model_config.py": "MODEL_CONFIG = {}\n",
                "signal_combiner.py": "def combine_signals(features, config):\n    return features['close'] * 0\n",
                "labels.py": "def compute_labels(bars):\n    return bars['close']\n",
            }

        monkeypatch.setattr(flow.researcher, "write_code", fake_write_code)

        def fake_run_tier(tier: int, context: dict, client=None) -> list[JudgeOutput]:
            if tier == 2:
                return [_approved_output("code")]
            raise AssertionError(f"unexpected tier {tier}")

        monkeypatch.setattr("alpha_forge.app.workflow.family_flow.run_tier", fake_run_tier)
        monkeypatch.setattr(
            "alpha_forge.app.workflow.family_flow.run_all_guards",
            lambda *args, **kwargs: [GuardResult(guard_name="ok", passed=False, violations=["fail"])],
        )

        flow.run_iteration("fam_adj")
        assert captured_mode[0] == "adjust_config"

    def test_replan_mode_goes_through_full_plan_phase(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore, monkeypatch,
    ) -> None:
        """When next_iteration_mode='replan' (default), full plan phase runs."""
        family = make_family(
            family_id="fam_replan",
            seed_id="seed_replan",
            state=FamilyState.ITERATE,
            current_iteration=1,
            next_iteration_mode="replan",
        )
        markdown_store.write_family(family)
        _write_seed(markdown_store, "seed_replan")

        flow = FamilyFlow(markdown_store, artifact_store)
        plan_called = [False]

        def fake_draft_plan(*args, **kwargs):
            plan_called[0] = True
            return "new plan"

        monkeypatch.setattr(flow.researcher, "draft_plan", fake_draft_plan)
        monkeypatch.setattr(
            flow.researcher, "write_code",
            lambda *args, **kwargs: {
                "features.py": "def compute_features(bars):\n    return bars\n",
                "model_config.py": "MODEL_CONFIG = {}\n",
                "signal_combiner.py": "def combine_signals(features, config):\n    return features['close'] * 0\n",
                "labels.py": "def compute_labels(bars):\n    return bars['close']\n",
            },
        )

        def fake_run_tier(tier: int, context: dict, client=None) -> list[JudgeOutput]:
            if tier in (1, 2):
                return [_approved_output("leakage" if tier == 1 else "code")]
            raise AssertionError(f"unexpected tier {tier}")

        monkeypatch.setattr("alpha_forge.app.workflow.family_flow.run_tier", fake_run_tier)
        monkeypatch.setattr(
            "alpha_forge.app.workflow.family_flow.run_all_guards",
            lambda *args, **kwargs: [GuardResult(guard_name="ok", passed=False, violations=["fail"])],
        )

        flow.run_iteration("fam_replan")
        assert plan_called[0], "draft_plan should be called in replan mode"


class TestGetHistoryContext:
    """Tests for _get_history_context windowed history."""

    def test_returns_empty_when_no_history(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        markdown_store.write_family(make_family(family_id="fam_hist"))
        flow = FamilyFlow(markdown_store, artifact_store)
        assert flow._get_history_context("fam_hist") == []

    def test_returns_full_text_with_few_entries(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        markdown_store.write_family(make_family(family_id="fam_hist"))
        markdown_store.append_history("fam_hist", "Entry one")
        markdown_store.append_history("fam_hist", "Entry two")
        flow = FamilyFlow(markdown_store, artifact_store)
        result = flow._get_history_context("fam_hist")
        assert len(result) == 1
        assert "Entry one" in result[0]
        assert "Entry two" in result[0]

    def test_returns_summary_plus_recent_with_many_entries(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        markdown_store.write_family(make_family(family_id="fam_hist"))
        for i in range(5):
            markdown_store.append_history("fam_hist", f"Entry {i}")
        flow = FamilyFlow(markdown_store, artifact_store)
        result = flow._get_history_context("fam_hist")
        assert len(result) == 1
        # Should contain history marker and the last 3 entries
        assert "HISTORY" in result[0]
        assert "Entry 4" in result[0]
        assert "Entry 3" in result[0]
        assert "Entry 2" in result[0]

    def test_surfaces_best_in_lineage_anchor(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        """Best-scoring entry is anchored in the context so judges recognize regressions."""
        markdown_store.write_family(make_family(family_id="fam_anchor"))
        # Five iterations with varying scores; iter_4 is the best (+0.300)
        scores = [-0.5, 0.1, -0.2, 0.3, 0.05]
        for i, s in enumerate(scores):
            markdown_store.append_history(
                "fam_anchor", f"Iteration iter_{i+1} body\nScore: {s:+.3f}"
            )
        flow = FamilyFlow(markdown_store, artifact_store)
        result = flow._get_history_context("fam_anchor")
        assert len(result) == 1
        text = result[0]
        assert "BEST IN LINEAGE" in text
        assert "+0.300" in text
        # The actual best-iteration body should be embedded in the anchor
        assert "iter_4" in text

    def test_no_anchor_when_no_scored_entries(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        markdown_store.write_family(make_family(family_id="fam_unscored"))
        for i in range(5):
            markdown_store.append_history("fam_unscored", f"GUARD FAILURE {i}")
        flow = FamilyFlow(markdown_store, artifact_store)
        result = flow._get_history_context("fam_unscored")
        assert len(result) == 1
        assert "BEST IN LINEAGE" not in result[0]


class TestExtractJudgeFeedback:
    """Tests for _extract_judge_feedback prioritization and capping."""

    def test_caps_must_fix_items_to_max(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        """15 must_fix items across judges should be capped to 3."""
        flow = FamilyFlow(markdown_store, artifact_store)
        outputs = []
        for judge in ("leakage", "code", "overfit", "realism", "result"):
            outputs.append(JudgeOutput(
                judge_type=judge,
                verdict=Verdict.REVISE,
                reasoning_summary=f"{judge} reasoning",
                must_fix=[f"{judge} fix {i}" for i in range(3)],
            ))
        result = flow._extract_judge_feedback(outputs, max_items=3)
        must_fix_lines = [r for r in result if "MUST FIX" in r]
        assert len(must_fix_lines) == 3
        # Should contain deferred note
        assert any("deferred" in r for r in result)

    def test_priority_order_leakage_before_result(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        """Leakage items should appear before result items."""
        flow = FamilyFlow(markdown_store, artifact_store)
        outputs = [
            JudgeOutput(judge_type="result", verdict=Verdict.REVISE, must_fix=["result fix"]),
            JudgeOutput(judge_type="leakage", verdict=Verdict.REVISE, must_fix=["leakage fix"]),
        ]
        result = flow._extract_judge_feedback(outputs, max_items=2)
        must_fix_lines = [r for r in result if "MUST FIX" in r]
        assert len(must_fix_lines) == 2
        # Leakage should come first
        leakage_idx = next(i for i, r in enumerate(must_fix_lines) if "leakage" in r)
        result_idx = next(i for i, r in enumerate(must_fix_lines) if "result" in r)
        assert leakage_idx < result_idx

    def test_includes_reasoning_even_when_capped(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        """Reasoning summaries should always be included, not capped."""
        flow = FamilyFlow(markdown_store, artifact_store)
        outputs = [
            JudgeOutput(judge_type="overfit", verdict=Verdict.REVISE,
                        reasoning_summary="important overfit reasoning",
                        must_fix=["fix1", "fix2", "fix3", "fix4"]),
        ]
        result = flow._extract_judge_feedback(outputs, max_items=2)
        assert any("important overfit reasoning" in r for r in result)


class TestFeedbackConflictDetection:
    """Tests for _detect_feedback_conflicts."""

    def test_detects_drop_vs_keep_conflict(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        """Opposing 'drop BTC' vs 'keep BTC' produces a conflict message."""
        items = [
            ("result", "Drop BTCUSDT from the universe — it drags aggregate Sharpe"),
            ("overfit", "Keep BTCUSDT in the universe — removing it is asset-shopping"),
        ]
        conflicts, consumed = FamilyFlow._detect_feedback_conflicts(items)
        assert len(conflicts) == 1
        assert "DISAGREE" in conflicts[0]
        assert "BTC" in conflicts[0]
        assert consumed == {0, 1}

    def test_no_conflict_same_direction(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        """Same-direction advice does not produce conflict."""
        items = [
            ("result", "Drop BTCUSDT — no edge"),
            ("overfit", "Drop BTCUSDT — simplify universe"),
        ]
        conflicts, consumed = FamilyFlow._detect_feedback_conflicts(items)
        assert len(conflicts) == 0

    def test_no_conflict_same_judge(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        """Opposing advice from the same judge is not flagged as inter-judge conflict."""
        items = [
            ("result", "Drop BTCUSDT"),
            ("result", "Add BTCUSDT back"),
        ]
        conflicts, consumed = FamilyFlow._detect_feedback_conflicts(items)
        assert len(conflicts) == 0


class TestResultReviewContext:
    """Tests for _build_result_review_context including code and plan."""

    def test_includes_code_and_plan(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        family = make_family(family_id="fam_ctx")
        flow = FamilyFlow(markdown_store, artifact_store)
        ctx = flow._build_result_review_context(
            [], make_robustness(), None, 1, family,
            code="def foo(): pass", plan="test plan",
        )
        assert ctx["code"] == "def foo(): pass"
        assert ctx["plan"] == "test plan"

    def test_defaults_code_and_plan_to_empty(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        family = make_family(family_id="fam_ctx2")
        flow = FamilyFlow(markdown_store, artifact_store)
        ctx = flow._build_result_review_context([], make_robustness(), None, 1, family)
        assert ctx["code"] == ""
        assert ctx["plan"] == ""


class TestHistoryFiltersFailures:
    """Tests for _get_history_context filtering guard/error entries."""

    def test_counts_only_scored_iterations(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        markdown_store.write_family(make_family(family_id="fam_hfilt"))
        # 3 scored entries
        for i in range(3):
            markdown_store.append_history("fam_hfilt", f"Iteration iter_{i}: stage=...\nScore: {0.5 + i * 0.1}")
        # 5 guard-failure entries (no Score: line)
        for i in range(5):
            markdown_store.append_history("fam_hfilt", f"Iteration iter_x: GUARD FAILURE\n  config hash missing")
        # 3 recent entries (shown in full)
        for i in range(3):
            markdown_store.append_history("fam_hfilt", f"Recent entry {i}\nScore: {0.8 + i * 0.01}")

        flow = FamilyFlow(markdown_store, artifact_store)
        result = flow._get_history_context("fam_hfilt")
        assert len(result) == 1
        # Summary should say "3 scored iterations", not "8 prior iterations"
        assert "3 scored iterations" in result[0]
        # Should still show recent entries
        assert "Recent entry 2" in result[0]

    def test_handles_negative_scores(
        self, markdown_store: MarkdownStore, artifact_store: ArtifactStore,
    ) -> None:
        markdown_store.write_family(make_family(family_id="fam_neg"))
        for s in ("-1.5", "0.5", "-2.3"):
            markdown_store.append_history("fam_neg", f"Iteration: ...\nScore: {s}")
        # Add 3 recent to trigger windowing
        for i in range(3):
            markdown_store.append_history("fam_neg", f"Recent {i}")

        flow = FamilyFlow(markdown_store, artifact_store)
        result = flow._get_history_context("fam_neg")
        assert "-1.5" in result[0]
        assert "-2.3" in result[0]


def test_promotion_on_qualified_regardless_of_verdict(
    markdown_store: MarkdownStore,
    artifact_store: ArtifactStore,
    monkeypatch,
) -> None:
    """Qualified improvement should promote to holdout even when tier-3 verdict is REVISE."""
    family = make_family(
        family_id="fam_promo",
        seed_id="seed_promo",
        state=FamilyState.QUEUED,
        current_iteration=0,
    )
    markdown_store.write_family(family)
    _write_seed(markdown_store, "seed_promo")

    flow = FamilyFlow(markdown_store, artifact_store)
    monkeypatch.setattr(flow.researcher, "draft_plan", lambda *args, **kwargs: "drafted plan")
    monkeypatch.setattr(
        flow.researcher, "write_code",
        lambda *args, **kwargs: {
            "features.py": "def compute_features(bars):\n    return bars\n",
            "model_config.py": "MODEL_CONFIG = {}\n",
            "signal_combiner.py": "def combine_signals(features, config):\n    return features['close'] * 0\n",
            "labels.py": "def compute_labels(bars):\n    return bars['close']\n",
        },
    )

    def fake_run_tier(tier: int, context: dict, client=None) -> list[JudgeOutput]:
        if tier == 1:
            return [_approved_output("leakage")]
        if tier == 2:
            return [_approved_output("code")]
        if tier == 3:
            # Tier-3 returns REVISE — should NOT block promotion
            return [JudgeOutput(judge_type="result", verdict=Verdict.REVISE,
                                must_fix=["improve sub-period stability"])]
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

    iteration = flow.run_iteration("fam_promo")
    updated_family = markdown_store.read_family("fam_promo")

    # Should promote despite REVISE verdict
    assert updated_family.state == FamilyState.PROMOTE_TO_HOLDOUT
    assert iteration.stage.value == "ITERATION_SUCCESS"


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
