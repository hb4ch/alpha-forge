#!/usr/bin/env python3
"""Fork eth_exhibits_institutional_momentum_over_weekly_ho_v1 → v2.

v1 was the only DONE family with a passing holdout (Sharpe 0.38). v2 adds an
on-chain conviction overlay (chain TVL + lido protocol TVL + funding rate
extremes) to operationalize the seed mechanism's "DeFi/staking narrative
holders" claim, addressing the v1 result-judge MUST_FIX on sub-period
instability.

This script also parks the currently-active divergence v3 family so the
orchestrator picks v2 as the next active family.
"""
from __future__ import annotations

import logging

from alpha_forge.app.domain.states import FamilyState
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.seed_flow import fork_family

PARENT = "eth_exhibits_institutional_momentum_over_weekly_ho_v1"
ACTIVE_TO_PARK = "divergences_at_price_extremes_indicate_fading_inst_v3"
WORKSPACE = "alpha_research"

FORK_REASON = (
    "v1 holdout passed (Sharpe 0.38, +4.3% return) but result-judge flagged "
    "sub-period instability (Sharpe range -1.08 to +1.31) and an open "
    "MUST_FIX for regime-aware features. The v1 mechanism explicitly cites "
    "'DeFi / staking / L2 narrative holders' as the source of the "
    "institutional momentum edge, but v1 had no on-chain signals to "
    "operationalize that claim. The newly-ETL'd alt-data (Ethereum chain "
    "TVL, lido protocol TVL on Ethereum, ETH USD-M funding rate) provides "
    "exactly those signals.\n\n"
    "v2 mandate: KEEP the v1 baseline (RSI-14 + 20d momentum + 20d z-score "
    "+ trend gate + vol target) UNCHANGED. ADD a multiplicative "
    "ecosystem-conviction overlay applied AFTER the v1 signal:\n"
    "  signal_v2 = signal_v1 * ecosystem_conviction * funding_dampener\n"
    "where:\n"
    "  ecosystem_conviction = 0.5 + 0.25*eth_tvl_healthy + 0.25*lido_tvl_healthy "
    "(in {0.50, 0.75, 1.00})\n"
    "  funding_dampener = clip(1 - 0.5*max(0, |funding_z| - 1.5), 0, 1) "
    "(1.0 when |z|<=1.5, ramps to 0 at |z|>=3.5)\n"
    "  eth_tvl_healthy  = (Ethereum chain TVL > 30d SMA).astype(float)\n"
    "  lido_tvl_healthy = (Lido protocol TVL on Ethereum > 30d SMA).astype(float)\n"
    "  funding_z = 30d rolling z-score of ETH 8h funding rate\n\n"
    "Implementation pattern (causal alignment):\n"
    "  series.reindex(bars.index, method='ffill').shift(1)\n"
    "applied to each alt-data series BEFORE rolling stats. This is the "
    "documented MultiSourceProvider recipe in CLAUDE.md.\n\n"
    "Conviction weighting (NOT a binary regime ON/OFF) — preserves trade "
    "frequency, attenuates risk, doesn't introduce a hard regime "
    "classifier the overfit judge will flag as rescue. Three new lookbacks "
    "all pinned to the same 30d window. Multiplier weights and funding "
    "thresholds chosen a priori (no optimization).\n\n"
    "Hard constraints:\n"
    "  - Do NOT modify the v1 features.py existing logic. Only ADD the "
    "three alt-data columns.\n"
    "  - Do NOT add a binary regime ON/OFF switch (regime rescue pattern "
    "v1 lineage already caught).\n"
    "  - Do NOT widen universe (v1 LOO evidence is ETH-only: "
    "Sharpe +1.39 ETH vs +0.22 SOL vs -0.26 BTC).\n"
    "  - Use MultiSourceProvider with the documented forward-fill + "
    "shift(1) pattern. NO scipy/sklearn. Only pandas + numpy + the provider.\n\n"
    "Falsification: holdout Sharpe < v1's 0.38 OR sub-period Sharpe "
    "std > 0.5 => alt-data overlay added no risk-adjusted improvement, "
    "archive v2."
)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    store = MarkdownStore(WORKSPACE)
    artifact_store = ArtifactStore(WORKSPACE)

    # --- Park divergence v3 (currently active) ---
    v3 = store.read_family(ACTIVE_TO_PARK)
    if v3.state != FamilyState.DONE:
        v3 = v3.model_copy(update={"state": FamilyState.DONE})
        store.write_family(v3)
        store.append_history(
            ACTIVE_TO_PARK,
            "Family parked as DONE. 3 iterations, best score -0.066 at iter_3 "
            "(Sharpe +0.11, 33 trades, PF 1.66 — reproduced v1 iter_4's "
            "positive-Sharpe baseline using the simplified divergence). "
            "Continued exploration of crypto alpha shifts to ETH momentum + "
            "alt-data overlay in eth_exhibits_institutional_momentum lineage.",
        )
        print(f"Parked {ACTIVE_TO_PARK} as DONE.")

    # --- Fork v1 → v2 ---
    child = fork_family(PARENT, FORK_REASON, store, artifact_store, configs_dir="configs")
    print(f"\nForked: {child.family_id} (state={child.state})")
    print(f"  next_iteration_mode = {child.next_iteration_mode}")

    # --- Update STATE.md to point at v2 ---
    state_path = store.root / "STATE.md"
    new_state = (
        "---\n"
        f"active_family: {child.family_id}\n"
        "best_qualified_score: 0.0\n"
        "best_score: 0.0\n"
        "current_iteration: 0\n"
        "family_queue: []\n"
        "last_transition_at: '2026-04-26T15:00:00+00:00'\n"
        "state: QUEUED\n"
        "---\n"
        "\n"
        "# Global State\n"
    )
    state_path.write_text(new_state)
    print(f"\nSTATE.md updated → active_family: {child.family_id}")

    # --- Verify Fix 1 wired correctly ---
    iter0 = store.read_iteration(child.family_id)
    assert iter0 is not None and iter0.iteration_id == "iter_0"
    assert "## FORK MUTATION" in iter0.plan
    assert child.next_iteration_mode == "revise_code"
    print("\n[VERIFIED] Fix 1 active:")
    print("  - next_iteration_mode = 'revise_code'")
    print("  - iter_0 synthesized with FORK MUTATION header")

    # --- Verify v1 iter_1-success baseline preserved on disk ---
    feats = (store.root / "families" / child.family_id / "research" / "features.py").read_text()
    assert "rsi_14_norm" in feats or "rsi_14" in feats
    assert "mom_20" in feats or "mom_20_norm" in feats
    assert "MultiSourceProvider" not in feats  # researcher will ADD it in iter_1
    print("  - inherited features.py = v1 baseline (RSI/momentum/zscore, no alt-data yet)")

    print(f"\nReady to run: uv run python scripts/run_iteration.py --family {child.family_id}")


if __name__ == "__main__":
    main()
