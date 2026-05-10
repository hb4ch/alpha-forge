#!/usr/bin/env python3
"""Fork v6→v8: relax entry thresholds to increase trade count.

The overfit judge on v7 correctly identified that this mechanism's core
problem is low trade frequency (4-8 trades/period). Adding filters makes
it worse. This fork goes the other direction: relax the two tightest
entry gates to let more setups through.

Changes:
  - atr_compression_threshold: 0.35 → 0.50 (more bars qualify as compressed)
  - abs_slope_percentile: 0.80 → 0.60 (less extreme slope required)

Everything else frozen from v6: ETH-only, conviction scaling, 2.0×ATR stops,
range re-entry exit, 5% hard stop, Pattern B.
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
    "Relax two entry thresholds to increase trade count:\n"
    "  1. atr_compression_threshold: 0.35 → 0.50\n"
    "  2. abs_slope_percentile: 0.80 → 0.60\n"
    "\n"
    "Seven versions of this mechanism all produce 4-8 validation trades per "
    "period — too few for statistical significance or meaningful sub-period "
    "stability measurement. Adding filters (v3 trend, v7 ADX) reduced trade "
    "count further without closing negative Sharpe windows.\n"
    "\n"
    "This fork tests the inverse: relax entry gates to increase sample size. "
    "If the edge survives at 15-20+ trades, sub-period stability becomes "
    "measurable and the mechanism is validated. If the edge disappears at "
    "higher trade frequency, the mechanism was always just overfitting to "
    "rare extreme events.\n"
    "\n"
    "Frozen from v6: ETH-only, conviction scaling, 2.0×ATR stops, range "
    "re-entry exit, 5% hard stop, Pattern B.\n"
    "\n"
    "Falsification: validation Sharpe < +0.05 after 20bps costs → archive.\n"
    "Promotion: validation Sharpe ≥ +0.4 with ≥ 15 trades."
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

    # Fix model_config.py
    mc_path = research / "model_config.py"
    mc_text = mc_path.read_text()
    mc_text = mc_text.replace(
        '"atr_compression_threshold": 0.35',
        '"atr_compression_threshold": 0.50'
    )
    mc_text = mc_text.replace(
        '"abs_slope_percentile": 0.80',
        '"abs_slope_percentile": 0.60'
    )
    mc_path.write_text(mc_text)
    print("Updated model_config.py: atr 0.50, slope 0.60")

    # Also update defaults in signal_combiner.py to match
    sc_path = research / "signal_combiner.py"
    sc_text = sc_path.read_text()
    sc_text = sc_text.replace(
        "config.get('atr_compression_threshold', 0.35)",
        "config.get('atr_compression_threshold', 0.50)"
    )
    sc_text = sc_text.replace(
        "config.get('abs_slope_percentile', 0.80)",
        "config.get('abs_slope_percentile', 0.60)"
    )
    sc_path.write_text(sc_text)
    print("Updated signal_combiner.py defaults")

    # Ensure ETH-only
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
        "last_transition_at: '2026-05-09T22:30:00+00:00'\n"
        "state: QUEUED\n"
        "---\n\n"
        "# Global State\n"
    )

    print(f"STATE.md → active_family: {child.family_id}")
    print(f"Ready: uv run python scripts/preflight_via_engine.py --family {child.family_id}")


if __name__ == "__main__":
    main()
