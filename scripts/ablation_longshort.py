#!/usr/bin/env python3
"""Long-short regime ablation: long in bull, cautious short in bear.

The IC is positive in ALL 3 periods. In bear markets, RSI stays low → signal says
"don't buy" → for long-only this means flat. But the signal also correctly predicts
further drops. A small short position should capture this.

Key constraint: keep the short position small (max 30%) to limit risk.
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


def compute_features(bars: pd.DataFrame) -> pd.DataFrame:
    close = bars["close"]
    feats = pd.DataFrame(index=bars.index)

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
    # Allow both long and short: clip to [-max, +max]
    max_pos = 3.0
    adjusted = adjusted.clip(lower=-max_pos, upper=max_pos)
    adjusted[signal.isna()] = np.nan
    return adjusted


class LongShortStrategy(Strategy):
    def __init__(self, feature_names: list[str], sma_n: int = 100,
                 bear_short_scale: float = 0.2, target_vol: float | None = None,
                 vol_lookback: int = 20, sma_slope: bool = False,
                 regime_adaptive: bool = False):
        """
        bear_short_scale: max short position in bear regime (0.0-1.0).
            0.0 = go flat in bear, 0.3 = up to 30% short.
        """
        super().__init__()
        self.feature_names = feature_names
        self.sma_n = sma_n
        self.bear_short_scale = bear_short_scale
        self.target_vol = target_vol
        self.vol_lookback = vol_lookback
        self.sma_slope = sma_slope
        self.regime_adaptive = regime_adaptive

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        feats = compute_features(bars)
        n = len(self.feature_names)

        z_feats = pd.DataFrame(index=feats.index)
        for f in self.feature_names:
            z_feats[f] = zscore_normalize(feats[f])

        if self.regime_adaptive and n >= 3:
            ret_abs = bars["close"].pct_change().abs()
            net_20 = bars["close"].pct_change(20).abs()
            path_20 = ret_abs.rolling(20, min_periods=20).sum()
            ts = (net_20 / path_20.clip(lower=1e-10)).shift(1)
            ts_smooth = ts.rolling(20, min_periods=10).mean()
            ts_median = ts_smooth.rolling(60, min_periods=30).median()
            trending = ts_smooth > ts_median

            w_trend = np.array([0.30, 0.45, 0.25])
            w_chop = np.array([0.45, 0.15, 0.40])
            w_matrix = np.where(
                trending.values[:, None], w_trend[None, :], w_chop[None, :]
            )
            composite = (z_feats[self.feature_names].values * w_matrix).sum(axis=1)
            raw_signal = pd.Series(composite, index=feats.index)
        else:
            w = np.ones(n) / n
            raw_signal = (z_feats[self.feature_names] * w).sum(axis=1)

        sig_std = raw_signal.std()
        if sig_std > 1e-8:
            raw_signal = raw_signal / sig_std * 0.3

        # Regime detection
        sma_col = f"sma_{self.sma_n}"
        close_prev = feats["close"].shift(1)
        sma_prev = feats[sma_col].shift(1)
        is_bull = close_prev >= sma_prev

        if self.sma_slope:
            sma_rising = sma_prev.diff(5) > 0
            is_bull = is_bull & sma_rising

        # Bull regime: long-only [0, 1]
        bull_signal = raw_signal.clip(lower=0.0, upper=1.0)

        # Bear regime: short-only [-bear_short_scale, 0]
        # When signal is negative → short proportionally
        bear_signal = raw_signal.clip(lower=-1.0, upper=0.0) * self.bear_short_scale

        signal = pd.Series(
            np.where(is_bull, bull_signal, bear_signal),
            index=feats.index,
        )

        if self.target_vol is not None:
            signal = apply_vol_target(signal, bars, self.target_vol, self.vol_lookback)

        signal[feats[self.feature_names[0]].isna()] = np.nan
        return signal


FEATURES_3 = ["rsi_14", "ret_20d", "zscore_close_20d"]

VARIANTS = {}

# ── Baselines ──
VARIANTS["flat_sma100"] = dict(
    feature_names=FEATURES_3, sma_n=100, bear_short_scale=0.0)

# ── Short scale sweep ──
for short_scale in [0.05, 0.10, 0.15, 0.20, 0.30]:
    for sma in [80, 100, 120]:
        VARIANTS[f"short{int(short_scale*100)}pct_sma{sma}"] = dict(
            feature_names=FEATURES_3, sma_n=sma, bear_short_scale=short_scale)

# ── Short + vol target ──
for short_scale in [0.05, 0.10, 0.15, 0.20]:
    for sma in [100, 120]:
        for vt in [0.15, 0.20]:
            VARIANTS[f"short{int(short_scale*100)}pct_sma{sma}_vt{int(vt*100)}"] = dict(
                feature_names=FEATURES_3, sma_n=sma,
                bear_short_scale=short_scale, target_vol=vt)

# ── Short + SMA slope ──
for short_scale in [0.10, 0.20]:
    for sma in [80, 100]:
        VARIANTS[f"short{int(short_scale*100)}pct_sma{sma}_slope"] = dict(
            feature_names=FEATURES_3, sma_n=sma,
            bear_short_scale=short_scale, sma_slope=True)

# ── Regime-adaptive + short ──
for short_scale in [0.10, 0.15, 0.20]:
    for sma in [100, 120]:
        VARIANTS[f"regime_short{int(short_scale*100)}pct_sma{sma}"] = dict(
            feature_names=FEATURES_3, sma_n=sma,
            bear_short_scale=short_scale, regime_adaptive=True)
        VARIANTS[f"regime_short{int(short_scale*100)}pct_sma{sma}_vt15"] = dict(
            feature_names=FEATURES_3, sma_n=sma,
            bear_short_scale=short_scale, target_vol=0.15,
            regime_adaptive=True)


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


def run_variant(spec: dict, bars_cache: dict[str, pd.DataFrame]) -> tuple[dict, dict]:
    strategy = LongShortStrategy(**spec)
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

    # ── Results Table ──
    print(f"\n{'=' * 170}")
    print("LONG-SHORT REGIME ABLATION: Long in Bull + Short in Bear (no BNB)".center(170))
    print("=" * 170)

    hdr = f"{'Variant':<34s} |"
    for pn in PERIODS:
        hdr += f" {pn+'_ret':>9s} {pn+'_shp':>8s} {pn+'_vol':>8s} {pn+'_mdd':>8s} |"
    hdr += f" {'All3>0':>7s}"
    print(hdr)
    print("-" * 170)

    winners = []
    for name, results in sorted(all_results.items(), key=lambda x: -x[1]["hold"]["return"]):
        row = f"{name:<34s} |"
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
            print(f"    {name:<34s}  hold={r['hold']['return']:+.2%}  "
                  f"train={r['train']['return']:+.1%}  val={r['val']['return']:+.1%}  "
                  f"hold_shp={r['hold']['sharpe']:+.2f}")


if __name__ == "__main__":
    main()
