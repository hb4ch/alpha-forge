"""Tests for alpha_forge.app.domain.scoring."""

from __future__ import annotations

import pytest

from alpha_forge.app.domain.models import CompositeScore
from alpha_forge.app.domain.scoring import compute_composite_score, is_qualified_improvement
from tests.conftest import make_result, make_robustness


class TestComputeCompositeScoreEmpty:
    """compute_composite_score([]) returns zero CompositeScore."""

    def test_empty_results(self) -> None:
        score = compute_composite_score([])
        assert score.alpha_quality == 0.0
        assert score.stability_bonus == 0.0
        assert score.turnover_penalty == 0.0
        assert score.drawdown_penalty == 0.0
        assert score.concentration_penalty == 0.0
        assert score.fragility_penalty == 0.0
        assert score.total == 0.0


class TestComputeCompositeScoreSingleSymbol:
    """Single symbol: stability_bonus=0, concentration_penalty=0."""

    def test_single_symbol_no_stability_or_concentration(self) -> None:
        result = make_result(sharpe=1.0, sortino=2.0, total_return=0.5)
        score = compute_composite_score([result])
        assert score.stability_bonus == 0.0
        assert score.concentration_penalty == 0.0


class TestAlphaQuality:
    """Alpha quality = 0.5*avg_sharpe + 0.3*avg_sortino + 0.2*avg_return."""

    def test_exact_alpha_quality(self) -> None:
        r1 = make_result(sharpe=2.0, sortino=3.0, total_return=1.0, symbol="A")
        r2 = make_result(sharpe=1.0, sortino=1.0, total_return=0.5, symbol="B")
        score = compute_composite_score([r1, r2])
        avg_sharpe = (2.0 + 1.0) / 2
        avg_sortino = (3.0 + 1.0) / 2
        avg_return = (1.0 + 0.5) / 2
        expected = 0.5 * avg_sharpe + 0.3 * avg_sortino + 0.2 * avg_return
        assert abs(score.alpha_quality - expected) < 1e-10


class TestTurnoverPenalty:
    """Turnover penalty: 0 when trades < 500, positive when > 500."""

    def test_no_penalty_below_threshold(self) -> None:
        result = make_result(total_trades=200)
        score = compute_composite_score([result])
        assert score.turnover_penalty == 0.0

    def test_no_penalty_at_threshold(self) -> None:
        result = make_result(total_trades=500)
        score = compute_composite_score([result])
        assert score.turnover_penalty == 0.0

    def test_positive_penalty_above_threshold(self) -> None:
        result = make_result(total_trades=1000)
        score = compute_composite_score([result])
        expected = max(0.0, (1000 - 500) / 500 * 0.1)
        assert abs(score.turnover_penalty - expected) < 1e-10
        assert score.turnover_penalty > 0


class TestDrawdownPenalty:
    """Drawdown penalty: 0 when abs(max_drawdown) < 0.3, positive when > 0.3."""

    def test_no_penalty_below_threshold(self) -> None:
        result = make_result(max_drawdown=-0.15)
        score = compute_composite_score([result])
        assert score.drawdown_penalty == 0.0

    def test_no_penalty_at_threshold(self) -> None:
        result = make_result(max_drawdown=-0.3)
        score = compute_composite_score([result])
        assert score.drawdown_penalty == 0.0

    def test_positive_penalty_above_threshold(self) -> None:
        result = make_result(max_drawdown=-0.6)
        score = compute_composite_score([result])
        expected = max(0.0, (0.6 - 0.3) / 0.3 * 0.2)
        assert abs(score.drawdown_penalty - expected) < 1e-10
        assert score.drawdown_penalty > 0


class TestConcentrationPenalty:
    """Concentration penalty: 0 when balanced, positive when one dominates."""

    def test_no_penalty_when_balanced(self) -> None:
        r1 = make_result(total_return=0.25, symbol="A")
        r2 = make_result(total_return=0.25, symbol="B")
        score = compute_composite_score([r1, r2])
        assert score.concentration_penalty == 0.0

    def test_positive_penalty_when_concentrated(self) -> None:
        r1 = make_result(total_return=0.9, symbol="A")
        r2 = make_result(total_return=0.1, symbol="B")
        score = compute_composite_score([r1, r2])
        total_abs = 0.9 + 0.1
        max_share = 0.9 / total_abs
        expected = max(0.0, (max_share - 0.5) * 0.2)
        assert abs(score.concentration_penalty - expected) < 1e-10
        assert score.concentration_penalty > 0

    def test_no_penalty_single_symbol(self) -> None:
        result = make_result(total_return=1.0)
        score = compute_composite_score([result])
        assert score.concentration_penalty == 0.0


