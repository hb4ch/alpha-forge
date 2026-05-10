#!/usr/bin/env python3
"""Fork v6→v7: add ADX trend-strength regime filter.

v6 achieved holdout +0.38 (ETH-only) but has a negative sub-period Sharpe
window (-0.45). The strategy fires into choppy/mean-reverting regimes and
gives back gains. A regime filter that gates entries on trend strength
should eliminate low-quality setups and close that negative window.

The tradeoff is fewer trades, but the remaining ones should be higher
quality since they only fire in trending environments.

Single-variable ablation: add ADX > threshold as an entry condition.
Everything else frozen at v6.
"""
from __future__ import annotations

import logging

from alpha_forge.app.domain.states import FamilyState
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.seed_flow import fork_family


PARENT = "volatility_compression_atrclose_and_40-bar_high-lo_v6"
WORKSPACE = "alpha_research"

FORK_REASON = (
    "Single-variable ablation: add ADX trend-strength regime filter to entry "
    "conditions.\n"
    "\n"
    "v6 ETH-only holdout Sharpe +0.38 is the best in the family line, but the "
    "strategy still has a -0.45 sub-period Sharpe window. The compression-"
    "breakout mechanism fires into all regimes indiscriminately — during choppy "
    "or mean-reverting periods, breakouts tend to fail and the strategy gives "
    "back gains.\n"
    "\n"
    "This fork adds a simple regime filter: require ADX(14) > 25 at entry. "
    "ADX measures trend strength independent of direction — values above 25 "
    "indicate a trending market where breakouts are more likely to follow "
    "through. Below 25 is choppy/range-bound where compression breakouts "
    "tend to be false signals.\n"
    "\n"
    "Frozen from v6: ETH-only, relaxed ATR threshold (0.35), slope percentile "
    "(0.80), conviction scaling, 2.0×ATR stops, range re-entry exit, 5% hard "
    "stop, Pattern B.\n"
    "\n"
    "Falsification: validation Sharpe < +0.05 after 20bps costs → archive.\n"
    "Promotion: validation Sharpe ≥ +0.4 AND all sub-period windows > 0."
)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    store = MarkdownStore(WORKSPACE)
    artifact_store = ArtifactStore(WORKSPACE)

    child = fork_family(PARENT, FORK_REASON, store, artifact_store, configs_dir="configs")
    print(f"Forked: {child.family_id}")

    research = store.root / "families" / child.family_id / "research"

    # --- Edit features.py: add ADX computation ---
    ft_path = research / "features.py"
    ft_text = ft_path.read_text()

    # Add ADX(14) computation after ATR block
    adx_block = """
    # ADX(14) — trend strength indicator for regime filter
    # Uses Wilder's smoothing like ATR
    high = df['high']
    low = df['low']
    close = df['close']
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr_14_raw = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    # Wilder's smoothing
    plus_di_14 = 100 * (plus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr_14_raw)
    minus_di_14 = 100 * (minus_dm.ewm(alpha=1/14, min_periods=14, adjust=False).mean() / atr_14_raw)
    dx = 100 * (plus_di_14 - minus_di_14).abs() / (plus_di_14 + minus_di_14)
    df['adx_14'] = dx.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
"""

    # Insert ADX block before the return statement
    if "adx_14" not in ft_text:
        ft_text = ft_text.replace(
            "return df",
            adx_block + "\n    return df"
        )
        ft_path.write_text(ft_text)
        print("Added ADX(14) computation to features.py")
    else:
        print("ADX already in features.py, skipping")

    # --- Edit signal_combiner.py: add ADX gate ---
    sc_path = research / "signal_combiner.py"
    sc_text = sc_path.read_text()

    # Add ADX config and feature reading
    sc_text = sc_text.replace(
        "    atr_thresh = config.get('atr_compression_threshold', 0.35)",
        "    atr_thresh = config.get('atr_compression_threshold', 0.35)\n"
        "    adx_threshold = config.get('adx_threshold', 25.0)"
    )
    sc_text = sc_text.replace(
        "    atr_pct = features['atr_pct_500']\n"
        "    slope_norm = features['slope_norm']\n"
        "    slope_pct = features['slope_pct_500']",
        "    atr_pct = features['atr_pct_500']\n"
        "    slope_norm = features['slope_norm']\n"
        "    slope_pct = features['slope_pct_500']\n"
        "    adx = features.get('adx_14', pd.Series(25.0, index=features.index))"
    )

    # Add regime filter to entry conditions
    sc_text = sc_text.replace(
        "    # Entry conditions\n"
        "    compression = atr_pct < atr_thresh",
        "    # Entry conditions\n"
        "    trending = adx > adx_threshold  # regime filter: require trending market\n"
        "    compression = (atr_pct < atr_thresh) & trending"
    )

    sc_path.write_text(sc_text)
    print("Added ADX regime filter to signal_combiner.py")

    # --- Edit model_config.py: add adx_threshold ---
    mc_path = research / "model_config.py"
    mc_text = mc_path.read_text()

    mc_text = mc_text.replace(
        '    "atr_compression_threshold": 0.35,      # atr_pct must be < this for compression',
        '    "atr_compression_threshold": 0.35,      # atr_pct must be < this for compression\n'
        '    "adx_threshold": 25.0,                  # ADX(14) must be > this (trending regime gate)'
    )

    mc_path.write_text(mc_text)
    print("Added adx_threshold to model_config.py")

    # --- Update STATE.md ---
    state_path = store.root / "STATE.md"
    state_path.write_text(
        "---\n"
        f"active_family: {child.family_id}\n"
        "best_qualified_score: 0.0\n"
        "best_score: 0.0\n"
        "current_iteration: 0\n"
        "family_queue: []\n"
        "last_transition_at: '2026-05-09T21:30:00+00:00'\n"
        "state: QUEUED\n"
        "---\n\n"
        "# Global State\n"
    )

    print(f"STATE.md → active_family: {child.family_id}")
    print(f"Ready: uv run python scripts/preflight_via_engine.py --family {child.family_id}")
    print(f"Then:  uv run python scripts/run_iteration.py --family {child.family_id}")


if __name__ == "__main__":
    main()
