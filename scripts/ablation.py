"""Ablation study for activity surge long-only alpha.

Tests three improvements independently:
  A. Baseline (10% SL only)
  B. + Trailing stop (3%, 4%, 5%)
  C. + Buy volume filter (buy_volume/volume > 0.5)
  D. + Regime filter (suppress when price > 15% below 60d high)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "crypto-pegasus"))

import time
import numpy as np
import pandas as pd
import yaml

from pegasus.config import BacktestConfig
from pegasus.data.provider import DataProvider
from pegasus.engine.backtest import BacktestEngine
from pegasus.strategy.base import Strategy

# ---------------------------------------------------------------------------
# Configs
# ---------------------------------------------------------------------------
FAMILY_DIR = Path(__file__).resolve().parent.parent / (
    "alpha_research/families/"
    "elevated_trade_count_and_volume_jointly_signal_ins_v1/research"
)

with open(Path(__file__).resolve().parent.parent / "configs/splits.yaml") as f:
    SPLITS = yaml.safe_load(f)

VAL_START = SPLITS["validation"]["start"]
VAL_END = SPLITS["validation"]["end"]
SYMBOLS = ["ETHUSDT", "BTCUSDT", "SOLUSDT", "BNBUSDT"]

# ---------------------------------------------------------------------------
# Feature computation (shared across all variants)
# ---------------------------------------------------------------------------

def compute_features(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy()
    df["trade_intensity"] = (
        df["trade_count"]
        / df["trade_count"].rolling(20, min_periods=20).mean().clip(lower=1)
    ).shift(1)
    df["volume_ma_ratio"] = (
        df["volume"]
        / df["volume"].rolling(20, min_periods=20).mean().clip(lower=1e-10)
    ).shift(1)
    # Buy ratio for variant C
    df["buy_ratio"] = (df["buy_volume"] / df["volume"].clip(lower=1e-10)).shift(1)
    # 60d high for variant D
    df["high_60d"] = df["close"].rolling(60, min_periods=60).max().shift(1)
    df["drawdown_from_high"] = (df["close"].shift(1) - df["high_60d"]) / df["high_60d"]
    return df


def baseline_signal(features: pd.DataFrame) -> pd.Series:
    """A: Baseline — long-only activity surge."""
    ti = features["trade_intensity"]
    vmr = features["volume_ma_ratio"]
    activity = (ti * vmr).pow(0.5)
    signal = (activity - 1.0).clip(lower=0.0, upper=1.0)
    signal[ti.isna() | vmr.isna()] = np.nan
    return signal


def buy_volume_signal(features: pd.DataFrame) -> pd.Series:
    """C: Baseline + buy volume filter (only go long when taker flow is net buy)."""
    signal = baseline_signal(features)
    buy_ratio = features["buy_ratio"]
    # Suppress signal when buy ratio <= 0.5 (more selling than buying)
    signal = signal.where(buy_ratio > 0.5, 0.0)
    return signal


def regime_filter_signal(features: pd.DataFrame) -> pd.Series:
    """D: Baseline + regime filter (suppress when >15% below 60d high)."""
    signal = baseline_signal(features)
    dd = features["drawdown_from_high"]
    # Suppress signal during deep drawdowns
    signal = signal.where(dd > -0.15, 0.0)
    return signal


# ---------------------------------------------------------------------------
# Strategy wrappers
# ---------------------------------------------------------------------------

class AblationStrategy(Strategy):
    def __init__(self, signal_fn):
        super().__init__()
        self._signal_fn = signal_fn

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        features = compute_features(bars)
        return self._signal_fn(features)


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------

VARIANTS = {}

# A: Baseline
VARIANTS["A_baseline"] = {
    "signal_fn": baseline_signal,
    "stop_loss_pct": 0.10,
    "trailing_stop_pct": None,
}

# B: Trailing stops (3%, 4%, 5%)
for pct in [3, 4, 5]:
    VARIANTS[f"B_trail_{pct}pct"] = {
        "signal_fn": baseline_signal,
        "stop_loss_pct": 0.10,
        "trailing_stop_pct": pct / 100,
    }

# C: Buy volume filter (with best trailing stop from B, or baseline if none helps)
VARIANTS["C_buy_vol_filter"] = {
    "signal_fn": buy_volume_signal,
    "stop_loss_pct": 0.10,
    "trailing_stop_pct": None,
}

# D: Regime filter
VARIANTS["D_regime_filter"] = {
    "signal_fn": regime_filter_signal,
    "stop_loss_pct": 0.10,
    "trailing_stop_pct": None,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_variant(name, spec, bars_cache):
    config = BacktestConfig(
        stop_loss_pct=spec["stop_loss_pct"],
        trailing_stop_pct=spec["trailing_stop_pct"],
        timeframe="1d",
    )
    strategy = AblationStrategy(spec["signal_fn"])
    engine = BacktestEngine(strategy, config)

    results = {}
    for sym in SYMBOLS:
        bars = bars_cache[sym]
        result = engine.run_on_bars(bars, symbol=sym, timeframe="1d")
        eq = result.equity_curve
        total_ret = eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1
        returns = eq["returns"].dropna()
        sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0
        max_dd = eq["drawdown"].min()
        n_trades = len(result.trades)
        results[sym] = {
            "return": total_ret,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "trades": n_trades,
        }
    return results


def main():
    print("Loading bars for validation period...")
    t0 = time.time()
    provider = DataProvider(BacktestConfig())
    bars_cache = {}
    for sym in SYMBOLS:
        bars_cache[sym] = provider.get_bars(sym, VAL_START, VAL_END, "1d")
        print(f"  {sym}: {len(bars_cache[sym])} bars")
    provider.close()
    print(f"  Loaded in {time.time() - t0:.1f}s\n")

    # Run all variants
    all_results = {}
    for name, spec in VARIANTS.items():
        t0 = time.time()
        all_results[name] = run_variant(name, spec, bars_cache)
        elapsed = time.time() - t0
        print(f"  {name}: {elapsed:.1f}s")

    # Print results table
    print("\n" + "=" * 100)
    print(f"{'Variant':<22} | {'ETH':>18} | {'BTC':>18} | {'SOL':>18} | {'BNB':>18} | {'Avg':>8}")
    print("-" * 100)

    for name, results in all_results.items():
        rets = [results[s]["return"] for s in SYMBOLS]
        avg = np.mean(rets)
        parts = []
        for s in SYMBOLS:
            r = results[s]
            parts.append(f"{r['return']:+6.1%} ({r['sharpe']:.2f})")
        print(f"{name:<22} | {' | '.join(parts)} | {avg:+6.1%}")

    # Drawdown table
    print("\n" + "=" * 100)
    print(f"{'Variant':<22} | {'ETH MaxDD':>10} | {'BTC MaxDD':>10} | {'SOL MaxDD':>10} | {'BNB MaxDD':>10}")
    print("-" * 100)

    for name, results in all_results.items():
        dds = [f"{results[s]['max_dd']:>9.1%}" for s in SYMBOLS]
        print(f"{name:<22} | {' | '.join(dds)}")

    # Trade count table
    print("\n" + "=" * 100)
    print(f"{'Variant':<22} | {'ETH #':>6} | {'BTC #':>6} | {'SOL #':>6} | {'BNB #':>6}")
    print("-" * 100)

    for name, results in all_results.items():
        trades = [f"{results[s]['trades']:>6}" for s in SYMBOLS]
        print(f"{name:<22} | {' | '.join(trades)}")


if __name__ == "__main__":
    main()
