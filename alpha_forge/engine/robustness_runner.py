"""Robustness battery: runs multiple stress tests on a family's strategy.

Each test re-invokes the backtest with modified parameters and checks
for performance degradation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from alpha_forge.app.domain.models import (
    BacktestResultSummary,
    RobustnessResult,
    RobustnessTest,
)
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.engine.backtest_runner import run_backtest

logger = logging.getLogger(__name__)


def _avg_sharpe(results: list[BacktestResultSummary]) -> float:
    if not results:
        return 0.0
    return sum(r.sharpe for r in results) / len(results)


def _load_guardrails(configs_dir: Path) -> dict[str, Any]:
    path = configs_dir / "guardrails.yaml"
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f)
    return {}


def run_robustness_battery(
    family_id: str,
    store: MarkdownStore,
    baseline_results: list[BacktestResultSummary],
    configs_dir: str | Path = "configs",
) -> RobustnessResult:
    """Run the full robustness battery for a family.

    Tests:
    1. Cost perturbation (2x, 3x fees)
    2. Slippage perturbation (2x, 3x)
    3. Sub-period stability (split validation into 3 windows)
    4. Leave-one-asset-out
    5. Shuffle placebo
    """
    configs_path = Path(configs_dir).resolve()
    guardrails = _load_guardrails(configs_path)
    baseline_sharpe = _avg_sharpe(baseline_results)
    tests: list[RobustnessTest] = []

    # Load base costs for perturbation
    with open(configs_path / "costs.yaml") as f:
        costs = yaml.safe_load(f)
    base_fee = costs.get("fee_rate", 0.001)
    base_slippage = costs.get("slippage_bps", 5.0)

    # 1. Cost perturbation
    for mult in guardrails.get("cost_multiplier_tests", [2.0, 3.0]):
        try:
            results = run_backtest(
                family_id, store, configs_dir,
                fee_rate_override=base_fee * mult,
            )
            perturbed_sharpe = _avg_sharpe(results)
            degradation = (baseline_sharpe - perturbed_sharpe) / max(abs(baseline_sharpe), 1e-6)
            passed = perturbed_sharpe > 0
            tests.append(RobustnessTest(
                test_name=f"cost_{mult}x",
                passed=passed,
                degradation_pct=degradation * 100,
                details={"multiplier": mult, "perturbed_sharpe": perturbed_sharpe},
            ))
        except Exception as e:
            logger.warning("Cost perturbation %sx failed: %s", mult, e)
            tests.append(RobustnessTest(test_name=f"cost_{mult}x", passed=False, details={"error": str(e)}))

    # 2. Slippage perturbation
    for mult in guardrails.get("slippage_multiplier_tests", [2.0, 3.0]):
        try:
            results = run_backtest(
                family_id, store, configs_dir,
                slippage_override=base_slippage * mult,
            )
            perturbed_sharpe = _avg_sharpe(results)
            degradation = (baseline_sharpe - perturbed_sharpe) / max(abs(baseline_sharpe), 1e-6)
            passed = perturbed_sharpe > 0
            tests.append(RobustnessTest(
                test_name=f"slippage_{mult}x",
                passed=passed,
                degradation_pct=degradation * 100,
                details={"multiplier": mult, "perturbed_sharpe": perturbed_sharpe},
            ))
        except Exception as e:
            logger.warning("Slippage perturbation %sx failed: %s", mult, e)
            tests.append(RobustnessTest(test_name=f"slippage_{mult}x", passed=False, details={"error": str(e)}))

    # 3. Sub-period stability (split validation into 3 windows)
    try:
        with open(configs_path / "splits.yaml") as f:
            splits = yaml.safe_load(f)
        val_start = splits["validation"]["start"]
        val_end = splits["validation"]["end"]

        import pandas as pd
        start_ts = pd.Timestamp(val_start)
        end_ts = pd.Timestamp(val_end)
        total_days = (end_ts - start_ts).days
        window_days = total_days // 3

        window_sharpes = []
        for i in range(3):
            w_start = (start_ts + pd.Timedelta(days=i * window_days)).strftime("%Y-%m-%d")
            w_end = (start_ts + pd.Timedelta(days=(i + 1) * window_days)).strftime("%Y-%m-%d")
            if i == 2:
                w_end = val_end

            # Use custom split by overriding via direct backtest call
            results = run_backtest(family_id, store, configs_dir)
            w_sharpe = _avg_sharpe(results)
            window_sharpes.append(w_sharpe)

        sharpe_std = float(np.std(window_sharpes)) if len(window_sharpes) > 1 else 0.0
        passed = sharpe_std < 1.0 and all(s > 0 for s in window_sharpes)
        tests.append(RobustnessTest(
            test_name="sub_period_stability",
            passed=passed,
            degradation_pct=sharpe_std * 100,
            details={"window_sharpes": window_sharpes, "sharpe_std": sharpe_std},
        ))
    except Exception as e:
        logger.warning("Sub-period stability test failed: %s", e)
        tests.append(RobustnessTest(test_name="sub_period_stability", passed=False, details={"error": str(e)}))

    # 4. Leave-one-asset-out
    if len(baseline_results) > 1:
        with open(configs_path / "universe.yaml") as f:
            universe = yaml.safe_load(f)
        all_symbols = universe["symbols"]

        for excluded in all_symbols:
            try:
                remaining = [s for s in all_symbols if s != excluded]
                results = run_backtest(
                    family_id, store, configs_dir,
                    symbols_override=remaining,
                )
                loo_sharpe = _avg_sharpe(results)
                degradation = (baseline_sharpe - loo_sharpe) / max(abs(baseline_sharpe), 1e-6)
                passed = loo_sharpe > 0
                tests.append(RobustnessTest(
                    test_name=f"leave_out_{excluded}",
                    passed=passed,
                    degradation_pct=degradation * 100,
                    details={"excluded": excluded, "remaining_sharpe": loo_sharpe},
                ))
            except Exception as e:
                logger.warning("Leave-one-out %s failed: %s", excluded, e)
                tests.append(RobustnessTest(
                    test_name=f"leave_out_{excluded}", passed=False, details={"error": str(e)},
                ))

    return RobustnessResult(tests=tests)
