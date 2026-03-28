"""Tests for alpha_forge.app.domain.models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from alpha_forge.app.domain.models import (
    CompositeScore,
    IdeaFamily,
    JudgeOutput,
    LeakageJudgeOutput,
    MutationBudget,
    OverfitJudgeOutput,
    RealismJudgeOutput,
    CodeJudgeOutput,
    ResultJudgeOutput,
    MutationJudgeOutput,
    RobustnessResult,
    RobustnessTest,
)
from alpha_forge.app.domain.states import MutationCategory
from tests.conftest import make_family, make_robustness


class TestIdeaFamilyValidator:
    """IdeaFamily model_validator rejects red_strike_count > strike_count."""

    def test_rejects_red_exceeds_total(self) -> None:
        with pytest.raises(ValidationError, match="red_strike_count cannot exceed"):
            make_family(strike_count=1, red_strike_count=2)

    def test_accepts_equal_counts(self) -> None:
        family = make_family(strike_count=2, red_strike_count=2)
        assert family.red_strike_count == 2

    def test_accepts_red_less_than_total(self) -> None:
        family = make_family(strike_count=3, red_strike_count=1)
        assert family.red_strike_count == 1


class TestMutationBudgetRemaining:
    """MutationBudget.remaining() returns correct values per category."""

    def test_default_budgets(self) -> None:
        budget = MutationBudget()
        assert budget.remaining(MutationCategory.HORIZON) == 2
        assert budget.remaining(MutationCategory.VENUE) == 2
        assert budget.remaining(MutationCategory.REPRESENTATION) == 1
        assert budget.remaining(MutationCategory.COMBINATION) == 1
        assert budget.remaining(MutationCategory.REGIME) == 1
        assert budget.remaining(MutationCategory.STRUCTURAL) == 0


class TestMutationBudgetConsume:
    """MutationBudget.consume() decrements and raises on zero."""

    def test_consume_decrements(self) -> None:
        budget = MutationBudget()
        new_budget = budget.consume(MutationCategory.HORIZON)
        assert new_budget.remaining(MutationCategory.HORIZON) == 1
        assert budget.remaining(MutationCategory.HORIZON) == 2  # original unchanged

    def test_consume_raises_on_zero(self) -> None:
        budget = MutationBudget()
        with pytest.raises(ValueError, match="No budget remaining"):
            budget.consume(MutationCategory.STRUCTURAL)

    def test_consume_twice_to_zero(self) -> None:
        budget = MutationBudget()
        b1 = budget.consume(MutationCategory.HORIZON)
        b2 = b1.consume(MutationCategory.HORIZON)
        assert b2.remaining(MutationCategory.HORIZON) == 0
        with pytest.raises(ValueError):
            b2.consume(MutationCategory.HORIZON)


class TestCompositeScoreTotal:
    """CompositeScore.total property tests."""

    def test_total_with_all_zeros(self) -> None:
        score = CompositeScore()
        assert score.total == 0.0

    def test_total_computation(self) -> None:
        score = CompositeScore(
            alpha_quality=1.0,
            stability_bonus=0.2,
            turnover_penalty=0.1,
            drawdown_penalty=0.05,
            concentration_penalty=0.03,
            fragility_penalty=0.02,
        )
        expected = 1.0 + 0.2 - 0.1 - 0.05 - 0.03 - 0.02
        assert abs(score.total - expected) < 1e-10

    def test_total_negative_when_penalties_dominate(self) -> None:
        score = CompositeScore(
            alpha_quality=0.1,
            turnover_penalty=0.5,
            drawdown_penalty=0.5,
        )
        assert score.total < 0


class TestRobustnessResult:
    """RobustnessResult.all_passed and pass_rate tests."""

    def test_all_passed_true(self) -> None:
        result = make_robustness([("t1", True, 0.01), ("t2", True, 0.02)])
        assert result.all_passed is True

    def test_all_passed_false_when_any_fail(self) -> None:
        result = make_robustness([("t1", True, 0.01), ("t2", False, 0.5)])
        assert result.all_passed is False

    def test_pass_rate_all_pass(self) -> None:
        result = make_robustness([("t1", True, 0.0), ("t2", True, 0.0)])
        assert result.pass_rate == 1.0

    def test_pass_rate_half(self) -> None:
        result = make_robustness([("t1", True, 0.0), ("t2", False, 0.5)])
        assert result.pass_rate == 0.5

    def test_pass_rate_empty(self) -> None:
        result = RobustnessResult(tests=[])
        assert result.pass_rate == 0.0

    def test_all_passed_empty(self) -> None:
        result = RobustnessResult(tests=[])
        assert result.all_passed is True  # all() on empty is True


class TestJudgeOutputExtras:
    """JudgeOutput accepts extra fields via model_config extra='allow'."""

    def test_extra_fields_accepted(self) -> None:
        output = JudgeOutput(judge_type="test", custom_field="hello", score=42)
        assert output.custom_field == "hello"  # type: ignore[attr-defined]
        assert output.score == 42  # type: ignore[attr-defined]


class TestJudgeSubclassDefaults:
    """Judge subclass defaults for judge_type."""

    def test_leakage_default(self) -> None:
        assert LeakageJudgeOutput().judge_type == "leakage"

    def test_overfit_default(self) -> None:
        assert OverfitJudgeOutput().judge_type == "overfit"

    def test_realism_default(self) -> None:
        assert RealismJudgeOutput().judge_type == "realism"

    def test_code_default(self) -> None:
        assert CodeJudgeOutput().judge_type == "code"

    def test_result_default(self) -> None:
        assert ResultJudgeOutput().judge_type == "result"

    def test_mutation_default(self) -> None:
        assert MutationJudgeOutput().judge_type == "mutation"
