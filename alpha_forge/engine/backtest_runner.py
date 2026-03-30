"""Backtest runner: thin wrapper over crypto-pegasus BacktestEngine.

Reads configs, constructs ResearchStrategy from a family's research/ dir,
runs backtests via pegasus, and returns summarized results.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from alpha_forge.app.domain.models import BacktestResultSummary
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.engine.research_strategy import ResearchStrategy

from pegasus.config import BacktestConfig
from pegasus.engine.backtest import BacktestEngine
from pegasus.metrics.report import compute_metrics
from pegasus.viz.plots import save_report

logger = logging.getLogger(__name__)


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def _build_config(
    configs_dir: Path,
    split_name: str = "validation",
) -> tuple[BacktestConfig, str, str, list[str]]:
    """Build BacktestConfig from config files.

    Returns (config, start_date, end_date, symbols).
    """
    costs = _load_yaml(configs_dir / "costs.yaml")
    splits = _load_yaml(configs_dir / "splits.yaml")
    universe = _load_yaml(configs_dir / "universe.yaml")

    split = splits[split_name]

    config = BacktestConfig(
        symbols=universe["symbols"],
        timeframe=universe.get("default_timeframe", "5min"),
        initial_capital=costs.get("initial_capital", 100_000.0),
        fee_rate=costs.get("fee_rate", 0.001),
        slippage_bps=costs.get("slippage_bps", 5.0),
    )

    return config, split["start"], split["end"], universe["symbols"]


def run_backtest(
    family_id: str,
    store: MarkdownStore,
    configs_dir: str | Path = "configs",
    split_name: str = "validation",
    fee_rate_override: float | None = None,
    slippage_override: float | None = None,
    symbols_override: list[str] | None = None,
    timeframe_override: str | None = None,
    start_override: str | None = None,
    end_override: str | None = None,
    save_html: bool = True,
) -> list[BacktestResultSummary]:
    """Run a backtest for a family on the specified split.

    Returns a list of BacktestResultSummary, one per symbol.
    """
    configs_path = Path(configs_dir).resolve()
    config, start, end, symbols = _build_config(configs_path, split_name)

    if fee_rate_override is not None:
        config = config.model_copy(update={"fee_rate": fee_rate_override})
    if slippage_override is not None:
        config = config.model_copy(update={"slippage_bps": slippage_override})
    if symbols_override is not None:
        symbols = symbols_override
    if timeframe_override is not None:
        config = config.model_copy(update={"timeframe": timeframe_override})
    if start_override is not None:
        start = start_override
    if end_override is not None:
        end = end_override

    # Build strategy from family's research/ dir
    research_dir = store.root / "families" / family_id / "research"
    strategy = ResearchStrategy(research_dir)

    # Read timeframe: model_config > family horizon > universe default
    if timeframe_override is None:
        resolved_tf = None
        try:
            resolved_tf = strategy._config_mod.MODEL_CONFIG.get("timeframe")
            if resolved_tf:
                logger.info("Using timeframe from model_config: %s", resolved_tf)
        except Exception:
            pass
        # Fallback: read from family's allowed_mutations.horizon
        if not resolved_tf:
            try:
                family = store.read_family(family_id)
                horizons = family.mutation_budget_horizons if hasattr(family, "mutation_budget_horizons") else None
                if not horizons and hasattr(family, "allowed_mutations"):
                    horizons = family.allowed_mutations.get("horizon", []) if isinstance(family.allowed_mutations, dict) else None
                # Try reading raw FAMILY.md allowed_mutations
                if not horizons:
                    import yaml
                    family_path = store.root / "families" / family_id / "FAMILY.md"
                    if family_path.exists():
                        text = family_path.read_text()
                        if "---" in text:
                            front = text.split("---")[1]
                            meta = yaml.safe_load(front)
                            am = meta.get("allowed_mutations", {})
                            horizons = am.get("horizon", [])
                if horizons and len(horizons) == 1:
                    resolved_tf = horizons[0]
                    logger.info("Using timeframe from family horizon: %s", resolved_tf)
            except Exception:
                pass
        if resolved_tf:
            config = config.model_copy(update={"timeframe": resolved_tf})

    # Read risk management params from MODEL_CONFIG
    try:
        mc = strategy._config_mod.MODEL_CONFIG
        risk_updates = {}
        for key in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct"):
            val = mc.get(key)
            if val is not None:
                risk_updates[key] = float(val)
                logger.info("Risk param from model_config: %s=%s", key, val)
        if risk_updates:
            config = config.model_copy(update=risk_updates)
    except Exception:
        pass

    engine = BacktestEngine(strategy=strategy, config=config)
    results: list[BacktestResultSummary] = []

    for symbol in symbols:
        logger.info("Running backtest: %s / %s / %s-%s", family_id, symbol, start, end)
        bt_result = engine.run(symbol, start, end, config.timeframe)
        bt_result.metrics = compute_metrics(bt_result)

        # Save HTML report per symbol
        if save_html:
            try:
                reports_dir = store.root / "reports" / family_id
                reports_dir.mkdir(parents=True, exist_ok=True)
                report_path = reports_dir / f"{symbol}_{config.timeframe}.html"
                save_report(bt_result, report_path)
                logger.info("Saved report: %s", report_path)
            except Exception as e:
                logger.warning("Failed to save HTML report for %s: %s", symbol, e)

        summary = BacktestResultSummary(
            symbol=symbol,
            timeframe=config.timeframe,
            sharpe=bt_result.metrics.get("sharpe", 0.0),
            sortino=bt_result.metrics.get("sortino", 0.0),
            max_drawdown=bt_result.metrics.get("max_drawdown", 0.0),
            total_return=bt_result.metrics.get("total_return", 0.0),
            calmar=bt_result.metrics.get("calmar", 0.0),
            win_rate=bt_result.metrics.get("win_rate", 0.0),
            profit_factor=bt_result.metrics.get("profit_factor", 0.0),
            total_trades=bt_result.metrics.get("total_trades", 0),
            exposure_pct=bt_result.metrics.get("exposure_pct", 0.0),
            all_metrics=bt_result.metrics,
        )
        results.append(summary)

    return results
