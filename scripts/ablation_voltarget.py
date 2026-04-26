#!/usr/bin/env python3
"""Volatility-targeting ablation for J_rsi_only strategy.

Tests different target vol levels and lookback windows.
Drops BNB. Evaluates across all 3 periods.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "crypto-pegasus"))

import time
import numpy as np
import pandas as pd
import yaml

from pegasus.config import BacktestConfig
from pegasus.data.provider import DataProvider
from pegasus.engine.backtest import BacktestEngine
from pegasus.strategy.base import Strategy

# ── Config ───────────────────────────────────────────────────────────────────

with open(Path(__file__).resolve().parent.parent / "configs/splits.yaml") as f:
    SPLITS = yaml.safe_load(f)

SYMBOLS = ["ETHUSDT", "BTCUSDT", "SOLUSDT"]  # No BNB
PERIODS = {
    "train": (SPLITS["train"]["start"], SPLITS["train"]["end"]),
    "val": (SPLITS["validation"]["start"], SPLITS["validation"]["end"]),
    "hold": (SPLITS["holdout"]["start"], SPLITS["holdout"]["end"]),
}


# ── Features & Signal ────────────────────────────────────────────────────────

def compute_rsi_signal(bars: pd.DataFrame) -> pd.Series:
    """RSI-14, long-only, z-score normalized."""
    close = bars["close"]

    # RSI-14
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=13, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(com=13, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.clip(lower=1e-10)
    rsi = 100 - 100 / (1 + rs)
    rsi_centered = ((rsi - 50) / 50).shift(1)

    # Z-score normalize
    mu = rsi_centered.rolling(60, min_periods=60).mean()
    sigma = rsi_centered.rolling(60, min_periods=60).std().clip(lower=1e-8)
    z = ((rsi_centered - mu) / sigma).clip(-3, 3)

    # Scale and clip to [0, 1]
    signal = (z * 0.3).clip(lower=0.0, upper=1.0)
    signal[rsi_centered.isna()] = np.nan
    return signal


def apply_vol_target(
    signal: pd.Series,
    bars: pd.DataFrame,
    target_vol: float,
    vol_lookback: int = 20,
) -> pd.Series:
    """Scale signal by target_vol / realized_vol.

    target_vol: annualized target (e.g. 0.20 = 20%)
    vol_lookback: days for realized vol estimate
    """
    ret = bars["close"].pct_change()
    realized_vol = ret.rolling(vol_lookback, min_periods=vol_lookback).std() * np.sqrt(252)
    realized_vol = realized_vol.shift(1)  # avoid lookahead

    # Scale factor: target / realized, capped to avoid extreme leverage
    scale = (target_vol / realized_vol.clip(lower=0.01)).clip(upper=10.0)

    adjusted = signal * scale
    # Clip final signal to [0, max_leverage] — cap at 3x
    adjusted = adjusted.clip(lower=0.0, upper=3.0)
    adjusted[signal.isna()] = np.nan
    return adjusted


# ── Strategy ─────────────────────────────────────────────────────────────────

class VolTargetStrategy(Strategy):
    def __init__(self, target_vol: float, vol_lookback: int = 20):
        super().__init__()
        self.target_vol = target_vol
        self.vol_lookback = vol_lookback

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        raw_signal = compute_rsi_signal(bars)
        if self.target_vol is None:
            return raw_signal
        return apply_vol_target(raw_signal, bars, self.target_vol, self.vol_lookback)


# ── Variants ─────────────────────────────────────────────────────────────────

VARIANTS = {}

# Baseline: no vol targeting
VARIANTS["baseline_no_vt"] = {"target_vol": None, "vol_lookback": 20}

# Target vol sweep
for tv in [0.10, 0.15, 0.20, 0.25, 0.30, 0.40]:
    VARIANTS[f"vt_{int(tv*100)}pct"] = {"target_vol": tv, "vol_lookback": 20}

# Vol lookback sweep at 20% target
for lb in [10, 20, 40, 60]:
    VARIANTS[f"vt20_lb{lb}"] = {"target_vol": 0.20, "vol_lookback": lb}


# ── Runner ───────────────────────────────────────────────────────────────────

def compute_metrics(returns: pd.Series) -> dict:
    r = returns.dropna()
    if len(r) < 10 or r.std() == 0:
        return {"return": 0, "sharpe": 0, "sortino": 0, "max_dd": 0, "ann_vol": 0, "avg_lev": 0}
    ann = np.sqrt(252)
    cum = (1 + r).cumprod()
    total_ret = cum.iloc[-1] - 1
    sharpe = r.mean() / r.std() * ann
    downside = r[r < 0].std()
    sortino = r.mean() / downside * ann if downside > 0 else 0
    max_dd = (cum / cum.cummax() - 1).min()
    ann_vol = r.std() * ann
    return {"return": total_ret, "sharpe": sharpe, "sortino": sortino,
            "max_dd": max_dd, "ann_vol": ann_vol}


def run_variant(spec: dict, bars_cache: dict[str, pd.DataFrame]) -> dict:
    strategy = VolTargetStrategy(spec["target_vol"], spec["vol_lookback"])
    config = BacktestConfig(stop_loss_pct=0.10, timeframe="1d")
    engine = BacktestEngine(strategy, config)

    results = {}
    for pn, (start, end) in PERIODS.items():
        period_returns = []
        period_exposures = []
        for sym in SYMBOLS:
            bars = bars_cache[sym].loc[start:end]
            if len(bars) < 80:
                continue
            result = engine.run_on_bars(bars, symbol=sym, timeframe="1d")
            period_returns.append(result.equity_curve["returns"])
            pos = result.positions.dropna()
            period_exposures.append(pos.abs().mean())

        if period_returns:
            combined = pd.concat(period_returns, axis=1).mean(axis=1)
            m = compute_metrics(combined)
            m["avg_exposure"] = np.mean(period_exposures)
            results[pn] = m
        else:
            results[pn] = {"return": 0, "sharpe": 0, "sortino": 0,
                           "max_dd": 0, "ann_vol": 0, "avg_exposure": 0}
    return results


def main():
    print("Loading cached bars (no BNB)...")
    config = BacktestConfig()
    provider = DataProvider(config)
    bars_cache = {}
    full_start = SPLITS["train"]["start"]
    full_end = SPLITS["holdout"]["end"]
    for sym in SYMBOLS:
        bars_cache[sym] = provider.get_bars(sym, full_start, full_end, "1d")
        print(f"  {sym}: {len(bars_cache[sym])} bars")
    provider.close()
    print()

    # Run
    all_results = {}
    for name, spec in VARIANTS.items():
        t0 = time.time()
        all_results[name] = run_variant(spec, bars_cache)
        print(f"  {name}: {time.time()-t0:.1f}s")

    # ── Main Table ──
    print(f"\n{'=' * 150}")
    print("VOL-TARGETING ABLATION (RSI-14, no BNB, equal-weight portfolio)".center(150))
    print("=" * 150)

    hdr = f"{'Variant':<18s} |"
    for pn in PERIODS:
        hdr += f" {pn+'_ret':>9s} {pn+'_shp':>8s} {pn+'_vol':>8s} {pn+'_mdd':>8s} |"
    hdr += f" {'All3>0':>6s}"
    print(hdr)
    print("-" * 150)

    for name, results in all_results.items():
        row = f"{name:<18s} |"
        period_rets = []
        for pn in PERIODS:
            m = results[pn]
            period_rets.append(m["return"])
            row += f" {m['return']:>+8.1%} {m['sharpe']:>+7.2f} {m['ann_vol']:>7.1%} {m['max_dd']:>7.1%} |"
        all_pos = all(r > 0 for r in period_rets)
        row += f" {'YES' if all_pos else 'no':>6s}"
        print(row)

    # ── Exposure Table ──
    print(f"\n{'=' * 80}")
    print("AVERAGE EXPOSURE (position size)".center(80))
    print("=" * 80)
    hdr = f"{'Variant':<18s}"
    for pn in PERIODS:
        hdr += f"  {pn:>8s}"
    print(hdr)
    print("-" * 50)
    for name, results in all_results.items():
        row = f"{name:<18s}"
        for pn in PERIODS:
            row += f"  {results[pn]['avg_exposure']:>7.2f}x"
        print(row)

    # ── Per-Symbol Detail for best variant ──
    # Find best by: positive in all 3 periods, highest avg Sharpe
    best = None
    best_score = -999
    for name, results in all_results.items():
        rets = [results[pn]["return"] for pn in PERIODS]
        sharpes = [results[pn]["sharpe"] for pn in PERIODS]
        if all(r > 0 for r in rets):
            score = np.mean(sharpes)
            if score > best_score:
                best_score = score
                best = name

    if best:
        print(f"\n{'=' * 100}")
        print(f"BEST VARIANT: {best} (avg Sharpe = {best_score:.2f})".center(100))
        print("=" * 100)

        spec = VARIANTS[best]
        strategy = VolTargetStrategy(spec["target_vol"], spec["vol_lookback"])
        config = BacktestConfig(stop_loss_pct=0.10, timeframe="1d")
        engine = BacktestEngine(strategy, config)

        for pn, (start, end) in PERIODS.items():
            print(f"\n  {pn}:")
            print(f"    {'Symbol':<10s}  {'Return':>8s}  {'Sharpe':>7s}  {'MaxDD':>7s}  {'AnnVol':>7s}  {'Exposure':>8s}")
            for sym in SYMBOLS:
                bars = bars_cache[sym].loc[start:end]
                if len(bars) < 80:
                    continue
                result = engine.run_on_bars(bars, symbol=sym, timeframe="1d")
                m = compute_metrics(result.equity_curve["returns"])
                pos = result.positions.dropna()
                exp = pos.abs().mean()
                print(f"    {sym:<10s}  {m['return']:>+7.1%}  {m['sharpe']:>+6.2f}  {m['max_dd']:>6.1%}  {m['ann_vol']:>6.1%}  {exp:>7.2f}x")
    else:
        print("\n  No variant achieved positive returns in all 3 periods.")


if __name__ == "__main__":
    main()
