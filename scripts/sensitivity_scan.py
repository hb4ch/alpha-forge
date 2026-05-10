#!/usr/bin/env python3
"""One-at-a-time parameter sensitivity scan around the v6 baseline.

Varies atr_compression_threshold, abs_slope_percentile, and stop_atr_mult
independently, running the engine backtest on train/val/holdout at 20bps
for each value. Outputs JSON for analysis.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.engine.backtest_runner import run_backtest

BASELINE_CONFIG = {
    "timeframe": "4h",
    "universe": ["ETHUSDT"],
    "atr_compression_threshold": 0.35,
    "abs_slope_percentile": 0.80,
    "stop_atr_mult": 2.0,
    "min_conviction": 0.30,
    "max_conviction": 1.0,
    "stop_loss_pct": 0.05,
    "trailing_stop_pct": 0.30,
    "take_profit_pct": None,
}

SCANS = {
    "atr_compression_threshold": [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70],
    "abs_slope_percentile": [0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95],
    "stop_atr_mult": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0],
}

SPLITS = ("train", "validation", "holdout")
COST_BPS = 20


def write_model_config(path: Path, config: dict):
    lines = ["MODEL_CONFIG = {"]
    for k, v in config.items():
        if isinstance(v, str):
            lines.append(f'    "{k}": "{v}",')
        elif v is None:
            lines.append(f'    "{k}": None,')
        elif isinstance(v, list):
            lines.append(f'    "{k}": {v},')
        else:
            lines.append(f'    "{k}": {v},')
    lines.append("}")
    path.write_text("\n".join(lines) + "\n")


def run_scan(
    family_id: str,
    store: MarkdownStore,
    configs_dir: Path,
    v6_research: Path,
) -> list[dict[str, Any]]:
    sandbox = store.root / "families" / family_id
    sandbox.mkdir(parents=True, exist_ok=True)
    research = sandbox / "research"
    research.mkdir(exist_ok=True)

    # Copy base files from v6
    for fn in ("features.py", "labels.py", "signal_combiner.py"):
        shutil.copy2(v6_research / fn, research / fn)

    # Write FAMILY.md
    (sandbox / "FAMILY.md").write_text(
        "---\n"
        f"family_id: {family_id}\n"
        "seed_id: sensitivity_scan\n"
        "base_hypothesis: sensitivity scan\n"
        "mechanism: sensitivity scan\n"
        "state: QUEUED\n"
        "---\n"
    )

    fee_rate = COST_BPS / 1e4 / 2.0
    slip = COST_BPS / 2.0
    all_results: list[dict[str, Any]] = []

    for param_name, values in SCANS.items():
        for val in values:
            cfg = {**BASELINE_CONFIG, param_name: val}
            write_model_config(research / "model_config.py", cfg)

            row = {"param": param_name, "value": val, "splits": {}}
            for split in SPLITS:
                try:
                    results = run_backtest(
                        family_id, store,
                        configs_dir=configs_dir,
                        split_name=split,
                        fee_rate_override=fee_rate,
                        slippage_override=slip,
                        save_html=False,
                    )
                    if results:
                        m = results[0].all_metrics
                        row["splits"][split] = {
                            "sharpe": round(m.get("sharpe", 0.0), 4),
                            "total_return": round(m.get("total_return", 0.0), 4),
                            "max_drawdown": round(m.get("max_drawdown", 0.0), 4),
                            "total_trades": m.get("total_trades", 0),
                            "win_rate": round(m.get("win_rate", 0.0), 4),
                            "profit_factor": round(m.get("profit_factor", 0.0), 4),
                            "exposure_pct": round(m.get("exposure_pct", 0.0), 4),
                            "avg_trade_return": round(m.get("avg_trade_return", 0.0), 6),
                        }
                    else:
                        row["splits"][split] = None
                except Exception as e:
                    row["splits"][split] = {"error": str(e)}
            all_results.append(row)

            # Print progress
            summary = " | ".join(
                f"{s[:3]}={row['splits'][s]['sharpe']:+.2f}" if row["splits"].get(s) and "error" not in row["splits"][s] else f"{s[:3]}=ERR"
                for s in SPLITS
            )
            print(f"  {param_name}={val:.2f}  [{summary}]")

    return all_results


def print_summary(results: list[dict[str, Any]]):
    """Print a formatted summary table."""
    print("\n" + "=" * 90)
    print("PARAMETER SENSITIVITY SCAN — v6 baseline (4h, ETH-only, 20bps)")
    print("=" * 90)

    for param_name in SCANS:
        print(f"\n--- {param_name} ---")
        print(f"  {'Value':>6}  {'train':>7}  {'val':>7}  {'holdout':>7}  "
              f"{'trades(T)':>9}  {'trades(V)':>9}  {'trades(H)':>9}  "
              f"{'maxDD':>7}  {'expos':>6}")
        print(f"  {'-'*6}  {'-'*7}  {'-'*7}  {'-'*7}  "
              f"{'-'*9}  {'-'*9}  {'-'*9}  {'-'*7}  {'-'*6}")

        param_results = [r for r in results if r["param"] == param_name]
        param_results.sort(key=lambda r: r["value"])

        for row in param_results:
            t = row["splits"].get("train", {}) or {}
            v = row["splits"].get("validation", {}) or {}
            h = row["splits"].get("holdout", {}) or {}

            # Highlight baseline value
            marker = " <-- baseline" if row["value"] == BASELINE_CONFIG[param_name] else ""
            print(f"  {row['value']:>6.2f}  {t.get('sharpe', 0):>+7.2f}  "
                  f"{v.get('sharpe', 0):>+7.2f}  {h.get('sharpe', 0):>+7.2f}  "
                  f"{t.get('total_trades', 0):>9}  {v.get('total_trades', 0):>9}  "
                  f"{h.get('total_trades', 0):>9}  "
                  f"{h.get('max_drawdown', 0)*100:>+6.1f}%  "
                  f"{h.get('exposure_pct', 0)*100:>5.1f}%{marker}")


def main():
    logging.basicConfig(level=logging.WARNING)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", default="alpha_research")
    p.add_argument("--configs", default="configs")
    p.add_argument("--json-output", help="save results to JSON file")
    args = p.parse_args()

    workspace = Path(args.workspace).resolve()
    configs_dir = Path(args.configs).resolve()
    store = MarkdownStore(workspace)
    v6_research = store.root / "families" / "volatility_compression_atrclose_and_40-bar_high-lo_v6" / "research"

    if not v6_research.exists():
        print(f"v6 research not found at {v6_research}")
        sys.exit(1)

    results = run_scan("sensitivity_scan_v6", store, configs_dir, v6_research)
    print_summary(results)

    if args.json_output:
        with open(args.json_output, "w") as f:
            json.dump(results, f, indent=2, default=float)
        print(f"\nResults saved to {args.json_output}")


if __name__ == "__main__":
    main()
