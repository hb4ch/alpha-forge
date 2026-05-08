#!/usr/bin/env python3
"""Park compression_breakout v1 + fork v2 with the faithful-port baseline.

v1 carried 5 months of mid-flight harness changes (engine patterns docs,
new judge prompts, deadlock escape). Net result: code on disk works
(preflight ETH val Sharpe +1.56, all-3-splits-positive at 20bps), but
the family state is stuck in CODE_REVISION_REQUIRED with stale plan,
guard env-drift, and code_revision_count=2.

v2 inherits the faithful-port code via Fix #1 (fork copies parent
research dir + sets next_iteration_mode=revise_code + synthesizes
iter_0 with parent plan + fork rationale). Fresh repro metadata,
fresh judge prompts, clean state. Should run end-to-end this time.
"""
from __future__ import annotations

import logging

from alpha_forge.app.domain.states import FamilyState
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.seed_flow import fork_family


PARENT = "volatility_compression_atrclose_and_40-bar_high-lo_v1"
WORKSPACE = "alpha_research"

FORK_REASON = (
    "v1 hit a multi-stage harness-development blocker:\n"
    "  - Researcher's iter_1 implementation drifted from seed parameters "
    "(ATR_14 vs spec ATR_20, slope thresh 0.80 vs spec 0.60, percentile "
    "lookback 252 vs spec 500). Tier-2 approved internally-clean code; "
    "result-judge correctly flagged poor metrics. iter_1 backtest: ETH 1 "
    "trade, SOL 5 trades, both negative Sharpe.\n"
    "  - iter_2 attempted with same parameter drift. Code judge caught a "
    "signal-persistence bug.\n"
    "  - Manual faithful-port (matching seed spec exactly) produced engine-"
    "validated PASS via scripts/preflight_via_engine.py: ETH 4h "
    "train=+0.54 / val=+1.56 / holdout=+0.10, SOL 4h train=+0.53 / "
    "val=+0.36 / holdout=+0.49, all at 20bps RT cost.\n"
    "  - But the older judge prompts rejected the faithful port's Pattern B "
    "(stateful loop in signal_combiner, sentinel-wide engine stops). The "
    "judge prompts were updated mid-flight to recognize Pattern B; that "
    "left v1 with stale plan + env drift + repro-guard failures.\n"
    "\n"
    "v2 mandate (binding):\n"
    "  - PRESERVE the faithful-port code already in research/ on disk. The "
    "researcher MUST NOT rewrite features.py / signal_combiner.py from "
    "scratch. The code is empirically validated and the seed mechanism "
    "is correctly implemented.\n"
    "  - Pattern B is the chosen engine-integration pattern: stateful "
    "Python loop in signal_combiner tracking entry / init stop / trailing "
    "extreme / range re-entry exit. Constant signal magnitude (±0.5) "
    "while in position. Engine stops at sentinel 0.30. This is "
    "documented in CLAUDE.md and the researcher's code-write prompt.\n"
    "  - Universe: ETHUSDT + SOLUSDT (both pass preflight). Timeframe: 4h.\n"
    "  - If revisions are needed, they should target parameter tuning or "
    "small fixes, NOT structural rewrites of the entry/exit logic.\n"
    "\n"
    "Falsification: holdout Sharpe < +0.05 on both symbols → archive v2.\n"
    "Promotion threshold: holdout Sharpe ≥ +0.4 on at least one symbol."
)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    store = MarkdownStore(WORKSPACE)
    artifact_store = ArtifactStore(WORKSPACE)

    # --- Park v1 ---
    parent = store.read_family(PARENT)
    if parent.state != FamilyState.DONE:
        parent = parent.model_copy(update={"state": FamilyState.DONE})
        store.write_family(parent)
        store.append_history(
            PARENT,
            "Family parked as DONE. iter_1/iter_2 churned on parameter drift "
            "and judge prompt mismatches with Pattern B. Faithful-port code on "
            "disk validated via scripts/preflight_via_engine.py: ETH 4h val "
            "Sharpe +1.56, all-3-splits-positive at 20bps RT. Continued in "
            "fork v2 with that code as the inherited baseline.",
        )
        print(f"Parked {PARENT} as DONE.")
    else:
        print(f"{PARENT} already DONE.")

    # --- Fork v1 → v2 (Fix #1 preserves baseline + sets revise_code mode) ---
    child = fork_family(PARENT, FORK_REASON, store, artifact_store, configs_dir="configs")
    print(f"\nForked: {child.family_id}")
    print(f"  state = {child.state}")
    print(f"  next_iteration_mode = {child.next_iteration_mode}")

    # --- Update STATE.md ---
    state_path = store.root / "STATE.md"
    state_path.write_text(
        "---\n"
        f"active_family: {child.family_id}\n"
        "best_qualified_score: 0.0\n"
        "best_score: 0.0\n"
        "current_iteration: 0\n"
        "family_queue: []\n"
        "last_transition_at: '2026-05-08T18:00:00+00:00'\n"
        "state: QUEUED\n"
        "---\n\n"
        "# Global State\n"
    )
    print(f"\nSTATE.md → active_family: {child.family_id}")

    # --- Verify Fix #1 ---
    iter0 = store.read_iteration(child.family_id)
    assert iter0 is not None and iter0.iteration_id == "iter_0"
    assert "## FORK MUTATION" in iter0.plan
    assert child.next_iteration_mode == "revise_code"
    print("\n[VERIFIED] Harness fix #1 active: revise_code mode + iter_0 plan synthesized")

    # --- Verify faithful-port code preserved ---
    research = store.root / "families" / child.family_id / "research"
    sc_text = (research / "signal_combiner.py").read_text()
    mc_text = (research / "model_config.py").read_text()
    assert "in_long" in sc_text and "trailing_extreme" in sc_text, \
        "faithful-port stateful loop not preserved"
    assert "0.30" in mc_text, "sentinel-wide engine stops not preserved"
    print("[VERIFIED] Faithful-port baseline preserved in research/")
    print(f"\nReady to run: uv run python scripts/run_iteration.py --family {child.family_id}")


if __name__ == "__main__":
    main()
