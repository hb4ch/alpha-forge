#!/usr/bin/env python3
"""Data quality audit and alpha signal scanner.

Usage:
    python scripts/probe_data.py                  # full run
    python scripts/probe_data.py --quality-only    # data quality only
    python scripts/probe_data.py --alpha-only      # alpha scan only
    python scripts/probe_data.py --save            # save CSVs to reports/probe/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy import stats

# Allow importing pegasus from the sibling repo
sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "crypto-pegasus"))

from pegasus.config import BacktestConfig
from pegasus.data.provider import DataProvider

# ── Constants ────────────────────────────────────────────────────────────────

HORIZONS = [1, 2, 3, 5, 10, 20]
FEATURE_NAMES = [
    "buy_sell_zscore",
    "vwap_deviation",
    "volume_surprise",
    "trade_count_surprise",
    "price_volume_divergence",
    "spread_proxy",
    "close_location",
    "vwap_momentum",
    "buy_pressure_momentum",
]


# ── Config ───────────────────────────────────────────────────────────────────

def load_configs(configs_dir: str = "configs") -> tuple[dict, dict]:
    base = Path(configs_dir)
    with open(base / "splits.yaml") as f:
        splits = yaml.safe_load(f)
    with open(base / "universe.yaml") as f:
        universe = yaml.safe_load(f)
    return splits, universe


def make_provider() -> DataProvider:
    provider = DataProvider(BacktestConfig())
    provider.conn.execute("SET threads TO 16")
    provider.conn.execute("SET memory_limit = '30GB'")
    return provider


# ── Part 1: Data Quality ────────────────────────────────────────────────────

def audit_gaps(df: pd.DataFrame, tf_minutes: int = 5) -> pd.DataFrame:
    expected = pd.Timedelta(minutes=tf_minutes)
    tolerance = pd.Timedelta(seconds=30)
    diffs = df.index.to_series().diff()
    gaps = diffs[diffs > expected + tolerance]
    rows = []
    for end_time, delta in gaps.items():
        start_time = end_time - delta
        missing = int(delta / expected) - 1
        rows.append({"gap_start": start_time, "gap_end": end_time, "missing_bars": missing})
    return pd.DataFrame(rows).sort_values("missing_bars", ascending=False).reset_index(drop=True)


def audit_basic_stats(df: pd.DataFrame) -> pd.DataFrame:
    desc = df.describe().T[["mean", "std", "min", "max"]]
    desc["nan_count"] = df.isna().sum()
    return desc


def audit_anomalies(df: pd.DataFrame) -> dict:
    return {
        "zero_volume": int((df["volume"] == 0).sum()),
        "zero_trade_count": int((df["trade_count"] == 0).sum()),
        "high_lt_low": int((df["high"] < df["low"]).sum()),
        "close_gt_high": int((df["close"] > df["high"]).sum()),
        "close_lt_low": int((df["close"] < df["low"]).sum()),
    }


def audit_distributions(df: pd.DataFrame) -> dict:
    buy_ratio = df["buy_volume"] / df["volume"].clip(lower=1e-10)
    tc = df["trade_count"]
    return {
        "buy_ratio": buy_ratio.describe().to_dict(),
        "trade_count": tc.describe().to_dict(),
        "trade_count_q75": float(tc.quantile(0.75)),
    }


def audit_intraday(df: pd.DataFrame) -> pd.Series:
    return df.groupby(df.index.hour)["volume"].mean()


def run_quality_audit(provider: DataProvider, symbols: list[str], start: str, end: str) -> None:
    print("=" * 80)
    print("DATA QUALITY AUDIT".center(80))
    print("=" * 80)

    for sym in symbols:
        print(f"\n{'─' * 80}")
        print(f"  {sym}")
        print(f"{'─' * 80}")

        df = provider.get_bars(sym, start, end, "5min")
        if df.empty:
            print("  NO DATA")
            continue

        # Expected bars
        total_minutes = (pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / 60
        expected_bars = int(total_minutes / 5)
        actual_bars = len(df)
        coverage = actual_bars / expected_bars * 100

        print(f"  Bars: {actual_bars:,} / {expected_bars:,} expected ({coverage:.2f}%)")
        print(f"  Range: {df.index.min()} to {df.index.max()}")

        # Gaps
        gaps = audit_gaps(df)
        print(f"  Gaps: {len(gaps)} total, {int(gaps['missing_bars'].sum()) if len(gaps) else 0} bars missing")
        if len(gaps):
            print("  Top gaps:")
            for _, g in gaps.head(5).iterrows():
                print(f"    {g['gap_start']} -> {g['gap_end']}  ({g['missing_bars']} bars)")

        # Stats
        print("\n  Column Stats:")
        st = audit_basic_stats(df)
        print(f"  {'':>24s}  {'mean':>14s}  {'std':>14s}  {'min':>14s}  {'max':>14s}  {'NaN':>6s}")
        for col in st.index:
            r = st.loc[col]
            print(f"  {col:>24s}  {r['mean']:>14.4f}  {r['std']:>14.4f}  {r['min']:>14.4f}  {r['max']:>14.4f}  {int(r['nan_count']):>6d}")

        # Anomalies
        anom = audit_anomalies(df)
        issues = {k: v for k, v in anom.items() if v > 0}
        if issues:
            print(f"\n  Anomalies: {issues}")
        else:
            print("\n  Anomalies: none")

        # Distributions
        dist = audit_distributions(df)
        br = dist["buy_ratio"]
        print(f"\n  Buy/Volume Ratio:  mean={br['mean']:.4f}  std={br['std']:.4f}  "
              f"25%={br['25%']:.4f}  50%={br['50%']:.4f}  75%={br['75%']:.4f}")
        print(f"  Trade Count Q75: {dist['trade_count_q75']:.0f}")

        # Intraday
        hourly = audit_intraday(df)
        peak_hour = hourly.idxmax()
        trough_hour = hourly.idxmin()
        print(f"\n  Volume by Hour:  peak={peak_hour:02d}:00 ({hourly[peak_hour]:,.0f})  "
              f"trough={trough_hour:02d}:00 ({hourly[trough_hour]:,.0f})  "
              f"ratio={hourly[peak_hour]/hourly[trough_hour]:.1f}x")

        # Free memory
        del df


# ── Part 2: Alpha Probing ───────────────────────────────────────────────────

def compute_features(df: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    feats = pd.DataFrame(index=df.index)

    buy_ratio = df["buy_volume"] / df["volume"].clip(lower=1e-10)

    # 1. Buy/sell z-score
    rm = buy_ratio.rolling(lookback).mean()
    rs = buy_ratio.rolling(lookback).std()
    feats["buy_sell_zscore"] = ((buy_ratio - rm) / rs.clip(lower=1e-8)).shift(1)

    # 2. VWAP deviation
    feats["vwap_deviation"] = ((df["close"] - df["vwap"]) / df["close"]).shift(1)

    # 3. Volume surprise
    vol_rm = df["volume"].rolling(lookback).mean()
    feats["volume_surprise"] = (df["volume"] / vol_rm.clip(lower=1e-8)).shift(1)

    # 4. Trade count surprise
    tc_rm = df["trade_count"].rolling(lookback).mean()
    feats["trade_count_surprise"] = (df["trade_count"] / tc_rm.clip(lower=1e-8)).shift(1)

    # 5. Price-volume divergence
    price_dir = np.sign(df["close"].pct_change())
    vol_dir = np.sign(df["volume"].pct_change())
    feats["price_volume_divergence"] = (price_dir * vol_dir).shift(1)

    # 6. Spread proxy (intra-bar volatility)
    feats["spread_proxy"] = ((df["high"] - df["low"]) / df["close"]).shift(1)

    # 7. Close location in bar
    bar_range = df["high"] - df["low"]
    feats["close_location"] = ((df["close"] - df["low"]) / bar_range.clip(lower=1e-10)).shift(1)

    # 8. VWAP momentum
    feats["vwap_momentum"] = df["vwap"].pct_change(lookback).shift(1)

    # 9. Buy pressure momentum
    feats["buy_pressure_momentum"] = buy_ratio.diff(lookback).shift(1)

    return feats


def compute_forward_returns(df: pd.DataFrame, horizons: list[int] | None = None) -> pd.DataFrame:
    if horizons is None:
        horizons = HORIZONS
    fwd = pd.DataFrame(index=df.index)
    close = df["close"]
    for h in horizons:
        fwd[f"fwd_{h}"] = close.pct_change(h).shift(-h)
    return fwd


def compute_ic_table(
    features: pd.DataFrame,
    fwd_rets: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    ic = pd.DataFrame(index=features.columns, columns=fwd_rets.columns, dtype=float)
    pvals = pd.DataFrame(index=features.columns, columns=fwd_rets.columns, dtype=float)

    for feat in features.columns:
        for col in fwd_rets.columns:
            mask = features[feat].notna() & fwd_rets[col].notna()
            if mask.sum() < 100:
                ic.loc[feat, col] = np.nan
                pvals.loc[feat, col] = np.nan
                continue
            corr, p = stats.spearmanr(features[feat][mask], fwd_rets[col][mask])
            ic.loc[feat, col] = corr
            pvals.loc[feat, col] = p

    return ic, pvals


def compute_quintile_returns(
    feature: pd.Series,
    fwd_ret: pd.Series,
    n: int = 5,
) -> pd.Series | None:
    mask = feature.notna() & fwd_ret.notna()
    f = feature[mask]
    r = fwd_ret[mask]

    if f.nunique() < n:
        return None

    try:
        bins = pd.qcut(f, n, labels=False, duplicates="drop")
    except ValueError:
        return None

    return r.groupby(bins).mean()


def is_monotonic(series: pd.Series) -> str:
    if series is None or len(series) < 3:
        return "N/A"
    vals = series.dropna().values
    rank_corr = np.corrcoef(np.arange(len(vals)), vals)[0, 1]
    if rank_corr > 0.8:
        return "YES+"
    if rank_corr < -0.8:
        return "YES-"
    return "no"


def run_alpha_probe(
    provider: DataProvider,
    symbols: list[str],
    train_start: str,
    train_end: str,
    lookback: int = 20,
    save_dir: Path | None = None,
) -> None:
    print("\n" + "=" * 80)
    print("ALPHA SIGNAL SCAN (TRAIN ONLY)".center(80))
    print("=" * 80)

    all_ic: dict[str, pd.DataFrame] = {}

    for sym in symbols:
        print(f"\n{'─' * 80}")
        print(f"  {sym}  |  {train_start} to {train_end}")
        print(f"{'─' * 80}")

        df = provider.get_bars(sym, train_start, train_end, "5min")
        if df.empty:
            print("  NO DATA")
            continue

        feats = compute_features(df, lookback)
        fwd = compute_forward_returns(df)
        ic, pvals = compute_ic_table(feats, fwd)
        all_ic[sym] = ic

        # IC Table
        fwd_cols = [f"fwd_{h}" for h in HORIZONS]
        hdr = f"  {'Feature':>28s}" + "".join(f"  {c:>8s}" for c in fwd_cols)
        print(f"\n  IC Table (Spearman):")
        print(hdr)
        for feat in feats.columns:
            row = f"  {feat:>28s}"
            for col in fwd_cols:
                v = ic.loc[feat, col]
                p = pvals.loc[feat, col]
                star = "*" if (p is not None and not np.isnan(p) and p < 0.01) else " "
                row += f"  {v:>7.4f}{star}" if not np.isnan(v) else f"  {'N/A':>8s}"
            print(row)
        print("  (* p < 0.01)")

        # Quintile returns for fwd_5
        print(f"\n  Quintile Mean Returns (fwd_5):")
        print(f"  {'Feature':>28s}  {'Q1':>8s}  {'Q2':>8s}  {'Q3':>8s}  {'Q4':>8s}  {'Q5':>8s}  Mono?")
        for feat in feats.columns:
            qr = compute_quintile_returns(feats[feat], fwd["fwd_5"])
            if qr is None:
                print(f"  {feat:>28s}  {'(insufficient unique values)':>48s}")
                continue
            mono = is_monotonic(qr)
            row = f"  {feat:>28s}"
            for q in range(len(qr)):
                row += f"  {qr.iloc[q]:>8.5f}"
            # Pad if fewer than 5 bins
            for _ in range(5 - len(qr)):
                row += f"  {'---':>8s}"
            row += f"  {mono:>5s}"
            print(row)

        # Turnover
        print(f"\n  Signal Turnover:")
        for feat in feats.columns:
            tv = feats[feat].diff().abs().mean()
            print(f"    {feat:>28s}: {tv:.4f}")

        if save_dir:
            save_dir.mkdir(parents=True, exist_ok=True)
            ic.to_csv(save_dir / f"ic_{sym}.csv")
            feats.describe().to_csv(save_dir / f"feature_stats_{sym}.csv")

        del df, feats, fwd

    # Cross-symbol summary
    if len(all_ic) > 1:
        print(f"\n{'=' * 80}")
        print("CROSS-SYMBOL SUMMARY (fwd_5 IC)".center(80))
        print(f"{'=' * 80}")

        syms = list(all_ic.keys())
        hdr = f"  {'Feature':>28s}" + "".join(f"  {s:>8s}" for s in syms) + f"  {'Avg':>8s}  {'#>0.01':>6s}"
        print(hdr)

        for feat in FEATURE_NAMES:
            vals = []
            row = f"  {feat:>28s}"
            for s in syms:
                v = all_ic[s].loc[feat, "fwd_5"] if feat in all_ic[s].index else np.nan
                vals.append(v)
                row += f"  {v:>8.4f}" if not np.isnan(v) else f"  {'N/A':>8s}"
            avg = np.nanmean(vals)
            count_sig = sum(1 for v in vals if not np.isnan(v) and abs(v) > 0.01)
            row += f"  {avg:>8.4f}  {count_sig:>6d}"
            print(row)

    if save_dir:
        print(f"\n  Results saved to {save_dir}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Data quality audit + alpha signal scanner")
    parser.add_argument("--quality-only", action="store_true", help="Run quality audit only")
    parser.add_argument("--alpha-only", action="store_true", help="Run alpha scan only")
    parser.add_argument("--lookback", type=int, default=20, help="Rolling window for features")
    parser.add_argument("--save", action="store_true", help="Save CSVs to reports/probe/")
    parser.add_argument("--configs-dir", default="configs", help="Path to configs directory")
    args = parser.parse_args()

    splits, universe = load_configs(args.configs_dir)
    symbols = universe["symbols"]
    train_start = splits["train"]["start"]
    train_end = splits["train"]["end"]
    full_start = splits["train"]["start"]
    full_end = splits["validation"]["end"]

    provider = make_provider()

    try:
        if not args.alpha_only:
            run_quality_audit(provider, symbols, full_start, full_end)

        if not args.quality_only:
            save_dir = Path("alpha_research/reports/probe") if args.save else None
            run_alpha_probe(provider, symbols, train_start, train_end, args.lookback, save_dir)
    finally:
        provider.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
