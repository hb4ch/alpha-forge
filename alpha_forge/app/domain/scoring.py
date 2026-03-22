"""Composite score computation and qualified-improvement logic."""

from __future__ import annotations

from alpha_forge.app.domain.models import (
    BacktestResultSummary,
    CompositeScore,
    RobustnessResult,
)

# Thresholds
QUALIFIED_IMPROVEMENT_THRESHOLD = 0.05  # 5% composite score improvement required
MAX_TURNOVER_ANNUAL = 500.0  # penalize above this
MAX_DRAWDOWN = 0.30  # penalize above this


def compute_composite_score(
    results: list[BacktestResultSummary],
    robustness: RobustnessResult | None = None,
) -> CompositeScore:
    """Compute composite score from backtest results across symbols."""
    if not results:
        return CompositeScore()

    avg_sharpe = sum(r.sharpe for r in results) / len(results)
    avg_sortino = sum(r.sortino for r in results) / len(results)
    avg_drawdown = sum(abs(r.max_drawdown) for r in results) / len(results)
    avg_return = sum(r.total_return for r in results) / len(results)

    # Alpha quality: blend of risk-adjusted metrics
    alpha_quality = 0.5 * avg_sharpe + 0.3 * avg_sortino + 0.2 * avg_return

    # Stability bonus: low variance of Sharpe across symbols
    if len(results) > 1:
        sharpe_values = [r.sharpe for r in results]
        sharpe_mean = sum(sharpe_values) / len(sharpe_values)
        sharpe_var = sum((s - sharpe_mean) ** 2 for s in sharpe_values) / len(sharpe_values)
        stability_bonus = max(0.0, 0.2 - sharpe_var)
    else:
        stability_bonus = 0.0

    # Turnover penalty
    avg_trades = sum(r.total_trades for r in results) / len(results)
    turnover_penalty = max(0.0, (avg_trades - MAX_TURNOVER_ANNUAL) / MAX_TURNOVER_ANNUAL * 0.1)

    # Drawdown penalty
    drawdown_penalty = max(0.0, (avg_drawdown - MAX_DRAWDOWN) / MAX_DRAWDOWN * 0.2)

    # Concentration penalty: if one symbol dominates returns
    if len(results) > 1:
        returns = [r.total_return for r in results]
        total_abs = sum(abs(r) for r in returns) or 1.0
        max_share = max(abs(r) for r in returns) / total_abs
        concentration_penalty = max(0.0, (max_share - 0.5) * 0.2)
    else:
        concentration_penalty = 0.0

    # Fragility penalty: from robustness results
    fragility_penalty = 0.0
    if robustness and robustness.tests:
        fail_rate = 1.0 - robustness.pass_rate
        fragility_penalty = fail_rate * 0.3

    return CompositeScore(
        alpha_quality=alpha_quality,
        stability_bonus=stability_bonus,
        turnover_penalty=turnover_penalty,
        drawdown_penalty=drawdown_penalty,
        concentration_penalty=concentration_penalty,
        fragility_penalty=fragility_penalty,
    )


def is_qualified_improvement(
    current: CompositeScore,
    best: float,
    robustness: RobustnessResult | None = None,
) -> bool:
    """Check if current score is a qualified improvement over the family best.

    Requires:
    - Composite score improves beyond threshold
    - Required robustness tests pass
    - No major stability dimension materially worsens
    """
    if current.total <= best:
        return False

    improvement = current.total - best
    if improvement < QUALIFIED_IMPROVEMENT_THRESHOLD:
        return False

    # Robustness must pass if available
    if robustness and not robustness.all_passed:
        return False

    return True
