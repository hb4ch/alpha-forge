#!/usr/bin/env python3
"""Ablation: remove range re-entry exit condition.

v4 optimized the trailing stop to 2.0×ATR, improving holdout Sharpe from
+0.08 → +0.11. The next exit condition to test is the range re-entry exit:
  ci < breakout_high (for longs) / ci > breakout_low (for shorts)

This exits when price falls back below the 40-bar high it broke out above —
a "failed breakout" condition. But price often retests breakout levels
before continuing. Removing this may let more trades survive to maturity.

Single-variable ablation. Everything else frozen at v4 values:
  - Relaxed entry thresholds (atr_pct<0.35, range_pct<0.40, slope_pct>0.50)
  - ETH-only, long+short, Pattern B
  - Init stop 2×ATR, trailing stop 2.0×ATR
  - Sentinel engine stops at 0.30
"""
from __future__ import annotations

import logging

from alpha_forge.app.domain.states import FamilyState
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.seed_flow import fork_family


PARENT = "volatility_compression_atrclose_and_40-bar_high-lo_v4"
WORKSPACE = "alpha_research"

FORK_REASON = (
    "Ablation: remove range re-entry exit condition.\n"
    "\n"
    "v4 optimized the trailing stop (2.5→2.0×ATR), improving holdout Sharpe "
    "from +0.08 to +0.11. The next exit condition to test is the range re-entry "
    "exit: ci < breakout_high (longs) / ci > breakout_low (shorts).\n"
    "\n"
    "This exits when price falls back inside the 40-bar range it broke out of — "
    "a 'failed breakout' condition. But price frequently retests breakout levels "
    "before continuing. Removing this exit lets trades survive retests and rely "
    "solely on the trailing stop for exit timing.\n"
    "\n"
    "Single-variable ablation. Everything else frozen at v4 values.\n"
    "\n"
    "Falsification: holdout Sharpe < +0.05 → archive.\n"
    "Promotion: holdout Sharpe ≥ +0.4."
)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    store = MarkdownStore(WORKSPACE)
    artifact_store = ArtifactStore(WORKSPACE)

    # Park v4
    parent = store.read_family(PARENT)
    if parent.state != FamilyState.ARCHIVED_REJECTED:
        parent = parent.model_copy(update={"state": FamilyState.ARCHIVED_REJECTED})
        store.write_family(parent)
    print(f"{PARENT} state: {parent.state}")

    # Fork
    child = fork_family(PARENT, FORK_REASON, store, artifact_store, configs_dir="configs")
    print(f"Forked: {child.family_id}")

    research = store.root / "families" / child.family_id / "research"

    # --- Fix features.py (remove sma50 if present from checkpoint) ---
    ft_path = research / "features.py"
    ft_text = ft_path.read_text()
    if "sma50" in ft_text or "sma_50" in ft_text:
        # Rewrite to clean version
        ft_path.write_text("""\"\"\"L3 compression + breakout + slope. v5: ablation — no range re-entry exit.
Pinned parameters: ATR_20, range_40, slope_10, percentile_lookback=500.
\"\"\"
import pandas as pd
import numpy as np


def compute_features(bars: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = bars['open'], bars['high'], bars['low'], bars['close']

    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr_20 = tr.rolling(20).mean()

    atr_norm = (atr_20 / c).shift(1)
    range_40 = ((h.rolling(40).max() - l.rolling(40).min()) / c).shift(1)

    def causal_pct(series, window):
        return series.rolling(window).apply(
            lambda w: (w < w.iloc[-1]).mean() if not np.isnan(w.iloc[-1]) else np.nan,
            raw=False,
        )

    atr_pct = causal_pct(atr_norm, 500)
    range_pct = causal_pct(range_40, 500)

    high_40_prior = h.rolling(40).max().shift(1)
    low_40_prior = l.rolling(40).min().shift(1)

    def slope_func(window):
        x = np.arange(len(window))
        y = window.values if hasattr(window, 'values') else window
        if np.any(np.isnan(y)):
            return np.nan
        n = len(y)
        x_mean = x.mean(); y_mean = y.mean()
        num = ((x - x_mean) * (y - y_mean)).sum()
        den = ((x - x_mean) ** 2).sum()
        return num / den if den > 0 else np.nan

    slope_10 = c.shift(1).rolling(10).apply(slope_func, raw=True)
    slope_norm = slope_10 / atr_20.shift(1)
    slope_pct = causal_pct(slope_norm, 500)

    return pd.DataFrame({
        'close': c,
        'high_40_prior': high_40_prior,
        'low_40_prior': low_40_prior,
        'atr_20': atr_20,
        'atr_pct_500': atr_pct,
        'range_pct_500': range_pct,
        'slope_norm': slope_norm,
        'slope_pct_500': slope_pct,
    }, index=bars.index)
""")
        print("Fixed features.py (removed sma50 from checkpoint)")
    else:
        print("features.py already clean")

    # --- SINGLE CHANGE: remove range re-entry exit ---
    sc_path = research / "signal_combiner.py"
    sc_text = sc_path.read_text()

    # Remove long range re-entry exit
    old_long = """                if init_stop is not None and ci < init_stop:
                    in_long = False
                elif trail_stop is not None and ci < trail_stop:
                    in_long = False
                elif breakout_high is not None and ci < breakout_high:
                    in_long = False"""
    new_long = """                if init_stop is not None and ci < init_stop:
                    in_long = False
                elif trail_stop is not None and ci < trail_stop:
                    in_long = False"""

    assert old_long in sc_text, "long range re-entry pattern not found"
    sc_text = sc_text.replace(old_long, new_long)

    # Remove short range re-entry exit
    old_short = """                if init_stop is not None and ci > init_stop:
                    in_short = False
                elif trail_stop is not None and ci > trail_stop:
                    in_short = False
                elif breakout_low is not None and ci > breakout_low:
                    in_short = False"""
    new_short = """                if init_stop is not None and ci > init_stop:
                    in_short = False
                elif trail_stop is not None and ci > trail_stop:
                    in_short = False"""

    assert old_short in sc_text, "short range re-entry pattern not found"
    sc_text = sc_text.replace(old_short, new_short)

    # Update docstring
    sc_text = sc_text.replace(
        "Exit (Pattern B): init stop (2×ATR), trailing stop (2.5×ATR), range re-entry.",
        "Exit (Pattern B): init stop (2×ATR), trailing stop (2.0×ATR). Range re-entry REMOVED."
    )
    sc_text = sc_text.replace(
        "- Init stop 2×ATR, range re-entry preserved",
        "- Init stop 2×ATR, range re-entry REMOVED"
    )

    sc_path.write_text(sc_text)

    # Verify
    sc_verify = sc_path.read_text()
    assert "breakout_high" not in sc_verify or "ci < breakout_high" not in sc_verify, \
        "range re-entry exit still present for longs"
    assert "ci > breakout_low" not in sc_verify, \
        "range re-entry exit still present for shorts"
    print("[VERIFIED] Range re-entry exit removed (both sides)")

    # Also fix model_config to ETH-only if needed
    mc_path = research / "model_config.py"
    mc_text = mc_path.read_text()
    if "SOLUSDT" in mc_text:
        mc_text = mc_text.replace(
            '"universe": ["ETHUSDT", "SOLUSDT"]',
            '"universe": ["ETHUSDT"]'
        )
        mc_path.write_text(mc_text)
        print("Fixed model_config.py: ETH-only")
    else:
        print("model_config.py already ETH-only")

    # Update STATE.md
    state_path = store.root / "STATE.md"
    state_path.write_text(
        "---\n"
        f"active_family: {child.family_id}\n"
        "best_qualified_score: 0.0\n"
        "best_score: 0.0\n"
        "current_iteration: 0\n"
        "family_queue: []\n"
        "last_transition_at: '2026-05-09T21:00:00+00:00'\n"
        "state: QUEUED\n"
        "---\n\n"
        "# Global State\n"
    )
    print(f"STATE.md → active_family: {child.family_id}")
    print(f"\nReady: uv run python scripts/preflight_via_engine.py --family {child.family_id}")


if __name__ == "__main__":
    main()
