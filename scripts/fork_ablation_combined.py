#!/usr/bin/env python3
"""Combine two winning ablations: 2.0×ATR trail + drop range_pct condition.

Ablation results:
  1. Trail stop 2.5→2.0×ATR: holdout +0.10→+0.14 (+40%)
  2. Drop range_pct: holdout +0.14→+0.15, trades 5→7

Both changes are independently validated. This fork combines them.
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
    "Combine two independently-validated ablations:\n"
    "  1. Trail stop 2.5→2.0×ATR (holdout Sharpe +0.10→+0.14)\n"
    "  2. Drop range_pct condition (holdout Sharpe +0.14→+0.15, trades 5→7)\n"
    "\n"
    "Each change was tested in isolation and showed improvement. Combining them\n"
    "preserves the core mechanism (ATR compression breakout) while removing a\n"
    "redundant filter and optimizing exit timing.\n"
    "\n"
    "Frozen from v4: relaxed ATR threshold, ETH-only, long+short, Pattern B,\n"
    "init stop 2×ATR, range re-entry exit, sentinel engine stops.\n"
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

    child = fork_family(PARENT, FORK_REASON, store, artifact_store, configs_dir="configs")
    print(f"Forked: {child.family_id}")

    research = store.root / "families" / child.family_id / "research"

    # Fix features.py — remove sma50 if present from checkpoint
    ft_path = research / "features.py"
    ft_text = ft_path.read_text()
    if "sma50" in ft_text or "sma_50" in ft_text:
        # Remove sma50 lines
        lines = ft_text.split('\n')
        cleaned = [l for l in lines if 'sma50' not in l and 'sma_50' not in l and 'Trend filter' not in l]
        ft_path.write_text('\n'.join(cleaned))
        print("Cleaned sma50 from features.py")

    # Write clean signal_combiner.py from the v4 baseline with two edits
    sc_text = (research / "signal_combiner.py").read_text()
    # Edit 1: trail stop to 2.0
    sc_text = sc_text.replace('2.5 * atr_i', '2.0 * atr_i')
    sc_text = sc_text.replace('1.5 * atr_i', '2.0 * atr_i')
    # Edit 2: drop range_pct
    sc_text = sc_text.replace(
        'compression = (atr_pct < 0.35) & (range_pct < 0.40)',
        'compression = atr_pct < 0.35  # range_pct dropped per ablation'
    )
    (research / "signal_combiner.py").write_text(sc_text)

    # Ensure model_config is ETH-only
    mc_path = research / "model_config.py"
    mc_text = mc_path.read_text()
    if "SOLUSDT" in mc_text:
        mc_text = mc_text.replace('["ETHUSDT", "SOLUSDT"]', '["ETHUSDT"]')
        mc_path.write_text(mc_text)

    # Update STATE.md
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
