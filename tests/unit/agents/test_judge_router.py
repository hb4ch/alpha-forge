"""Tests for aggregate_verdict and judge routing behavior."""

import pytest

from alpha_forge.app.agents import judge_router
from alpha_forge.app.agents.judge_code import CodeJudge
from alpha_forge.app.agents.judge_leakage import LeakageJudge
from alpha_forge.app.agents.judge_result import ResultJudge
from alpha_forge.app.agents.judge_router import aggregate_verdict
from alpha_forge.app.domain.models import JudgeOutput
from alpha_forge.app.domain.states import Verdict


def _out(verdict: Verdict) -> JudgeOutput:
    return JudgeOutput(verdict=verdict)


class TestAggregateVerdict:
    """Tests for the priority-based verdict aggregation logic."""

    def test_all_approve(self):
        outputs = [_out(Verdict.APPROVE), _out(Verdict.APPROVE)]
        assert aggregate_verdict(outputs) == Verdict.APPROVE

    def test_reject_clamped_to_revise(self):
        outputs = [_out(Verdict.APPROVE), _out(Verdict.REJECT)]
        assert aggregate_verdict(outputs) == Verdict.REVISE

    def test_fork_required_clamped_to_revise(self):
        outputs = [
            _out(Verdict.FORK_REQUIRED),
            _out(Verdict.APPROVE),
            _out(Verdict.APPROVE),
        ]
        assert aggregate_verdict(outputs) == Verdict.REVISE

    def test_revise_mixed_with_approve(self):
        outputs = [_out(Verdict.REVISE), _out(Verdict.APPROVE)]
        assert aggregate_verdict(outputs) == Verdict.REVISE

    def test_approve_with_constraints_mixed_with_approve(self):
        outputs = [_out(Verdict.APPROVE_WITH_CONSTRAINTS), _out(Verdict.APPROVE)]
        assert aggregate_verdict(outputs) == Verdict.APPROVE_WITH_CONSTRAINTS

    def test_empty_list(self):
        assert aggregate_verdict([]) == Verdict.APPROVE

    def test_single_reject_clamped(self):
        outputs = [_out(Verdict.REJECT)]
        assert aggregate_verdict(outputs) == Verdict.REVISE

    def test_all_revise(self):
        outputs = [_out(Verdict.REVISE), _out(Verdict.REVISE)]
        assert aggregate_verdict(outputs) == Verdict.REVISE

    def test_approve_with_constraints_and_approve_only(self):
        outputs = [
            _out(Verdict.APPROVE_WITH_CONSTRAINTS),
            _out(Verdict.APPROVE),
            _out(Verdict.APPROVE),
        ]
        assert aggregate_verdict(outputs) == Verdict.APPROVE_WITH_CONSTRAINTS


class TestAggregateVerdictClamping:
    """Tests that REJECT/FORK_REQUIRED are clamped to REVISE."""

    def test_multiple_rejects_clamped(self):
        outputs = [_out(Verdict.REJECT), _out(Verdict.REJECT), _out(Verdict.APPROVE)]
        assert aggregate_verdict(outputs) == Verdict.REVISE

    def test_fork_and_reject_both_clamped(self):
        outputs = [_out(Verdict.FORK_REQUIRED), _out(Verdict.REJECT), _out(Verdict.APPROVE)]
        assert aggregate_verdict(outputs) == Verdict.REVISE

    def test_revise_propagates(self):
        outputs = [_out(Verdict.REVISE), _out(Verdict.APPROVE), _out(Verdict.APPROVE)]
        assert aggregate_verdict(outputs) == Verdict.REVISE

    def test_approve_with_constraints_propagates(self):
        outputs = [_out(Verdict.APPROVE_WITH_CONSTRAINTS), _out(Verdict.APPROVE)]
        assert aggregate_verdict(outputs) == Verdict.APPROVE_WITH_CONSTRAINTS

    def test_all_approve(self):
        outputs = [_out(Verdict.APPROVE), _out(Verdict.APPROVE), _out(Verdict.APPROVE)]
        assert aggregate_verdict(outputs) == Verdict.APPROVE


def test_base_judge_uses_role_specific_client_mapping(monkeypatch) -> None:
    requested_roles: list[str] = []

    def fake_get_client_for_role(role: str):
        requested_roles.append(role)
        return object()

    monkeypatch.setattr("alpha_forge.app.agents.llm_config.get_client_for_role", fake_get_client_for_role)

    LeakageJudge()
    CodeJudge()
    ResultJudge()

    assert requested_roles == ["leakage_judge", "code_judge", "result_judge"]


def test_run_tier_uses_independent_judge_instances_when_client_missing(monkeypatch) -> None:
    created: list[tuple[str, object | None]] = []

    class FakeLeakageJudge:
        judge_type = "leakage"

        def __init__(self, client=None) -> None:
            created.append(("leakage", client))

        def evaluate_leakage(self, **kwargs) -> JudgeOutput:
            return JudgeOutput(judge_type="leakage", verdict=Verdict.APPROVE)

    class FakeCodeJudge:
        judge_type = "code"

        def __init__(self, client=None) -> None:
            created.append(("code", client))

        def evaluate_code(self, **kwargs) -> JudgeOutput:
            return JudgeOutput(judge_type="code", verdict=Verdict.APPROVE)

    monkeypatch.setattr(judge_router, "LeakageJudge", FakeLeakageJudge)
    monkeypatch.setattr(judge_router, "CodeJudge", FakeCodeJudge)

    outputs = judge_router.run_tier(
        2,
        {
            "plan": "plan",
            "code": "code",
            "diff": "diff",
            "history": ["history"],
            "changed_files": ["features.py"],
            "allowed_files": ["features.py"],
            "forbidden_files": ["configs/*"],
        },
        client=None,
        max_workers=1,
    )

    assert created == [("leakage", None), ("code", None)]
    assert {output.judge_type for output in outputs} == {"leakage", "code"}
