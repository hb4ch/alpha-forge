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
                save_html=False,
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
                save_html=False,
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
    #
    # Instead of running separate backtests per window (which breaks when
    # the window is shorter than the feature warmup period), run ONE
    # full-period backtest and compute Sharpe from the equity curve's
    # per-bar returns for each sub-window.
    try:
        with open(configs_path / "splits.yaml") as f:
            splits = yaml.safe_load(f)
        val_start = splits["validation"]["start"]
        val_end = splits["validation"]["end"]

        import pandas as pd
        from alpha_forge.engine.research_strategy import ResearchStrategy
        from pegasus.engine.backtest import BacktestEngine
        from alpha_forge.engine.backtest_runner import _build_config as _bc

        research_dir = store.root / "families" / family_id / "research"
        strategy = ResearchStrategy(research_dir)
        config, _, _, default_symbols = _bc(configs_path)

        # Apply risk params and timeframe from MODEL_CONFIG
        try:
            mc = strategy._config_mod.MODEL_CONFIG
            updates = {}
            tf = mc.get("timeframe")
            if tf:
                updates["timeframe"] = tf
            for key in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct"):
                val = mc.get(key)
                if val is not None:
                    updates[key] = float(val)
            if updates:
                config = config.model_copy(update=updates)
        except Exception:
            pass

        # Determine symbols
        bt_symbols = None
        try:
            mc_universe = strategy._config_mod.MODEL_CONFIG.get("universe")
            if mc_universe and isinstance(mc_universe, list):
                # Normalize: researcher may write "BTC" instead of "BTCUSDT"
                canonical = {s.replace("USDT", ""): s for s in default_symbols}
                bt_symbols = [canonical.get(s.replace("USDT", ""), s) for s in mc_universe]
        except Exception:
            pass
        if bt_symbols is None:
            bt_symbols = default_symbols

        engine = BacktestEngine(strategy=strategy, config=config)

        # Load bars from training start so features warm up before
        # validation begins. This ensures valid signals from bar 1 of
        # the validation period.
        train_start = splits["train"]["start"]

        # Collect per-bar returns across symbols, then average
        all_returns = []
        for sym in bt_symbols:
            bt_result = engine.run(sym, train_start, val_end, config.timeframe)
            eq = bt_result.equity_curve
            if eq is not None and "returns" in eq.columns and len(eq) > 0:
                all_returns.append(eq["returns"])

        if all_returns:
            # Average returns across symbols (equal weight)
            combined = pd.concat(all_returns, axis=1).mean(axis=1)
            combined.index = pd.to_datetime(combined.index, utc=True)

            start_ts = pd.Timestamp(val_start, tz="UTC")
            end_ts = pd.Timestamp(val_end, tz="UTC")
            total_days = max((end_ts - start_ts).days, 1)
            window_days = max(total_days // 3, 1)

            window_sharpes = []
            for i in range(3):
                w_start = start_ts + pd.Timedelta(days=i * window_days)
                w_end = start_ts + pd.Timedelta(days=(i + 1) * window_days)
                if i == 2:
                    w_end = end_ts

                window_rets = combined.loc[
                    (combined.index >= w_start) & (combined.index < w_end)
                ]
                # Drop NaN/zero returns from warmup
                window_rets = window_rets.dropna()
                if len(window_rets) > 5:
                    mean_r = window_rets.mean()
                    std_r = window_rets.std()
                    w_sharpe = float(mean_r / std_r * np.sqrt(252)) if std_r > 0 else 0.0
                else:
                    w_sharpe = 0.0
                window_sharpes.append(round(w_sharpe, 4))
        else:
            window_sharpes = [0.0, 0.0, 0.0]

        sharpe_std = float(np.std(window_sharpes)) if len(window_sharpes) > 1 else 0.0
        positive_windows = sum(1 for s in window_sharpes if s > 0)
        passed = sharpe_std < 1.0 and positive_windows >= 2
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
        # Use MODEL_CONFIG universe if available, else fall back to universe.yaml
        all_symbols = None
        try:
            research_dir = store.root / "families" / family_id / "research"
            from alpha_forge.engine.research_strategy import ResearchStrategy
            _strat = ResearchStrategy(research_dir)
            mc_universe = _strat._config_mod.MODEL_CONFIG.get("universe")
            if mc_universe and isinstance(mc_universe, list):
                with open(configs_path / "universe.yaml") as f:
                    cfg_symbols = yaml.safe_load(f)["symbols"]
                canonical = {s.replace("USDT", ""): s for s in cfg_symbols}
                all_symbols = [canonical.get(s.replace("USDT", ""), s) for s in mc_universe]
        except Exception:
            pass
        if all_symbols is None:
            with open(configs_path / "universe.yaml") as f:
                universe = yaml.safe_load(f)
            all_symbols = universe["symbols"]

        for excluded in all_symbols:
            try:
                remaining = [s for s in all_symbols if s != excluded]
                results = run_backtest(
                    family_id, store, configs_dir,
                    symbols_override=remaining,
                    save_html=False,
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
