#!/usr/bin/env python3
"""Engine-faithful pre-flight: run a strategy through the actual backtest engine
across train/val/holdout splits at multiple cost levels.

This replaces the pattern of custom event-driven pre-flights that diverge from
engine semantics (see compression_breakout v1 lessons). Custom pre-flights are
fine as a *first* sanity check on the strategy logic, but the *final* pre-flight
before drafting a seed should always go through this tool to surface engine
integration issues early.

Usage:

    # Run against an existing family
    uv run python scripts/preflight_via_engine.py \\
        --family compression_breakout_v1

    # Run against a strategy directory (must contain features.py / labels.py /
    # model_config.py / signal_combiner.py); copies into a sandbox family.
    uv run python scripts/preflight_via_engine.py \\
        --strategy-dir /tmp/my_strategy \\
        --sandbox-name preflight_compression_breakout

    # Override symbols and timeframe at the engine level
    uv run python scripts/preflight_via_engine.py \\
        --family compression_breakout_v1 \\
        --symbols ETHUSDT --timeframes 4h
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

# Project root on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.engine.backtest_runner import run_backtest


REQUIRED_FILES = ("features.py", "labels.py", "model_config.py", "signal_combiner.py")
DEFAULT_SPLITS = ("train", "validation", "holdout")
DEFAULT_COSTS_BPS = (0, 20)  # 20 bps RT covers BTC/ETH/SOL spot at 4-10 bps fee + 1-5 slip


def _ensure_family(
    family_id: str,
    strategy_dir: Path | None,
    workspace: Path,
) -> str:
    """Either validate the family already exists, or copy files into a sandbox.

    Returns the resolved family_id to use for the runs.
    """
    families_dir = workspace / "families"

    if strategy_dir is not None:
        # Validate strategy_dir has the four research files
        for fn in REQUIRED_FILES:
            if not (strategy_dir / fn).exists():
                raise SystemExit(f"--strategy-dir is missing {fn}")

        sandbox = families_dir / family_id
        if sandbox.exists():
            print(f"[preflight] Sandbox family {family_id} already exists; overwriting research/")
        sandbox.mkdir(parents=True, exist_ok=True)
        # Minimal FAMILY.md so MarkdownStore can read it
        if not (sandbox / "FAMILY.md").exists():
            (sandbox / "FAMILY.md").write_text(
                "---\n"
                f"family_id: {family_id}\n"
                f"seed_id: preflight_sandbox\n"
                "base_hypothesis: preflight sandbox\n"
                "mechanism: preflight sandbox\n"
                "state: QUEUED\n"
                "---\n"
            )
        research = sandbox / "research"
        research.mkdir(exist_ok=True)
        for fn in REQUIRED_FILES:
            shutil.copy2(strategy_dir / fn, research / fn)
        # Drop any cached __pycache__
        cache = research / "__pycache__"
        if cache.exists():
            shutil.rmtree(cache)
        print(f"[preflight] Sandboxed strategy at {sandbox}")
        return family_id

    # Existing family path — just verify research files are present
    research = families_dir / family_id / "research"
    if not research.exists():
        raise SystemExit(
            f"Family {family_id} has no research/ directory. "
            f"Did you mean --strategy-dir?"
        )
    missing = [fn for fn in REQUIRED_FILES if not (research / fn).exists()]
    if missing:
        raise SystemExit(f"Family {family_id} is missing research files: {missing}")
    return family_id


def _run_one(
    family_id: str,
    store: MarkdownStore,
    configs_dir: Path,
    split_name: str,
    fee_bps: float,
    slippage_bps: float,
    symbols: list[str] | None,
    timeframe: str | None,
) -> list[dict[str, Any]]:
    """Run a single (split, cost) cell via run_backtest. Returns metrics rows."""
    fee_rate = fee_bps / 1e4 / 2.0  # round-trip cost split per side as fee_rate
    slip = slippage_bps / 2.0
    results = run_backtest(
        family_id,
        store,
        configs_dir=configs_dir,
        split_name=split_name,
        fee_rate_override=fee_rate,
        slippage_override=slip,
        symbols_override=symbols,
        timeframe_override=timeframe,
        save_html=False,
    )
    rows: list[dict[str, Any]] = []
    for r in results:
        m = r.all_metrics
        rows.append(dict(
            symbol=r.symbol, timeframe=r.timeframe,
            split=split_name, cost_bps=fee_bps + slippage_bps,
            sharpe=m.get("sharpe", 0.0),
            total_return=m.get("total_return", 0.0),
            max_drawdown=m.get("max_drawdown", 0.0),
            total_trades=m.get("total_trades", 0),
            win_rate=m.get("win_rate", 0.0),
            profit_factor=m.get("profit_factor", 0.0),
            avg_trade_return=m.get("avg_trade_return", 0.0),
            exposure_pct=m.get("exposure_pct", 0.0),
            long_exposure_pct=m.get("long_exposure_pct", 0.0),
            short_exposure_pct=m.get("short_exposure_pct", 0.0),
        ))
    return rows


def _decide(rows: list[dict[str, Any]], cost_bps_for_decision: int) -> tuple[bool, list[str]]:
    """Apply the standard decision rule to a result table.

    Pass criteria:
    - At realistic cost (cost_bps_for_decision), all 3 splits positive Sharpe
      on at least 2 distinct symbols (or all symbols if only 1)
    - Sub-period collapse not catastrophic (no Sharpe < -1.0)
    """
    notes: list[str] = []
    by_symbol: dict[str, dict[str, float]] = {}
    for row in rows:
        if int(row["cost_bps"]) != cost_bps_for_decision:
            continue
        sym = row["symbol"]
        by_symbol.setdefault(sym, {})[row["split"]] = row["sharpe"]

    if not by_symbol:
        return False, [f"no rows at {cost_bps_for_decision}bps"]

    passing = 0
    for sym, splits in by_symbol.items():
        sharpes = list(splits.values())
        if all(s > 0 for s in sharpes):
            passing += 1
            notes.append(f"  {sym}: PASS at {cost_bps_for_decision}bps "
                         + " / ".join(f"{k}={v:+.2f}" for k, v in splits.items()))
        else:
            notes.append(f"  {sym}: fail "
                         + " / ".join(f"{k}={v:+.2f}" for k, v in splits.items()))
        if any(s < -1.0 for s in sharpes):
            notes.append(f"    -- catastrophic split (<−1.0) seen on {sym}")

    threshold = max(2, len(by_symbol)) if len(by_symbol) > 1 else 1
    return passing >= min(threshold, len(by_symbol)), notes


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--family", help="existing family_id (must have research/)")
    p.add_argument("--strategy-dir", type=Path,
                   help="path to a directory with the 4 research files (alternative to --family)")
    p.add_argument("--sandbox-name",
                   help="sandbox family_id when using --strategy-dir (default: preflight_<dir-name>)")
    p.add_argument("--workspace", default="alpha_research", help="workspace root")
    p.add_argument("--configs", default="configs", help="configs dir")
    p.add_argument("--symbols", help="comma-separated override (e.g. BTCUSDT,ETHUSDT)")
    p.add_argument("--timeframes", help="comma-separated; runs each one")
    p.add_argument("--splits", default=",".join(DEFAULT_SPLITS),
                   help=f"comma-separated splits (default: {','.join(DEFAULT_SPLITS)})")
    p.add_argument("--costs-bps", default=",".join(str(c) for c in DEFAULT_COSTS_BPS),
                   help=f"comma-separated cost levels in bps RT (default: {','.join(str(c) for c in DEFAULT_COSTS_BPS)})")
    p.add_argument("--decision-cost-bps", type=int, default=20,
                   help="the cost level used for pass/fail (default: 20)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of formatted table")
    p.add_argument("--quiet", action="store_true", help="suppress engine logs")
    args = p.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    if not args.family and not args.strategy_dir:
        p.error("must pass --family or --strategy-dir")
    if args.family and args.strategy_dir:
        p.error("--family and --strategy-dir are mutually exclusive")

    workspace = Path(args.workspace).resolve()
    configs_dir = Path(args.configs).resolve()
    store = MarkdownStore(workspace)

    family_id = args.family
    if args.strategy_dir:
        sandbox_name = args.sandbox_name or f"preflight_{args.strategy_dir.name}"
        family_id = _ensure_family(sandbox_name, args.strategy_dir, workspace)
    else:
        family_id = _ensure_family(family_id, None, workspace)

    splits = [s.strip() for s in args.splits.split(",") if s.strip()]
    costs = [int(c.strip()) for c in args.costs_bps.split(",") if c.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",")] if args.timeframes else [None]
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None

    rows: list[dict[str, Any]] = []
    for tf in timeframes:
        for split in splits:
            for cost in costs:
                fee_bps = cost / 2.0
                slip_bps = cost / 2.0
                cell_rows = _run_one(
                    family_id, store, configs_dir, split,
                    fee_bps=fee_bps, slippage_bps=slip_bps,
                    symbols=symbols, timeframe=tf,
                )
                rows.extend(cell_rows)

    if args.json:
        print(json.dumps(rows, indent=2, default=float))
        return

    # Formatted table grouped by symbol/timeframe
    print(f"\n=== Pre-flight via engine: {family_id} ===")
    sym_tf = sorted({(r["symbol"], r["timeframe"]) for r in rows})
    for sym, tf in sym_tf:
        print(f"\n{sym} {tf}")
        print(f"  {'cost_bps':>9}  {'split':>10}  {'Sharpe':>7}  {'ret':>7}  {'maxDD':>7}  "
              f"{'trades':>6}  {'win%':>5}  {'PF':>5}  {'expos':>6}")
        for cost in costs:
            for split in splits:
                match = [r for r in rows if r["symbol"] == sym and r["timeframe"] == tf
                         and int(r["cost_bps"]) == cost and r["split"] == split]
                if not match:
                    continue
                m = match[0]
                print(f"  {cost:>9}  {split:>10}  {m['sharpe']:>+6.2f}  "
                      f"{m['total_return']*100:>+6.1f}%  {m['max_drawdown']*100:>+6.1f}%  "
                      f"{m['total_trades']:>6}  {m['win_rate']*100:>4.0f}%  {m['profit_factor']:>5.2f}  "
                      f"{m['exposure_pct']*100:>5.1f}%")

    # Decision rule
    print(f"\n=== Decision rule (at {args.decision_cost_bps} bps RT, all-3-splits-positive) ===")
    passed, notes = _decide(rows, args.decision_cost_bps)
    for line in notes:
        print(line)
    print(f"\n>>> PROCEED TO SEED: {passed}")


if __name__ == "__main__":
    main()
