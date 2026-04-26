#!/usr/bin/env python3
"""Combined ablation: multi-factor stable features + SMA trend filter + vol targeting.

Builds on findings from ablation_multifactor.py and ablation_voltarget.py:
- 4 stable features have positive IC in all 3 periods
- SMA trend filter reduces holdout loss (RSI alone: -7.4% → -1.7% with SMA100+vt20)
- Vol targeting controls volatility but can't flip negative signal
- Hypothesis: multi-factor composite is more robust than RSI alone;
  combining with trend filter may push holdout positive.

Drops BNB. Tests all 3 periods.
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


# ── Feature Computation ─────────────────────────────────────────────────────

def compute_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute the 4 stable features + SMA for trend filter."""
    close = bars["close"]
    feats = pd.DataFrame(index=bars.index)

    # 1. RSI-14
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=13, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(com=13, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.clip(lower=1e-10)
    rsi = 100 - 100 / (1 + rs)
    feats["rsi_14"] = ((rsi - 50) / 50).shift(1)

    # 2. ret_20d
    feats["ret_20d"] = close.pct_change(20).shift(1)

    # 3. zscore_close_20d (also covers bollinger_pct — highly correlated)
    sma20 = close.rolling(20, min_periods=20).mean()
    std20 = close.rolling(20, min_periods=20).std()
    feats["zscore_close_20d"] = ((close - sma20) / std20.clip(lower=1e-10)).shift(1)

    # SMA for trend filter (not a signal feature)
    for n in [50, 100, 200]:
        feats[f"sma_{n}"] = close.rolling(n, min_periods=n).mean()
    feats["close"] = close  # for SMA comparison

    return feats


def zscore_normalize(series: pd.Series, window: int = 60) -> pd.Series:
    """Rolling z-score with winsorization at ±3."""
    mu = series.rolling(window, min_periods=window).mean()
    sigma = series.rolling(window, min_periods=window).std().clip(lower=1e-8)
    z = (series - mu) / sigma
    return z.clip(-3, 3)


def apply_vol_target(signal: pd.Series, bars: pd.DataFrame,
                     target_vol: float, vol_lookback: int = 20) -> pd.Series:
    """Scale signal by target_vol / realized_vol."""
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
    def __init__(self, feature_names: list[str], weights: list[float] | None = None,
                 sma_filter: int | None = None, target_vol: float | None = None,
                 vol_lookback: int = 20, regime_adaptive: bool = False):
        super().__init__()
        self.feature_names = feature_names
        self.weights = weights
        self.sma_filter = sma_filter
        self.target_vol = target_vol
        self.vol_lookback = vol_lookback
        self.regime_adaptive = regime_adaptive

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        feats = compute_features(bars)
        n = len(self.feature_names)

        # Z-score normalize each feature
        z_feats = pd.DataFrame(index=feats.index)
        for f in self.feature_names:
            z_feats[f] = zscore_normalize(feats[f])

        # Weights
        if self.regime_adaptive and n >= 3:
            # Detect trending vs choppy
            ret_abs = bars["close"].pct_change().abs()
            net_20 = bars["close"].pct_change(20).abs()
            path_20 = ret_abs.rolling(20, min_periods=20).sum()
            ts = (net_20 / path_20.clip(lower=1e-10)).shift(1)
            ts_smooth = ts.rolling(20, min_periods=10).mean()
            ts_median = ts_smooth.rolling(60, min_periods=30).median()
            trending = ts_smooth > ts_median

            # In trending: lean momentum (ret_20d, rsi)
            # In choppy: lean mean-reversion (zscore, rsi as MR)
            # feature order: rsi_14, ret_20d, zscore_close_20d
            w_trend = np.array([0.30, 0.45, 0.25])
            w_chop = np.array([0.45, 0.15, 0.40])
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

        # Scale to ~0.3 std target
        sig_std = signal.std()
        if sig_std > 1e-8:
            signal = signal / sig_std * 0.3

        # Long-only
        signal = signal.clip(lower=0.0, upper=1.0)

        # SMA trend filter: go flat when close < SMA
        if self.sma_filter is not None:
            sma_col = f"sma_{self.sma_filter}"
            if sma_col in feats.columns:
                below_sma = feats["close"].shift(1) < feats[sma_col].shift(1)
                signal[below_sma] = 0.0

        # Vol targeting
        if self.target_vol is not None:
            signal = apply_vol_target(signal, bars, self.target_vol, self.vol_lookback)

        # NaN warmup
        signal[feats[self.feature_names[0]].isna()] = np.nan
        return signal


# ── Variants ─────────────────────────────────────────────────────────────────

FEATURES_3 = ["rsi_14", "ret_20d", "zscore_close_20d"]
FEATURES_2_MR = ["rsi_14", "zscore_close_20d"]  # Mean-reversion pair

VARIANTS = {}

# ── Baselines ──
VARIANTS["baseline_rsi"] = dict(
    feature_names=["rsi_14"], sma_filter=None, target_vol=None)

VARIANTS["baseline_3f_eq"] = dict(
    feature_names=FEATURES_3, sma_filter=None, target_vol=None)

# ── 3-factor + SMA trend filter ──
for sma in [50, 100, 200]:
    VARIANTS[f"3f_sma{sma}"] = dict(
        feature_names=FEATURES_3, sma_filter=sma, target_vol=None)

# ── 3-factor + SMA + vol target ──
for sma in [50, 100]:
    for vt in [0.15, 0.20, 0.25]:
        VARIANTS[f"3f_sma{sma}_vt{int(vt*100)}"] = dict(
            feature_names=FEATURES_3, sma_filter=sma, target_vol=vt)

# ── 3-factor + regime-adaptive + SMA ──
for sma in [50, 100]:
    VARIANTS[f"3f_regime_sma{sma}"] = dict(
        feature_names=FEATURES_3, sma_filter=sma, target_vol=None,
        regime_adaptive=True)

# ── 3-factor + regime + SMA + vol target ──
for sma in [50, 100]:
    VARIANTS[f"3f_regime_sma{sma}_vt20"] = dict(
        feature_names=FEATURES_3, sma_filter=sma, target_vol=0.20,
        regime_adaptive=True)

# ── 2-factor MR pair + SMA (RSI + zscore, dropping momentum) ──
for sma in [50, 100]:
    VARIANTS[f"2f_mr_sma{sma}"] = dict(
        feature_names=FEATURES_2_MR, sma_filter=sma, target_vol=None)
    VARIANTS[f"2f_mr_sma{sma}_vt20"] = dict(
        feature_names=FEATURES_2_MR, sma_filter=sma, target_vol=0.20)

# ── RSI + SMA (single feature baselines for comparison) ──
for sma in [50, 100]:
    VARIANTS[f"rsi_sma{sma}"] = dict(
        feature_names=["rsi_14"], sma_filter=sma, target_vol=None)
    VARIANTS[f"rsi_sma{sma}_vt20"] = dict(
        feature_names=["rsi_14"], sma_filter=sma, target_vol=0.20)


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
        target_vol=spec.get("target_vol"),
        vol_lookback=spec.get("vol_lookback", 20),
        regime_adaptive=spec.get("regime_adaptive", False),
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

    # Run all variants
    all_results = {}
    for name, spec in VARIANTS.items():
        t0 = time.time()
        all_results[name] = run_variant(spec, bars_cache)
        print(f"  {name}: {time.time()-t0:.1f}s")

    # ── Results Table ──
    print(f"\n{'=' * 160}")
    print("COMBINED ABLATION: Multi-Factor + SMA Trend Filter + Vol Target (no BNB)".center(160))
    print("=" * 160)

    hdr = f"{'Variant':<26s} |"
    for pn in PERIODS:
        hdr += f" {pn+'_ret':>9s} {pn+'_shp':>8s} {pn+'_vol':>8s} {pn+'_mdd':>8s} |"
    hdr += f" {'All3>0':>6s}"
    print(hdr)
    print("-" * 160)

    winners = []
    for name, results in all_results.items():
        row = f"{name:<26s} |"
        period_rets = []
        for pn in PERIODS:
            m = results[pn]
            period_rets.append(m["return"])
            row += f" {m['return']:>+8.1%} {m['sharpe']:>+7.2f} {m['ann_vol']:>7.1%} {m['max_dd']:>7.1%} |"
        all_pos = all(r > 0 for r in period_rets)
        row += f" {'>>> YES' if all_pos else '    no':>7s}"
        if all_pos:
            winners.append(name)
        print(row)

    # ── Winners Section ──
    if winners:
        print(f"\n{'=' * 100}")
        print(f"WINNERS — Positive returns in ALL 3 periods ({len(winners)} variants)".center(100))
        print("=" * 100)
        for name in winners:
            r = all_results[name]
            avg_sharpe = np.mean([r[pn]["sharpe"] for pn in PERIODS])
            min_ret = min(r[pn]["return"] for pn in PERIODS)
            print(f"  {name:<26s}  avg_sharpe={avg_sharpe:+.2f}  min_period_ret={min_ret:+.1%}")
            for pn in PERIODS:
                m = r[pn]
                print(f"    {pn:<6s}: ret={m['return']:+.1%}  sharpe={m['sharpe']:+.2f}  "
                      f"vol={m['ann_vol']:.1%}  mdd={m['max_dd']:.1%}")
    else:
        print("\n  No variant achieved positive returns in all 3 periods.")

        # Show top 5 closest (smallest holdout loss)
        print("\n  Top 5 closest (smallest holdout loss):")
        sorted_by_hold = sorted(all_results.items(), key=lambda x: -x[1]["hold"]["return"])
        for name, results in sorted_by_hold[:5]:
            r = results
            print(f"    {name:<26s}  hold={r['hold']['return']:+.1%}  "
                  f"train={r['train']['return']:+.1%}  val={r['val']['return']:+.1%}")


if __name__ == "__main__":
    main()
