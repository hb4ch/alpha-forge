"""Materialize bar cache parquets for given symbols and timeframes.

Wraps ``pegasus.data.provider.DataProvider.materialize`` so we can populate
the bar cache for new timeframes without one-off scripts.

Usage:
    uv run python scripts/warm_cache.py --symbols BTCUSDT --timeframes 4h
    uv run python scripts/warm_cache.py --symbols BTCUSDT,ETHUSDT --timeframes 4h,1h --start 2023-03-01 --end 2026-03-01
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from pegasus.config import BacktestConfig
from pegasus.data.provider import DataProvider


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", required=True, help="Comma-separated symbol list, e.g. BTCUSDT,ETHUSDT")
    p.add_argument("--timeframes", required=True, help="Comma-separated timeframe list, e.g. 4h,1h,1d")
    p.add_argument("--start", default="2023-03-01", help="ISO start date (default: 2023-03-01)")
    p.add_argument("--end", default="2026-03-01", help="ISO end date (default: 2026-03-01)")
    p.add_argument("--force", action="store_true", help="Re-materialize even if cache file exists")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [tf.strip() for tf in args.timeframes.split(",") if tf.strip()]

    cfg = BacktestConfig()
    dp = DataProvider(cfg)
    cache_dir = cfg.bar_cache_dir

    for symbol in symbols:
        for tf in timeframes:
            cache_path = Path(cache_dir) / f"{symbol}_{tf}.parquet"
            if cache_path.exists() and not args.force:
                print(f"[skip] {cache_path} exists (pass --force to rebuild)")
                continue
            print(f"[build] {symbol} {tf} {args.start} → {args.end}")
            t0 = time.time()
            path = dp.materialize(symbol, args.start, args.end, timeframe=tf)
            elapsed = time.time() - t0
            size_mb = Path(path).stat().st_size / 1024 / 1024
            print(f"[done]  {path} ({size_mb:.1f} MB, {elapsed:.1f}s)")


if __name__ == "__main__":
    main()
