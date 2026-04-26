#!/usr/bin/env python3
"""Fine-grained ablation around the best combined variant.

3f_regime_sma100_vt20 achieved -0.3% holdout. This script tests:
- SMA lengths 80-150 in steps of 10
- Vol targets 10-25% in steps of 5
- Regime weight variations
- SMA slope filter (require SMA to be rising)
- Dual SMA filter (close > SMA_fast AND SMA_fast > SMA_slow)
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

SYMBOLS = ["ETHUSDT", "BTCUSDT", "SOLUSDT"]
PERIODS = {
    "train": (SPLITS["train"]["start"], SPLITS["train"]["end"]),
    "val": (SPLITS["validation"]["start"], SPLITS["validation"]["end"]),
    "hold": (SPLITS["holdout"]["start"], SPLITS["holdout"]["end"]),
}

FEATURES_3 = ["rsi_14", "ret_20d", "zscore_close_20d"]


# ── Feature Computation ─────────────────────────────────────────────────────

def compute_features(bars: pd.DataFrame) -> pd.DataFrame:
    close = bars["close"]
    feats = pd.DataFrame(index=bars.index)

    # RSI-14
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=13, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(com=13, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.clip(lower=1e-10)
    rsi = 100 - 100 / (1 + rs)
    feats["rsi_14"] = ((rsi - 50) / 50).shift(1)

    feats["ret_20d"] = close.pct_change(20).shift(1)

    sma20 = close.rolling(20, min_periods=20).mean()
    std20 = close.rolling(20, min_periods=20).std()
    feats["zscore_close_20d"] = ((close - sma20) / std20.clip(lower=1e-10)).shift(1)

    # Precompute SMAs for trend filters
    for n in [50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150]:
        feats[f"sma_{n}"] = close.rolling(n, min_periods=n).mean()
    feats["close"] = close

    return feats


def zscore_normalize(series: pd.Series, window: int = 60) -> pd.Series:
    mu = series.rolling(window, min_periods=window).mean()
    sigma = series.rolling(window, min_periods=window).std().clip(lower=1e-8)
    z = (series - mu) / sigma
    return z.clip(-3, 3)


def apply_vol_target(signal: pd.Series, bars: pd.DataFrame,
                     target_vol: float, vol_lookback: int = 20) -> pd.Series:
    ret = bars["close"].pct_change()
    realized_vol = ret.rolling(vol_lookback, min_periods=vol_lookback).std() * np.sqrt(252)
    realized_vol = realized_vol.shift(1)
    scale = (target_vol / realized_vol.clip(lower=0.01)).clip(upper=10.0)
    adjusted = signal * scale
    adjusted = adjusted.clip(lower=0.0, upper=3.0)
    adjusted[signal.isna()] = np.nan
    return adjusted


# ── Strategy ─────────────────────────────────────────────────────────────────

class CombinedStrategy(Strategy):
    def __init__(self, feature_names, weights=None, sma_filter=None,
                 sma_slope=False, dual_sma=None, target_vol=None,
                 vol_lookback=20, regime_weights=None):
        super().__init__()
        self.feature_names = feature_names
        self.weights = weights
        self.sma_filter = sma_filter
        self.sma_slope = sma_slope  # require SMA to be rising
        self.dual_sma = dual_sma    # (fast, slow) SMA cross filter
        self.target_vol = target_vol
        self.vol_lookback = vol_lookback
        self.regime_weights = regime_weights

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        feats = compute_features(bars)
        n = len(self.feature_names)

        z_feats = pd.DataFrame(index=feats.index)
        for f in self.feature_names:
            z_feats[f] = zscore_normalize(feats[f])

        if self.regime_weights is not None:
            ret_abs = bars["close"].pct_change().abs()
            net_20 = bars["close"].pct_change(20).abs()
            path_20 = ret_abs.rolling(20, min_periods=20).sum()
            ts = (net_20 / path_20.clip(lower=1e-10)).shift(1)
            ts_smooth = ts.rolling(20, min_periods=10).mean()
            ts_median = ts_smooth.rolling(60, min_periods=30).median()
            trending = ts_smooth > ts_median

            w_trend = np.array(self.regime_weights["trending"])
            w_chop = np.array(self.regime_weights["choppy"])
            w_matrix = np.where(
                trending.values[:, None], w_trend[None, :], w_chop[None, :]
            )
            composite = (z_feats[self.feature_names].values * w_matrix).sum(axis=1)
            signal = pd.Series(composite, index=feats.index)
        else:
            if self.weights is not None:
                w = np.array(self.weights)
            else:
                w = np.ones(n) / n
            signal = (z_feats[self.feature_names] * w).sum(axis=1)

        sig_std = signal.std()
        if sig_std > 1e-8:
            signal = signal / sig_std * 0.3
        signal = signal.clip(lower=0.0, upper=1.0)

        # SMA trend filter
        if self.sma_filter is not None:
            sma_col = f"sma_{self.sma_filter}"
            if sma_col in feats.columns:
                sma_vals = feats[sma_col].shift(1)
                close_prev = feats["close"].shift(1)
                below_sma = close_prev < sma_vals

                if self.sma_slope:
                    # Also require SMA to be rising (5-day slope > 0)
                    sma_slope = sma_vals.diff(5)
                    below_sma = below_sma | (sma_slope <= 0)

                signal[below_sma] = 0.0

        # Dual SMA filter
        if self.dual_sma is not None:
            fast_n, slow_n = self.dual_sma
            fast_col = f"sma_{fast_n}"
            slow_col = f"sma_{slow_n}"
            if fast_col in feats.columns and slow_col in feats.columns:
                bearish = feats[fast_col].shift(1) < feats[slow_col].shift(1)
                signal[bearish] = 0.0

        # Vol targeting
        if self.target_vol is not None:
            signal = apply_vol_target(signal, bars, self.target_vol, self.vol_lookback)

        signal[feats[self.feature_names[0]].isna()] = np.nan
        return signal


# ── Variants ─────────────────────────────────────────────────────────────────

# Standard regime weights
RW_STANDARD = {
    "trending": [0.30, 0.45, 0.25],  # rsi, ret_20d, zscore
    "choppy":   [0.45, 0.15, 0.40],
}

# MR-heavy regime (less momentum everywhere)
RW_MR_HEAVY = {
    "trending": [0.35, 0.30, 0.35],
    "choppy":   [0.50, 0.10, 0.40],
}

# Strong trend tilt
RW_TREND_HEAVY = {
    "trending": [0.20, 0.55, 0.25],
    "choppy":   [0.50, 0.10, 0.40],
}

VARIANTS = {}

# ── SMA length sweep with regime, no vol target ──
for sma in [80, 90, 100, 110, 120, 130]:
    VARIANTS[f"regime_sma{sma}"] = dict(
        feature_names=FEATURES_3, sma_filter=sma,
        regime_weights=RW_STANDARD)

# ── SMA length sweep with regime + vol target ──
for sma in [80, 90, 100, 110, 120, 130]:
    for vt in [0.10, 0.15, 0.20]:
        VARIANTS[f"regime_sma{sma}_vt{int(vt*100)}"] = dict(
            feature_names=FEATURES_3, sma_filter=sma,
            target_vol=vt, regime_weights=RW_STANDARD)

# ── Regime weight variations at SMA100 ──
VARIANTS["regime_mr_sma100"] = dict(
    feature_names=FEATURES_3, sma_filter=100,
    regime_weights=RW_MR_HEAVY)
VARIANTS["regime_tr_sma100"] = dict(
    feature_names=FEATURES_3, sma_filter=100,
    regime_weights=RW_TREND_HEAVY)
VARIANTS["regime_mr_sma100_vt15"] = dict(
    feature_names=FEATURES_3, sma_filter=100, target_vol=0.15,
    regime_weights=RW_MR_HEAVY)
VARIANTS["regime_tr_sma100_vt15"] = dict(
    feature_names=FEATURES_3, sma_filter=100, target_vol=0.15,
    regime_weights=RW_TREND_HEAVY)

# ── SMA slope filter (require SMA to be rising) ──
for sma in [80, 100, 120]:
    VARIANTS[f"regime_sma{sma}_slope"] = dict(
        feature_names=FEATURES_3, sma_filter=sma, sma_slope=True,
        regime_weights=RW_STANDARD)
    VARIANTS[f"regime_sma{sma}_slope_vt15"] = dict(
        feature_names=FEATURES_3, sma_filter=sma, sma_slope=True,
        target_vol=0.15, regime_weights=RW_STANDARD)

# ── Dual SMA cross filter (no single SMA filter, use cross instead) ──
for fast, slow in [(50, 100), (50, 120), (60, 120)]:
    VARIANTS[f"regime_cross{fast}_{slow}"] = dict(
        feature_names=FEATURES_3, dual_sma=(fast, slow),
        regime_weights=RW_STANDARD)
    VARIANTS[f"regime_cross{fast}_{slow}_vt15"] = dict(
        feature_names=FEATURES_3, dual_sma=(fast, slow),
        target_vol=0.15, regime_weights=RW_STANDARD)

# ── Equal weight (no regime) with SMA100-120 + vol target ──
for sma in [100, 110, 120]:
    for vt in [0.10, 0.15]:
        VARIANTS[f"eq_sma{sma}_vt{int(vt*100)}"] = dict(
            feature_names=FEATURES_3, sma_filter=sma, target_vol=vt)


# ── Runner ───────────────────────────────────────────────────────────────────

def compute_metrics(returns: pd.Series) -> dict:
    r = returns.dropna()
    if len(r) < 10 or r.std() == 0:
        return {"return": 0, "sharpe": 0, "sortino": 0, "max_dd": 0, "ann_vol": 0}
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
    strategy = CombinedStrategy(
        feature_names=spec["feature_names"],
        weights=spec.get("weights"),
        sma_filter=spec.get("sma_filter"),
        sma_slope=spec.get("sma_slope", False),
        dual_sma=spec.get("dual_sma"),
        target_vol=spec.get("target_vol"),
        vol_lookback=spec.get("vol_lookback", 20),
        regime_weights=spec.get("regime_weights"),
    )
    config = BacktestConfig(stop_loss_pct=0.10, timeframe="1d")
    engine = BacktestEngine(strategy, config)

    results = {}
    for pn, (start, end) in PERIODS.items():
        period_returns = []
        for sym in SYMBOLS:
            bars = bars_cache[sym].loc[start:end]
            if len(bars) < 80:
                continue
            result = engine.run_on_bars(bars, symbol=sym, timeframe="1d")
            period_returns.append(result.equity_curve["returns"])
        if period_returns:
            combined = pd.concat(period_returns, axis=1).mean(axis=1)
            results[pn] = compute_metrics(combined)
        else:
            results[pn] = {"return": 0, "sharpe": 0, "sortino": 0,
                           "max_dd": 0, "ann_vol": 0}
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

    all_results = {}
    for name, spec in VARIANTS.items():
        t0 = time.time()
        all_results[name] = run_variant(spec, bars_cache)
        elapsed = time.time() - t0
        hold_ret = all_results[name]["hold"]["return"]
        marker = " <<<" if hold_ret > 0 else ""
        print(f"  {name}: {elapsed:.1f}s  hold={hold_ret:+.1%}{marker}")

    # ── Results Table ──
    print(f"\n{'=' * 170}")
    print("FINE-GRAINED ABLATION: 3-Factor Regime + SMA + Vol Target (no BNB)".center(170))
    print("=" * 170)

    hdr = f"{'Variant':<30s} |"
    for pn in PERIODS:
        hdr += f" {pn+'_ret':>9s} {pn+'_shp':>8s} {pn+'_vol':>8s} {pn+'_mdd':>8s} |"
    hdr += f" {'All3>0':>7s}"
    print(hdr)
    print("-" * 170)

    winners = []
    for name, results in sorted(all_results.items(), key=lambda x: -x[1]["hold"]["return"]):
        row = f"{name:<30s} |"
        period_rets = []
        for pn in PERIODS:
            m = results[pn]
            period_rets.append(m["return"])
            row += f" {m['return']:>+8.1%} {m['sharpe']:>+7.2f} {m['ann_vol']:>7.1%} {m['max_dd']:>7.1%} |"
        all_pos = all(r > 0 for r in period_rets)
        row += f" {'>>> YES' if all_pos else '     no':>7s}"
        if all_pos:
            winners.append(name)
        print(row)

    if winners:
        print(f"\n{'=' * 100}")
        print(f"WINNERS — Positive returns in ALL 3 periods ({len(winners)} variants)".center(100))
        print("=" * 100)
        for name in winners:
            r = all_results[name]
            avg_sharpe = np.mean([r[pn]["sharpe"] for pn in PERIODS])
            min_ret = min(r[pn]["return"] for pn in PERIODS)
            print(f"  {name:<30s}  avg_sharpe={avg_sharpe:+.2f}  min_period_ret={min_ret:+.1%}")
            for pn in PERIODS:
                m = r[pn]
                print(f"    {pn:<6s}: ret={m['return']:+.1%}  sharpe={m['sharpe']:+.2f}  "
                      f"vol={m['ann_vol']:.1%}  mdd={m['max_dd']:.1%}")
    else:
        print("\n  No variant achieved positive returns in all 3 periods.")
        print("\n  Top 10 closest (sorted by holdout return):")
        sorted_results = sorted(all_results.items(), key=lambda x: -x[1]["hold"]["return"])
        for name, results in sorted_results[:10]:
            r = results
            print(f"    {name:<30s}  hold={r['hold']['return']:+.2%}  "
                  f"train={r['train']['return']:+.1%}  val={r['val']['return']:+.1%}  "
                  f"hold_shp={r['hold']['sharpe']:+.2f}")


if __name__ == "__main__":
    main()
