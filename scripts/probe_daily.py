#!/usr/bin/env python3
"""Enhanced daily bar alpha probe with 3-period stability analysis.

Probes 28 features on daily bars across train/validation/holdout periods.
Reports IC stability, feature correlations, and ranking for multi-factor use.

Usage:
    python scripts/probe_daily.py                  # full run
    python scripts/probe_daily.py --save           # save CSVs
    python scripts/probe_daily.py --horizons 5 10 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "crypto-pegasus"))

from pegasus.config import BacktestConfig
from pegasus.data.provider import DataProvider

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_HORIZONS = [1, 3, 5, 10, 20]

FEATURE_GROUPS = {
    "Mean-Reversion": [
        "zscore_close_20d", "zscore_close_60d", "bollinger_pct",
        "rsi_14", "close_vs_high_20d", "range_position_20d",
    ],
    "Momentum": [
        "ret_1d", "ret_5d", "ret_10d", "ret_20d",
        "trend_strength_20d", "highs_count_20d",
    ],
    "Volume/Activity": [
        "trade_intensity", "volume_ma_ratio", "volume_trend",
        "turnover_intensity", "volume_price_corr_10d",
    ],
    "Microstructure": [
        "buy_pressure", "buy_pressure_zscore", "spread_proxy", "vwap_deviation",
    ],
    "Volatility": [
        "realized_vol_5d", "realized_vol_20d", "vol_ratio", "atr_ratio",
    ],
    "Interactions": [
        "momentum_volume", "activity_volatility", "mean_rev_vol",
    ],
}

ALL_FEATURES = [f for group in FEATURE_GROUPS.values() for f in group]


# ── Config ───────────────────────────────────────────────────────────────────

def load_configs(configs_dir: str = "configs") -> tuple[dict, dict]:
    base = Path(configs_dir)
    with open(base / "splits.yaml") as f:
        splits = yaml.safe_load(f)
    with open(base / "universe.yaml") as f:
        universe = yaml.safe_load(f)
    return splits, universe


# ── Feature Computation ──────────────────────────────────────────────────────

def compute_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute 28 daily features. All shifted by 1 bar to prevent lookahead."""
    df = bars
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    tc = df["trade_count"]
    bv = df["buy_volume"]
    vwap = df["vwap"]

    feats = pd.DataFrame(index=df.index)

    # ── Mean-Reversion ──
    sma20 = close.rolling(20, min_periods=20).mean()
    std20 = close.rolling(20, min_periods=20).std()
    sma60 = close.rolling(60, min_periods=60).mean()
    std60 = close.rolling(60, min_periods=60).std()

    feats["zscore_close_20d"] = ((close - sma20) / std20.clip(lower=1e-10)).shift(1)
    feats["zscore_close_60d"] = ((close - sma60) / std60.clip(lower=1e-10)).shift(1)
    feats["bollinger_pct"] = ((close - sma20) / (2 * std20).clip(lower=1e-10)).shift(1)

    # RSI-14
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=13, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(com=13, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.clip(lower=1e-10)
    rsi = 100 - 100 / (1 + rs)
    feats["rsi_14"] = ((rsi - 50) / 50).shift(1)

    feats["close_vs_high_20d"] = (
        (close - high.rolling(20, min_periods=20).max()) / close
    ).shift(1)

    range_20_high = high.rolling(20, min_periods=20).max()
    range_20_low = low.rolling(20, min_periods=20).min()
    feats["range_position_20d"] = (
        (close - range_20_low) / (range_20_high - range_20_low).clip(lower=1e-10)
    ).shift(1)

    # ── Momentum ──
    feats["ret_1d"] = close.pct_change(1).shift(1)
    feats["ret_5d"] = close.pct_change(5).shift(1)
    feats["ret_10d"] = close.pct_change(10).shift(1)
    feats["ret_20d"] = close.pct_change(20).shift(1)

    abs_daily_ret = close.pct_change().abs()
    net_ret_20 = close.pct_change(20).abs()
    path_sum_20 = abs_daily_ret.rolling(20, min_periods=20).sum()
    feats["trend_strength_20d"] = (net_ret_20 / path_sum_20.clip(lower=1e-10)).shift(1)

    up_days = (close.diff() > 0).astype(float)
    feats["highs_count_20d"] = up_days.rolling(20, min_periods=20).mean().shift(1)

    # ── Volume/Activity ──
    tc_ma20 = tc.rolling(20, min_periods=20).mean()
    vol_ma20 = volume.rolling(20, min_periods=20).mean()

    feats["trade_intensity"] = (tc / tc_ma20.clip(lower=1)).shift(1)
    feats["volume_ma_ratio"] = (volume / vol_ma20.clip(lower=1e-10)).shift(1)
    feats["volume_trend"] = (
        volume.rolling(5, min_periods=5).mean()
        / vol_ma20.clip(lower=1e-10)
    ).shift(1)
    feats["turnover_intensity"] = (
        (tc / tc_ma20.clip(lower=1)) * (volume / vol_ma20.clip(lower=1e-10))
    ).pow(0.5).shift(1)

    ret_daily = close.pct_change()
    vol_daily = volume.pct_change()
    feats["volume_price_corr_10d"] = ret_daily.rolling(10, min_periods=10).corr(vol_daily).shift(1)

    # ── Microstructure ──
    buy_ratio = bv / volume.clip(lower=1e-10)
    feats["buy_pressure"] = buy_ratio.shift(1)
    bp_ma = buy_ratio.rolling(20, min_periods=20).mean()
    bp_std = buy_ratio.rolling(20, min_periods=20).std()
    feats["buy_pressure_zscore"] = ((buy_ratio - bp_ma) / bp_std.clip(lower=1e-8)).shift(1)
    feats["spread_proxy"] = ((high - low) / close).shift(1)
    feats["vwap_deviation"] = ((close - vwap) / close).shift(1)

    # ── Volatility ──
    feats["realized_vol_5d"] = ret_daily.rolling(5, min_periods=5).std().shift(1)
    feats["realized_vol_20d"] = ret_daily.rolling(20, min_periods=20).std().shift(1)
    feats["vol_ratio"] = (
        feats["realized_vol_5d"] / feats["realized_vol_20d"].clip(lower=1e-10)
    )
    # Note: vol_ratio uses already-shifted components, no extra shift needed

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr5 = tr.rolling(5, min_periods=5).mean()
    atr20 = tr.rolling(20, min_periods=20).mean()
    feats["atr_ratio"] = (atr5 / atr20.clip(lower=1e-10)).shift(1)

    # ── Interactions ──
    feats["momentum_volume"] = feats["ret_5d"] * feats["volume_ma_ratio"]
    feats["activity_volatility"] = feats["trade_intensity"] * feats["vol_ratio"]
    feats["mean_rev_vol"] = feats["zscore_close_20d"] * feats["realized_vol_5d"]

    return feats


def compute_forward_returns(
    bars: pd.DataFrame, horizons: list[int] | None = None,
) -> pd.DataFrame:
    if horizons is None:
        horizons = DEFAULT_HORIZONS
    fwd = pd.DataFrame(index=bars.index)
    close = bars["close"]
    for h in horizons:
        fwd[f"fwd_{h}"] = close.pct_change(h).shift(-h)
    return fwd


# ── IC Analysis ──────────────────────────────────────────────────────────────

def compute_ic_table(
    features: pd.DataFrame, fwd_rets: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    ic = pd.DataFrame(index=features.columns, columns=fwd_rets.columns, dtype=float)
    pvals = pd.DataFrame(index=features.columns, columns=fwd_rets.columns, dtype=float)
    for feat in features.columns:
        for col in fwd_rets.columns:
            mask = features[feat].notna() & fwd_rets[col].notna()
            if mask.sum() < 50:
                ic.loc[feat, col] = np.nan
                pvals.loc[feat, col] = np.nan
                continue
            corr, p = stats.spearmanr(features[feat][mask], fwd_rets[col][mask])
            ic.loc[feat, col] = corr
            pvals.loc[feat, col] = p
    return ic, pvals


def stability_flag(ic_train: float, ic_val: float, ic_hold: float) -> str:
    """Classify IC stability across 3 periods."""
    vals = [ic_train, ic_val, ic_hold]
    if any(np.isnan(v) for v in vals):
        return "N/A"
    signs = [np.sign(v) for v in vals]
    abs_vals = [abs(v) for v in vals]

    # Check for sign flips
    flips = []
    if signs[1] != signs[0] and signs[0] != 0 and signs[1] != 0:
        flips.append("val")
    if signs[2] != signs[0] and signs[0] != 0 and signs[2] != 0:
        flips.append("hold")
    if len(flips) == 2:
        return "FLIP(v+h)"
    if flips:
        return f"FLIP({flips[0]})"

    # Check magnitude
    strong = sum(1 for v in abs_vals if v > 0.02)
    if strong < 2:
        return "WEAK"

    return "YES"


def stability_multiplier(flag: str) -> float:
    if flag == "YES":
        return 1.0
    if flag.startswith("FLIP") and "v+h" not in flag:
        return 0.5
    if flag == "WEAK":
        return 0.7
    return 0.0


# ── Correlation ──────────────────────────────────────────────────────────────

def compute_feature_correlations(
    all_features: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Average Spearman correlation across symbols (using all available data)."""
    corr_mats = []
    for sym, feats in all_features.items():
        corr_mats.append(feats.corr(method="spearman"))
    return pd.concat(corr_mats).groupby(level=0).mean().reindex(
        index=ALL_FEATURES, columns=ALL_FEATURES
    )


# ── Main Probe ───────────────────────────────────────────────────────────────

def run_probe(
    bars_cache: dict[str, pd.DataFrame],
    splits: dict,
    symbols: list[str],
    horizons: list[int],
    save_dir: Path | None = None,
) -> None:
    periods = {
        "train": (splits["train"]["start"], splits["train"]["end"]),
        "val": (splits["validation"]["start"], splits["validation"]["end"]),
        "hold": (splits["holdout"]["start"], splits["holdout"]["end"]),
    }

    # Compute features and IC for all symbols x periods
    # ic_data[period][symbol] = ic DataFrame
    ic_data: dict[str, dict[str, pd.DataFrame]] = {}
    all_train_features: dict[str, pd.DataFrame] = {}

    for period_name, (start, end) in periods.items():
        ic_data[period_name] = {}
        for sym in symbols:
            bars = bars_cache[sym].loc[start:end]
            if len(bars) < 80:
                print(f"  SKIP {sym} {period_name}: only {len(bars)} bars")
                continue

            feats = compute_features(bars)
            fwd = compute_forward_returns(bars, horizons)
            ic, pvals = compute_ic_table(feats, fwd)
            ic_data[period_name][sym] = ic

            if period_name == "train":
                all_train_features[sym] = feats

            if save_dir:
                save_dir.mkdir(parents=True, exist_ok=True)
                ic.to_csv(save_dir / f"ic_{sym}_{period_name}.csv")

    fwd_cols = [f"fwd_{h}" for h in horizons]
    ref_horizon = f"fwd_{horizons[-1]}"  # longest horizon for ranking

    # ── Section 1: Cross-Symbol IC Summary (3 periods) ──
    print("\n" + "=" * 120)
    print("CROSS-SYMBOL IC SUMMARY (averaged across symbols)".center(120))
    print("=" * 120)

    # Compute avg IC across symbols per period
    avg_ic: dict[str, pd.DataFrame] = {}
    for period_name in periods:
        if not ic_data[period_name]:
            continue
        frames = list(ic_data[period_name].values())
        avg_ic[period_name] = pd.concat(frames).groupby(level=0).mean()

    for h_col in fwd_cols:
        print(f"\n  ── {h_col} ──")
        hdr = f"  {'Feature':<28s}"
        for pn in periods:
            hdr += f"  {pn:>8s}"
        hdr += f"  {'Stable?':>10s}"
        print(hdr)
        print("  " + "-" * (28 + 8 * len(periods) + 12 + len(periods) * 2))

        for group_name, group_feats in FEATURE_GROUPS.items():
            print(f"  [{group_name}]")
            for feat in group_feats:
                row = f"    {feat:<26s}"
                vals = []
                for pn in periods:
                    if pn in avg_ic and feat in avg_ic[pn].index:
                        v = avg_ic[pn].loc[feat, h_col]
                        vals.append(v)
                        row += f"  {v:>+7.4f}" if not np.isnan(v) else f"  {'N/A':>7s}"
                    else:
                        vals.append(np.nan)
                        row += f"  {'N/A':>7s}"

                flag = stability_flag(*vals) if len(vals) == 3 else "N/A"
                row += f"  {flag:>10s}"

                # Alert on suspiciously high IC
                if any(abs(v) > 0.3 for v in vals if not np.isnan(v)):
                    row += "  ⚠ IC>0.3"

                print(row)

    # ── Section 2: Per-Symbol Detail (longest horizon) ──
    print(f"\n\n{'=' * 120}")
    print(f"PER-SYMBOL IC DETAIL ({ref_horizon})".center(120))
    print("=" * 120)

    for period_name in periods:
        print(f"\n  ── {period_name} ──")
        syms_in = list(ic_data[period_name].keys())
        if not syms_in:
            print("    No data")
            continue
        hdr = f"  {'Feature':<28s}" + "".join(f"  {s:>8s}" for s in syms_in) + f"  {'Avg':>8s}  {'4/4?':>4s}"
        print(hdr)
        for feat in ALL_FEATURES:
            vals = []
            row = f"  {feat:<28s}"
            for s in syms_in:
                v = ic_data[period_name][s].loc[feat, ref_horizon] if feat in ic_data[period_name][s].index else np.nan
                vals.append(v)
                row += f"  {v:>+7.4f}" if not np.isnan(v) else f"  {'N/A':>7s}"
            avg = np.nanmean(vals)
            consistent = all(np.sign(v) == np.sign(vals[0]) for v in vals if not np.isnan(v))
            row += f"  {avg:>+7.4f}  {'✓' if consistent else '✗':>4s}"
            print(row)

    # ── Section 3: Feature Correlation Matrix ──
    print(f"\n\n{'=' * 120}")
    print("FEATURE CORRELATION MATRIX (train, |r| > 0.5 pairs)".center(120))
    print("=" * 120)

    if all_train_features:
        corr = compute_feature_correlations(all_train_features)
        # Print high-correlation pairs
        pairs = []
        for i, f1 in enumerate(ALL_FEATURES):
            for f2 in ALL_FEATURES[i + 1:]:
                if f1 in corr.index and f2 in corr.columns:
                    r = corr.loc[f1, f2]
                    if not np.isnan(r) and abs(r) > 0.5:
                        pairs.append((f1, f2, r))
        pairs.sort(key=lambda x: -abs(x[2]))

        if pairs:
            print(f"\n  {'Feature A':<28s}  {'Feature B':<28s}  {'Corr':>6s}")
            print("  " + "-" * 66)
            for f1, f2, r in pairs:
                print(f"  {f1:<28s}  {f2:<28s}  {r:>+5.3f}")
        else:
            print("  No pairs with |r| > 0.5")

        if save_dir:
            corr.to_csv(save_dir / "feature_correlations.csv")

    # ── Section 4: Feature Ranking ──
    print(f"\n\n{'=' * 120}")
    print(f"FEATURE RANKING (scored by IC stability at {ref_horizon})".center(120))
    print("=" * 120)

    rankings = []
    for feat in ALL_FEATURES:
        ic_vals = []
        for pn in periods:
            if pn in avg_ic and feat in avg_ic[pn].index:
                ic_vals.append(avg_ic[pn].loc[feat, ref_horizon])
            else:
                ic_vals.append(np.nan)

        if any(np.isnan(v) for v in ic_vals):
            continue

        flag = stability_flag(*ic_vals)
        mult = stability_multiplier(flag)
        avg_abs_ic = np.mean([abs(v) for v in ic_vals])
        score = avg_abs_ic * mult

        # Determine dominant sign
        dominant_sign = "+" if sum(np.sign(v) for v in ic_vals) > 0 else "-"

        rankings.append({
            "feature": feat,
            "ic_train": ic_vals[0],
            "ic_val": ic_vals[1],
            "ic_hold": ic_vals[2],
            "avg_abs_ic": avg_abs_ic,
            "flag": flag,
            "mult": mult,
            "score": score,
            "sign": dominant_sign,
        })

    rankings.sort(key=lambda x: -x["score"])

    print(f"\n  {'Rank':>4s}  {'Feature':<28s}  {'IC_trn':>7s}  {'IC_val':>7s}  {'IC_hld':>7s}  "
          f"{'AvgAbs':>7s}  {'Stable':>10s}  {'Score':>6s}  {'Sign':>4s}")
    print("  " + "-" * 100)

    for i, r in enumerate(rankings, 1):
        print(f"  {i:>4d}  {r['feature']:<28s}  {r['ic_train']:>+6.4f}  {r['ic_val']:>+6.4f}  "
              f"{r['ic_hold']:>+6.4f}  {r['avg_abs_ic']:>6.4f}  {r['flag']:>10s}  "
              f"{r['score']:>5.4f}  {r['sign']:>4s}")

    if save_dir:
        pd.DataFrame(rankings).to_csv(save_dir / "feature_ranking.csv", index=False)

    # ── Section 5: Recommended Features for Multi-Factor ──
    print(f"\n\n{'=' * 120}")
    print("RECOMMENDED FEATURES FOR MULTI-FACTOR".center(120))
    print("=" * 120)

    if all_train_features:
        corr = compute_feature_correlations(all_train_features)
        selected = []
        for r in rankings:
            if r["score"] < 0.01:
                continue
            # Check correlation with already-selected features
            too_correlated = False
            for sel in selected:
                if r["feature"] in corr.index and sel["feature"] in corr.columns:
                    c = corr.loc[r["feature"], sel["feature"]]
                    if not np.isnan(c) and abs(c) > 0.5:
                        too_correlated = True
                        break
            if not too_correlated:
                selected.append(r)

        # Determine category for each selected feature
        feat_to_group = {}
        for gname, gfeats in FEATURE_GROUPS.items():
            for f in gfeats:
                feat_to_group[f] = gname

        print(f"\n  Selected (greedy, max |corr| < 0.5 between any pair):\n")
        print(f"  {'#':>3s}  {'Feature':<28s}  {'Category':<18s}  {'Score':>6s}  "
              f"{'IC_trn':>7s}  {'IC_val':>7s}  {'IC_hld':>7s}  {'Sign':>4s}")
        print("  " + "-" * 100)
        for i, r in enumerate(selected, 1):
            cat = feat_to_group.get(r["feature"], "?")
            print(f"  {i:>3d}  {r['feature']:<28s}  {cat:<18s}  {r['score']:>5.4f}  "
                  f"{r['ic_train']:>+6.4f}  {r['ic_val']:>+6.4f}  {r['ic_hold']:>+6.4f}  "
                  f"{r['sign']:>4s}")

        categories_present = set(feat_to_group.get(r["feature"], "?") for r in selected)
        missing = set(FEATURE_GROUPS.keys()) - categories_present
        if missing:
            print(f"\n  ⚠ Missing categories: {', '.join(missing)}")
            print("  Consider adding the best feature from each missing category even if score is low.")


def main():
    parser = argparse.ArgumentParser(description="Enhanced daily bar alpha probe")
    parser.add_argument("--save", action="store_true", help="Save CSVs")
    parser.add_argument("--horizons", nargs="+", type=int, default=DEFAULT_HORIZONS,
                        help="Forward return horizons")
    parser.add_argument("--configs-dir", default="configs")
    args = parser.parse_args()

    splits, universe = load_configs(args.configs_dir)
    symbols = universe["symbols"]

    # Load all bars once for the full range
    full_start = splits["train"]["start"]
    full_end = splits["holdout"]["end"]

    print(f"Loading daily bars for {full_start} to {full_end}...")
    provider = DataProvider(BacktestConfig())
    bars_cache = {}
    for sym in symbols:
        bars_cache[sym] = provider.get_bars(sym, full_start, full_end, "1d")
        print(f"  {sym}: {len(bars_cache[sym])} bars", flush=True)
    provider.close()
    print()

    save_dir = Path("alpha_research/reports/probe_daily") if args.save else None

    run_probe(bars_cache, splits, symbols, args.horizons, save_dir)

    print("\nDone.")


if __name__ == "__main__":
    main()
