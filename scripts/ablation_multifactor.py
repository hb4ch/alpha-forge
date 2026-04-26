#!/usr/bin/env python3
"""Multi-factor ablation study across train/validation/holdout.

Tests combinations of stable features from probe_daily.py results.
All variants evaluated on ALL 3 periods to catch overfit.
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

SYMBOLS = ["ETHUSDT", "BTCUSDT", "SOLUSDT", "BNBUSDT"]
PERIODS = {
    "train": (SPLITS["train"]["start"], SPLITS["train"]["end"]),
    "val": (SPLITS["validation"]["start"], SPLITS["validation"]["end"]),
    "hold": (SPLITS["holdout"]["start"], SPLITS["holdout"]["end"]),
}


# ── Feature Computation ──────────────────────────────────────────────────────

def compute_all_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute all candidate features for multi-factor signal."""
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    volume = bars["volume"]
    tc = bars["trade_count"]

    feats = pd.DataFrame(index=bars.index)

    # Mean-Reversion
    sma20 = close.rolling(20, min_periods=20).mean()
    std20 = close.rolling(20, min_periods=20).std()
    feats["zscore_close_20d"] = ((close - sma20) / std20.clip(lower=1e-10)).shift(1)

    # RSI-14
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=13, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(com=13, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.clip(lower=1e-10)
    rsi = 100 - 100 / (1 + rs)
    feats["rsi_14"] = ((rsi - 50) / 50).shift(1)

    # Momentum
    feats["ret_10d"] = close.pct_change(10).shift(1)
    feats["ret_20d"] = close.pct_change(20).shift(1)

    # Volume/Activity
    tc_ma20 = tc.rolling(20, min_periods=20).mean()
    vol_ma20 = volume.rolling(20, min_periods=20).mean()
    feats["trade_intensity"] = (tc / tc_ma20.clip(lower=1)).shift(1)
    feats["volume_ma_ratio"] = (volume / vol_ma20.clip(lower=1e-10)).shift(1)

    # Volatility
    ret_daily = close.pct_change()
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr5 = tr.rolling(5, min_periods=5).mean()
    atr20 = tr.rolling(20, min_periods=20).mean()
    feats["atr_ratio"] = (atr5 / atr20.clip(lower=1e-10)).shift(1)

    # Regime indicator: trend strength
    abs_daily_ret = close.pct_change().abs()
    net_ret_20 = close.pct_change(20).abs()
    path_sum_20 = abs_daily_ret.rolling(20, min_periods=20).sum()
    feats["trend_strength_20d"] = (net_ret_20 / path_sum_20.clip(lower=1e-10)).shift(1)

    return feats


def zscore_normalize(series: pd.Series, window: int = 60) -> pd.Series:
    """Rolling z-score with winsorization at ±3."""
    mu = series.rolling(window, min_periods=window).mean()
    sigma = series.rolling(window, min_periods=window).std().clip(lower=1e-8)
    z = (series - mu) / sigma
    return z.clip(-3, 3)


# ── Signal Functions ─────────────────────────────────────────────────────────

def make_signal_fn(feature_names: list[str], weights: list[float] | None = None,
                   regime_weights: dict | None = None, long_only: bool = True):
    """Factory for multi-factor signal functions.

    Args:
        feature_names: Which features to use.
        weights: Static weights (equal if None).
        regime_weights: If set, dict with 'trending' and 'choppy' weight vectors.
        long_only: Clip signal to [0, 1] if True.
    """
    def signal_fn(features: pd.DataFrame) -> pd.Series:
        n = len(feature_names)
        if weights is None:
            w = np.ones(n) / n
        else:
            w = np.array(weights)

        # Z-score normalize each feature
        z_feats = pd.DataFrame(index=features.index)
        for f in feature_names:
            z_feats[f] = zscore_normalize(features[f])

        if regime_weights is not None:
            # Detect regime from trend_strength_20d
            ts = features["trend_strength_20d"]
            ts_smooth = ts.rolling(20, min_periods=10).mean()
            ts_median = ts_smooth.rolling(60, min_periods=30).median()
            trending = ts_smooth > ts_median

            w_trend = np.array(regime_weights["trending"])
            w_chop = np.array(regime_weights["choppy"])
            w_matrix = np.where(
                trending.values[:, None],
                w_trend[None, :],
                w_chop[None, :],
            )
            composite = (z_feats[feature_names].values * w_matrix).sum(axis=1)
            signal = pd.Series(composite, index=features.index)
        else:
            composite = (z_feats[feature_names] * w).sum(axis=1)
            signal = composite

        # Scale to reasonable range
        signal = signal / max(signal.std(), 1e-8) * 0.3  # target ~0.3 std

        if long_only:
            signal = signal.clip(lower=0.0, upper=1.0)
        else:
            signal = signal.clip(lower=-1.0, upper=1.0)

        # Warmup
        signal[features[feature_names[0]].isna()] = np.nan
        return signal

    return signal_fn


# ── Strategy Wrapper ─────────────────────────────────────────────────────────

class MultiFactorStrategy(Strategy):
    def __init__(self, signal_fn):
        super().__init__()
        self._signal_fn = signal_fn

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        features = compute_all_features(bars)
        return self._signal_fn(features)


# ── Variant Definitions ──────────────────────────────────────────────────────

# Stable-only features (YES in all 3 periods)
STABLE = ["rsi_14", "ret_20d", "zscore_close_20d"]

# Stable + weak-but-positive
STABLE_PLUS = ["rsi_14", "ret_20d", "zscore_close_20d", "ret_10d"]

# Stable + activity (which flipped in holdout)
STABLE_ACTIVITY = ["rsi_14", "ret_20d", "zscore_close_20d", "trade_intensity", "volume_ma_ratio"]

# Kitchen sink
ALL_CANDIDATE = ["rsi_14", "ret_20d", "zscore_close_20d", "ret_10d",
                 "trade_intensity", "volume_ma_ratio", "atr_ratio"]

VARIANTS = {}

# A: Stable features, equal weight, long-only
VARIANTS["A_stable_eq_LO"] = {
    "features": STABLE,
    "weights": None,
    "regime_weights": None,
    "long_only": True,
    "stop_loss_pct": 0.10,
}

# B: Stable features, equal weight, long-short
VARIANTS["B_stable_eq_LS"] = {
    "features": STABLE,
    "weights": None,
    "regime_weights": None,
    "long_only": False,
    "stop_loss_pct": 0.10,
}

# C: Stable features, IC-weighted (from train ICs)
# rsi_14: +0.048, ret_20d: +0.044, zscore_close_20d: +0.037
ic_stable = np.array([0.048, 0.044, 0.037])
ic_stable_w = ic_stable / ic_stable.sum()
VARIANTS["C_stable_icw_LO"] = {
    "features": STABLE,
    "weights": ic_stable_w.tolist(),
    "regime_weights": None,
    "long_only": True,
    "stop_loss_pct": 0.10,
}

# D: Stable + weak, equal weight
VARIANTS["D_stable_plus_LO"] = {
    "features": STABLE_PLUS,
    "weights": None,
    "regime_weights": None,
    "long_only": True,
    "stop_loss_pct": 0.10,
}

# E: Stable + activity, equal weight
VARIANTS["E_with_activity_LO"] = {
    "features": STABLE_ACTIVITY,
    "weights": None,
    "regime_weights": None,
    "long_only": True,
    "stop_loss_pct": 0.10,
}

# F: Activity only (v1 baseline for comparison)
VARIANTS["F_activity_only"] = {
    "features": ["trade_intensity", "volume_ma_ratio"],
    "weights": None,
    "regime_weights": None,
    "long_only": True,
    "stop_loss_pct": 0.10,
}

# G: Regime-adaptive (stable features, heavier momentum in trend, MR in chop)
VARIANTS["G_regime_stable_LO"] = {
    "features": STABLE,  # rsi_14, ret_20d, zscore_close_20d
    "weights": None,
    "regime_weights": {
        "trending": [0.20, 0.50, 0.30],  # heavy on momentum (ret_20d)
        "choppy":   [0.50, 0.15, 0.35],  # heavy on MR (rsi_14, zscore)
    },
    "long_only": True,
    "stop_loss_pct": 0.10,
}

# H: All candidates, equal weight
VARIANTS["H_all_eq_LO"] = {
    "features": ALL_CANDIDATE,
    "weights": None,
    "regime_weights": None,
    "long_only": True,
    "stop_loss_pct": 0.10,
}

# I: All candidates, regime-adaptive
VARIANTS["I_all_regime_LO"] = {
    "features": ALL_CANDIDATE,
    # rsi_14, ret_20d, zscore_close_20d, ret_10d, trade_intensity, volume_ma_ratio, atr_ratio
    "weights": None,
    "regime_weights": {
        "trending": [0.10, 0.25, 0.05, 0.15, 0.20, 0.15, 0.10],
        "choppy":   [0.30, 0.05, 0.25, 0.05, 0.10, 0.10, 0.15],
    },
    "long_only": True,
    "stop_loss_pct": 0.10,
}

# J: RSI only (single strongest stable feature)
VARIANTS["J_rsi_only_LO"] = {
    "features": ["rsi_14"],
    "weights": None,
    "regime_weights": None,
    "long_only": True,
    "stop_loss_pct": 0.10,
}


# ── Runner ───────────────────────────────────────────────────────────────────

def run_variant(name: str, spec: dict, bars_cache: dict[str, pd.DataFrame]) -> dict:
    signal_fn = make_signal_fn(
        spec["features"],
        spec.get("weights"),
        spec.get("regime_weights"),
        spec.get("long_only", True),
    )
    config = BacktestConfig(
        stop_loss_pct=spec.get("stop_loss_pct", 0.10),
        trailing_stop_pct=spec.get("trailing_stop_pct"),
        timeframe="1d",
    )
    strategy = MultiFactorStrategy(signal_fn)
    engine = BacktestEngine(strategy, config)

    results = {}
    for period_name, (start, end) in PERIODS.items():
        results[period_name] = {}
        for sym in SYMBOLS:
            bars = bars_cache[sym].loc[start:end]
            if len(bars) < 80:
                results[period_name][sym] = {"return": np.nan, "sharpe": np.nan, "max_dd": np.nan}
                continue
            result = engine.run_on_bars(bars, symbol=sym, timeframe="1d")
            eq = result.equity_curve
            total_ret = eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1
            returns = eq["returns"].dropna()
            sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0
            max_dd = eq["drawdown"].min()
            pos = result.positions.dropna()
            exposure = (pos.abs() > 0.01).mean()
            results[period_name][sym] = {
                "return": total_ret,
                "sharpe": sharpe,
                "max_dd": max_dd,
                "exposure": exposure,
            }
    return results


def main():
    # Load bars from cache
    print("Loading cached bars...")
    t0 = time.time()
    config = BacktestConfig()
    provider = DataProvider(config)
    bars_cache = {}
    full_start = SPLITS["train"]["start"]
    full_end = SPLITS["holdout"]["end"]
    for sym in SYMBOLS:
        bars_cache[sym] = provider.get_bars(sym, full_start, full_end, "1d")
        print(f"  {sym}: {len(bars_cache[sym])} bars")
    provider.close()
    print(f"  Loaded in {time.time() - t0:.2f}s\n")

    # Run all variants
    all_results = {}
    for name, spec in VARIANTS.items():
        t0 = time.time()
        all_results[name] = run_variant(name, spec, bars_cache)
        print(f"  {name}: {time.time() - t0:.1f}s")

    # ── Results Table ──
    print(f"\n{'=' * 140}")
    print("MULTI-FACTOR ABLATION — RETURNS BY PERIOD".center(140))
    print("=" * 140)

    hdr = f"{'Variant':<22s} |"
    for pn in PERIODS:
        hdr += f" {pn + ' Avg':>9s}  {pn + ' Shp':>8s} |"
    hdr += f" {'All3>0?':>7s}  {'Overfit?':>8s}"
    print(hdr)
    print("-" * 140)

    for name, results in all_results.items():
        row = f"{name:<22s} |"
        period_rets = []
        period_sharpes = []
        for pn in PERIODS:
            rets = [results[pn][s]["return"] for s in SYMBOLS if not np.isnan(results[pn][s]["return"])]
            sharpes = [results[pn][s]["sharpe"] for s in SYMBOLS if not np.isnan(results[pn][s]["sharpe"])]
            avg_ret = np.mean(rets) if rets else np.nan
            avg_sharpe = np.mean(sharpes) if sharpes else np.nan
            period_rets.append(avg_ret)
            period_sharpes.append(avg_sharpe)
            row += f" {avg_ret:>+8.1%}  {avg_sharpe:>+7.2f}  |"

        all_positive = all(r > 0 for r in period_rets if not np.isnan(r))
        # Overfit check: val/train ratio
        if period_rets[0] > 0.001:
            overfit_ratio = period_rets[1] / period_rets[0]
            overfit = "YES" if overfit_ratio > 3.0 else "no"
        else:
            overfit = "n/a"

        row += f" {'YES' if all_positive else 'NO':>7s}  {overfit:>8s}"
        print(row)

    # ── Per-Symbol Detail ──
    print(f"\n{'=' * 140}")
    print("PER-SYMBOL RETURNS (all periods)".center(140))
    print("=" * 140)

    for name, results in all_results.items():
        print(f"\n  {name}:")
        hdr = f"    {'Period':<8s}"
        for s in SYMBOLS:
            hdr += f"  {s:>12s}"
        hdr += f"  {'Avg':>8s}"
        print(hdr)
        for pn in PERIODS:
            row = f"    {pn:<8s}"
            vals = []
            for s in SYMBOLS:
                r = results[pn][s]["return"]
                vals.append(r)
                row += f"  {r:>+11.1%}" if not np.isnan(r) else f"  {'N/A':>12s}"
            row += f"  {np.nanmean(vals):>+7.1%}"
            print(row)

    # ── Exposure Analysis ──
    print(f"\n{'=' * 140}")
    print("AVERAGE EXPOSURE (fraction of days with position)".center(140))
    print("=" * 140)

    hdr = f"{'Variant':<22s}"
    for pn in PERIODS:
        hdr += f"  {pn:>8s}"
    print(hdr)
    print("-" * 60)

    for name, results in all_results.items():
        row = f"{name:<22s}"
        for pn in PERIODS:
            exps = [results[pn][s].get("exposure", np.nan) for s in SYMBOLS]
            row += f"  {np.nanmean(exps):>7.1%}"
        print(row)

    # ── Max Drawdown ──
    print(f"\n{'=' * 140}")
    print("WORST MAX DRAWDOWN (across symbols, per period)".center(140))
    print("=" * 140)

    hdr = f"{'Variant':<22s}"
    for pn in PERIODS:
        hdr += f"  {pn:>8s}"
    print(hdr)
    print("-" * 60)

    for name, results in all_results.items():
        row = f"{name:<22s}"
        for pn in PERIODS:
            dds = [results[pn][s]["max_dd"] for s in SYMBOLS if not np.isnan(results[pn][s]["max_dd"])]
            worst = min(dds) if dds else np.nan
            row += f"  {worst:>7.1%}"
        print(row)


if __name__ == "__main__":
    main()