class TestFragilityPenalty:
    """Fragility penalty: 0 when no robustness, fail_rate * 0.3 otherwise."""

    def test_no_penalty_without_robustness(self) -> None:
        result = make_result()
        score = compute_composite_score([result], robustness=None)
        assert score.fragility_penalty == 0.0

    def test_no_penalty_all_pass(self) -> None:
        result = make_result()
        rob = make_robustness([("t1", True, 0.01), ("t2", True, 0.02)])
        score = compute_composite_score([result], robustness=rob)
        assert score.fragility_penalty == 0.0

    def test_penalty_with_failures(self) -> None:
        result = make_result()
        rob = make_robustness([("t1", True, 0.01), ("t2", False, 0.5)])
        score = compute_composite_score([result], robustness=rob)
        fail_rate = 0.5
        expected = fail_rate * 0.3
        assert abs(score.fragility_penalty - expected) < 1e-10


class TestIsQualifiedImprovement:
    """is_qualified_improvement tests."""

    def test_true_when_above_threshold_and_robust(self) -> None:
        score = CompositeScore(alpha_quality=1.0)
        rob = make_robustness([("t1", True, 0.01)])
        assert is_qualified_improvement(score, 0.0, rob) is True

    def test_false_when_below_threshold(self) -> None:
        score = CompositeScore(alpha_quality=0.54)
        rob = make_robustness([("t1", True, 0.01)])
        assert is_qualified_improvement(score, 0.5, rob) is False

    def test_false_when_robustness_fails(self) -> None:
        score = CompositeScore(alpha_quality=1.0)
        rob = make_robustness([("t1", False, 0.5)])
        assert is_qualified_improvement(score, 0.0, rob) is False

    def test_false_when_score_equals_best(self) -> None:
        score = CompositeScore(alpha_quality=0.5)
        assert is_qualified_improvement(score, 0.5) is False

    def test_false_when_score_less_than_best(self) -> None:
        score = CompositeScore(alpha_quality=0.3)
        assert is_qualified_improvement(score, 0.5) is False

    def test_true_without_robustness(self) -> None:
        score = CompositeScore(alpha_quality=1.0)
        assert is_qualified_improvement(score, 0.0, robustness=None) is True

    def test_false_when_core_test_fails(self) -> None:
        """core_tests_passed=False should block qualification."""
        score = CompositeScore(alpha_quality=1.0)
        rob = make_robustness([
            ("cost_2.0x", False, 0.5),  # Core test fails
            ("cost_3.0x", True, 0.1),
            ("slippage_2.0x", True, 0.03),
        ])
        assert rob.core_tests_passed is False
        assert is_qualified_improvement(score, 0.0, rob) is False

    def test_false_when_pass_rate_below_threshold(self) -> None:
        """Core tests pass but pass_rate < 0.7 should block."""
        score = CompositeScore(alpha_quality=1.0)
        rob = make_robustness([
            ("cost_2.0x", True, 0.05),
            ("cost_3.0x", False, 0.5),
            ("slippage_2.0x", True, 0.03),
            ("slippage_3.0x", False, 0.5),
            ("sub_period_stability", False, 0.5),
        ])
        assert rob.core_tests_passed is True
        assert rob.pass_rate < 0.7
        assert is_qualified_improvement(score, 0.0, rob) is False

    def test_true_when_core_pass_and_rate_above_threshold(self) -> None:
        """Core tests pass and pass_rate >= 0.7 should qualify."""
        score = CompositeScore(alpha_quality=1.0)
        rob = make_robustness([
            ("cost_2.0x", True, 0.05),
            ("cost_3.0x", True, 0.12),
            ("slippage_2.0x", True, 0.03),
            ("sub_period_stability", False, 0.5),  # 1 failure out of 4 → 75% pass
        ])
        assert rob.core_tests_passed is True
        assert rob.pass_rate >= 0.7
        assert is_qualified_improvement(score, 0.0, rob) is True
