#!/usr/bin/env python3
"""Ablation: tighten trailing stop. One change only.

v3 established that relaxed thresholds give a more honest read (7 val trades
vs 3) with preserved Sharpe. But holdout is stuck at +0.08 regardless of
entry criteria. The bottleneck may be exits — the 2.5×ATR trailing stop is
wide enough to let multi-bar trends fully retrace before exiting.

This fork tests ONE change: trailing stop 2.5×ATR → 1.5×ATR.
Everything else is v3 (relaxed thresholds, ETH-only, long+short, Pattern B).

If 1.5×ATR is too tight (cuts winners short), try 2.0×ATR in v5.
"""
from __future__ import annotations

import logging

from alpha_forge.app.domain.states import FamilyState
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.seed_flow import fork_family


PARENT = "volatility_compression_atrclose_and_40-bar_high-lo_v3"
WORKSPACE = "alpha_research"

FORK_REASON = (
    "Ablation: tighten trailing stop from 2.5×ATR to 1.5×ATR.\n"
    "\n"
    "v3 established that relaxed entry thresholds improve validation trade "
    "count (3→7) without degrading Sharpe (+1.56→+1.34). But holdout remained "
    "stuck at Sharpe +0.08 regardless. The 2.5×ATR trailing stop (~2.5-3.75% "
    "on 4h ETH) may be the bottleneck — wide enough to let trends fully retrace "
    "before exiting.\n"
    "\n"
    "This is a single-variable ablation. Only the trailing stop multiplier "
    "changes. Everything else is frozen at v3 values:\n"
    "  - Relaxed entry thresholds (atr_pct<0.35, range_pct<0.40, slope_pct>0.50)\n"
    "  - ETH-only universe\n"
    "  - Pattern B exits (init stop 2×ATR, range re-entry)\n"
    "  - Long+short\n"
    "  - Sentinel engine stops at 0.30\n"
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

    # Park v3
    parent = store.read_family(PARENT)
    if parent.state != FamilyState.ARCHIVED_REJECTED:
        parent = parent.model_copy(update={"state": FamilyState.ARCHIVED_REJECTED})
        store.write_family(parent)
    print(f"{PARENT} state: {parent.state}")

    # Fork
    child = fork_family(PARENT, FORK_REASON, store, artifact_store, configs_dir="configs")
    print(f"Forked: {child.family_id}")

    research = store.root / "families" / child.family_id / "research"

    # --- SINGLE CHANGE: trailing stop 2.5 → 1.5 ---
    sc_path = research / "signal_combiner.py"
    sc_text = sc_path.read_text()

    # Long trailing stop
    assert "trailing_extreme - 2.5 * atr_i" in sc_text, "long trail pattern not found"
    sc_text = sc_text.replace(
        "trailing_extreme - 2.5 * atr_i",
        "trailing_extreme - 1.5 * atr_i",
    )

    # Short trailing stop
    assert "trailing_extreme + 2.5 * atr_i" in sc_text, "short trail pattern not found"
    sc_text = sc_text.replace(
        "trailing_extreme + 2.5 * atr_i",
        "trailing_extreme + 1.5 * atr_i",
    )

    sc_path.write_text(sc_text)
    print("Patched signal_combiner.py: trailing stop 2.5×ATR → 1.5×ATR (both sides)")

    # Verify
    assert "2.5" not in sc_path.read_text(), "old 2.5 multiplier still present"
    assert "1.5 * atr_i" in sc_path.read_text(), "new 1.5 multiplier not found"
    print("[VERIFIED] Single-variable ablation applied")

    # Update STATE.md
    state_path = store.root / "STATE.md"
    state_path.write_text(
        "---\n"
        f"active_family: {child.family_id}\n"
        "best_qualified_score: 0.0\n"
        "best_score: 0.0\n"
        "current_iteration: 0\n"
        "family_queue: []\n"
        "last_transition_at: '2026-05-09T20:30:00+00:00'\n"
        "state: QUEUED\n"
        "---\n\n"
        "# Global State\n"
    )
    print(f"STATE.md → active_family: {child.family_id}")
    print(f"\nReady: uv run python scripts/preflight_via_engine.py --family {child.family_id}")


if __name__ == "__main__":
    main()
