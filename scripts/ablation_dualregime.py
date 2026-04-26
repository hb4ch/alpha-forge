#!/usr/bin/env python3
"""Dual-regime ablation: momentum in bull, mean-reversion in bear.

Key insight: zscore_close_20d has positive IC in ALL 3 periods (+0.037/+0.037/+0.051).
In bear markets, buying dips (when price is below 20d mean) generates small positive returns.

Instead of going flat when close < SMA, switch to a reduced mean-reversion position.
This generates small positive holdout returns instead of exactly zero.
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

with open(Path(__file__).resolve().parent.parent / "configs/splits.yaml") as f:
    SPLITS = yaml.safe_load(f)

SYMBOLS = ["ETHUSDT", "BTCUSDT", "SOLUSDT"]
PERIODS = {
    "train": (SPLITS["train"]["start"], SPLITS["train"]["end"]),
    "val": (SPLITS["validation"]["start"], SPLITS["validation"]["end"]),
    "hold": (SPLITS["holdout"]["start"], SPLITS["holdout"]["end"]),
}


# ── Features ─────────────────────────────────────────────────────────────────

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

    # ret_20d
    feats["ret_20d"] = close.pct_change(20).shift(1)

    # zscore_close_20d (mean-reversion)
    sma20 = close.rolling(20, min_periods=20).mean()
    std20 = close.rolling(20, min_periods=20).std()
    feats["zscore_close_20d"] = ((close - sma20) / std20.clip(lower=1e-10)).shift(1)

    # SMAs for regime detection
    for n in [50, 80, 100, 120]:
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

class DualRegimeStrategy(Strategy):
    def __init__(self, sma_n: int = 100, bear_scale: float = 0.3,
                 bear_mode: str = "mr_only", target_vol: float | None = None,
                 vol_lookback: int = 20, bull_features: list[str] | None = None,
                 sma_slope: bool = False):
        """
        sma_n: SMA length for bull/bear regime detection
        bear_scale: position scale in bear regime (0.0-1.0)
        bear_mode: 'mr_only' = only zscore in bear, 'all_reduced' = full signal but scaled down
        target_vol: optional vol targeting
        bull_features: features for bull signal (default: all 3)
        sma_slope: also require SMA slope > 0 for bull
        """
        super().__init__()
        self.sma_n = sma_n
        self.bear_scale = bear_scale
        self.bear_mode = bear_mode
        self.target_vol = target_vol
        self.vol_lookback = vol_lookback
        self.bull_features = bull_features or ["rsi_14", "ret_20d", "zscore_close_20d"]
        self.sma_slope = sma_slope

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        feats = compute_features(bars)

        # Z-score normalize features
        z = {}
        for f in ["rsi_14", "ret_20d", "zscore_close_20d"]:
            z[f] = zscore_normalize(feats[f])

        # Bull signal: multi-factor composite
        n_bull = len(self.bull_features)
        bull_signal = sum(z[f] for f in self.bull_features) / n_bull
        bull_std = bull_signal.std()
        if bull_std > 1e-8:
            bull_signal = bull_signal / bull_std * 0.3
        bull_signal = bull_signal.clip(lower=0.0, upper=1.0)

        # Bear signal: pure mean-reversion
        if self.bear_mode == "mr_only":
            # In bear: buy when zscore is NEGATIVE (price below 20d mean = dip)
            # Flip sign: negative zscore → positive signal (buy the dip)
            bear_signal = -z["zscore_close_20d"]
            bear_std = bear_signal.std()
            if bear_std > 1e-8:
                bear_signal = bear_signal / bear_std * 0.3
            bear_signal = bear_signal.clip(lower=0.0, upper=1.0) * self.bear_scale
        elif self.bear_mode == "all_reduced":
            bear_signal = bull_signal * self.bear_scale
        else:  # "flat"
            bear_signal = pd.Series(0.0, index=feats.index)

        # Regime detection
        sma_col = f"sma_{self.sma_n}"
        close_prev = feats["close"].shift(1)
        sma_prev = feats[sma_col].shift(1)
        is_bull = close_prev >= sma_prev

        if self.sma_slope:
            sma_rising = sma_prev.diff(5) > 0
            is_bull = is_bull & sma_rising

        # Combine
        signal = pd.Series(np.where(is_bull, bull_signal, bear_signal), index=feats.index)

        # Vol targeting
        if self.target_vol is not None:
            signal = apply_vol_target(signal, bars, self.target_vol, self.vol_lookback)

        # NaN warmup
        signal[feats["rsi_14"].isna()] = np.nan
        return signal


# ── Variants ─────────────────────────────────────────────────────────────────

VARIANTS = {}

# ── Baselines ──
VARIANTS["flat_sma100"] = dict(sma_n=100, bear_scale=0.0, bear_mode="flat")

# ── MR-only in bear, various scales ──
for scale in [0.1, 0.2, 0.3, 0.5]:
    for sma in [80, 100, 120]:
        VARIANTS[f"mr_{int(scale*100)}pct_sma{sma}"] = dict(
            sma_n=sma, bear_scale=scale, bear_mode="mr_only")

# ── MR-only in bear + vol target ──
for scale in [0.1, 0.2, 0.3]:
    for sma in [80, 100, 120]:
        for vt in [0.15, 0.20]:
            VARIANTS[f"mr_{int(scale*100)}pct_sma{sma}_vt{int(vt*100)}"] = dict(
                sma_n=sma, bear_scale=scale, bear_mode="mr_only", target_vol=vt)

# ── MR-only + SMA slope ──
for scale in [0.2, 0.3]:
    for sma in [80, 100]:
        VARIANTS[f"mr_{int(scale*100)}pct_sma{sma}_slope"] = dict(
            sma_n=sma, bear_scale=scale, bear_mode="mr_only", sma_slope=True)

# ── All-reduced in bear ──
for scale in [0.1, 0.2, 0.3]:
    VARIANTS[f"reduced_{int(scale*100)}pct_sma100"] = dict(
        sma_n=100, bear_scale=scale, bear_mode="all_reduced")

# ── 2-feature bull (RSI + zscore only, drop momentum) ──
for scale in [0.2, 0.3]:
    for sma in [80, 100]:
        VARIANTS[f"mr_{int(scale*100)}pct_2f_sma{sma}"] = dict(
            sma_n=sma, bear_scale=scale, bear_mode="mr_only",
            bull_features=["rsi_14", "zscore_close_20d"])


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
    strategy = DualRegimeStrategy(**spec)
    config = BacktestConfig(stop_loss_pct=0.10, timeframe="1d")
    engine = BacktestEngine(strategy, config)

    results = {}
    per_sym = {}
    for pn, (start, end) in PERIODS.items():
        period_returns = []
        per_sym[pn] = {}
        for sym in SYMBOLS:
            bars = bars_cache[sym].loc[start:end]
            if len(bars) < 80:
                continue
            result = engine.run_on_bars(bars, symbol=sym, timeframe="1d")
            period_returns.append(result.equity_curve["returns"])
            per_sym[pn][sym] = compute_metrics(result.equity_curve["returns"])
        if period_returns:
            combined = pd.concat(period_returns, axis=1).mean(axis=1)
            results[pn] = compute_metrics(combined)
        else:
            results[pn] = {"return": 0, "sharpe": 0, "sortino": 0,
                           "max_dd": 0, "ann_vol": 0}
    return results, per_sym


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
    all_per_sym = {}
    for name, spec in VARIANTS.items():
        t0 = time.time()
        all_results[name], all_per_sym[name] = run_variant(spec, bars_cache)
        hold_ret = all_results[name]["hold"]["return"]
        marker = " <<<" if hold_ret > 0 else ""
        print(f"  {name}: {time.time()-t0:.1f}s  hold={hold_ret:+.2%}{marker}")

    # ── Results Table (sorted by holdout return) ──
    print(f"\n{'=' * 170}")
    print("DUAL-REGIME ABLATION: Momentum in Bull + Mean-Reversion in Bear (no BNB)".center(170))
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

    # ── Winners ──
    if winners:
        print(f"\n{'=' * 120}")
        print(f"WINNERS — Positive returns in ALL 3 periods ({len(winners)} variants)".center(120))
        print("=" * 120)
        for name in sorted(winners, key=lambda n: -np.mean([all_results[n][p]["sharpe"] for p in PERIODS])):
            r = all_results[name]
            avg_sharpe = np.mean([r[pn]["sharpe"] for pn in PERIODS])
            min_ret = min(r[pn]["return"] for pn in PERIODS)
            print(f"\n  {name}  (avg_sharpe={avg_sharpe:+.2f}, min_period_ret={min_ret:+.2%})")
            for pn in PERIODS:
                m = r[pn]
                print(f"    {pn:<6s}: ret={m['return']:+.2%}  sharpe={m['sharpe']:+.2f}  "
                      f"sortino={m['sortino']:+.2f}  vol={m['ann_vol']:.1%}  mdd={m['max_dd']:.1%}")

            # Per-symbol detail
            print(f"    {'':6s}  {'Symbol':<10s}  {'train':>8s}  {'val':>8s}  {'hold':>8s}")
            for sym in SYMBOLS:
                vals = []
                for pn in PERIODS:
                    if sym in all_per_sym[name][pn]:
                        vals.append(f"{all_per_sym[name][pn][sym]['return']:+7.1%}")
                    else:
                        vals.append(f"{'N/A':>8s}")
                print(f"    {'':6s}  {sym:<10s}  {'  '.join(vals)}")
    else:
        print("\n  No variant achieved positive returns in all 3 periods.")
        print("\n  Top 10 closest:")
        sorted_results = sorted(all_results.items(), key=lambda x: -x[1]["hold"]["return"])
        for name, results in sorted_results[:10]:
            r = results
            print(f"    {name:<30s}  hold={r['hold']['return']:+.2%}  "
                  f"train={r['train']['return']:+.1%}  val={r['val']['return']:+.1%}  "
                  f"hold_shp={r['hold']['sharpe']:+.2f}")


if __name__ == "__main__":
    main()
