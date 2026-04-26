#!/usr/bin/env python3
"""Park v2 (CODE_REVISION_REQUIRED deadlock state) and fork v1 → v3 with the
new harness fixes active. The fork should preserve v1's iter_4 baseline
(simplified divergence + the only positive-Sharpe iteration in the lineage)
because fork_family now sets next_iteration_mode='revise_code' and synthesizes
an iter_0 carrying the parent plan + fork rationale.
"""
from __future__ import annotations

import logging

from alpha_forge.app.domain.states import FamilyState
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.seed_flow import fork_family

V1 = "divergences_at_price_extremes_indicate_fading_inst_v1"
V2 = "divergences_at_price_extremes_indicate_fading_inst_v2"
WORKSPACE = "alpha_research"

FORK_REASON = (
    "Forking from v1 (which has the iter_4 baseline preserved on disk: "
    "Sharpe +0.14 / PF 1.84 / 34 trades, the only positive-Sharpe run across "
    "the v1+v2 lineage). v2 burned 7 iterations because the researcher in "
    "REPLAN mode rebuilt features.py from scratch with strict double-pivot "
    "matching, which collapsed trade count to 2-6. Harness fix #1 (revise_code "
    "default for forks) + the iter_4 baseline on disk make this lineage finally "
    "work. v3 mandate: keep the simplified single-pivot divergence "
    "(sample histogram value at price pivot index, NOT separate hist-pivot "
    "match), and tune cost-survivability via three model_config knobs only — "
    "stop_loss_pct, take_profit_pct, trailing_stop_pct. Do NOT modify "
    "features.py or signal_combiner.py unless explicitly forced by a guard "
    "violation. Do NOT re-introduce strict histogram-pivot matching (v1 iter_5 "
    "and v2 iter_1-7 both proved this destroys signal). Do NOT add regime "
    "filters (v1 iter_3 was correctly flagged as regime rescue)."
)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    store = MarkdownStore(WORKSPACE)
    artifact_store = ArtifactStore(WORKSPACE)

    # --- Park v2 ---
    v2_family = store.read_family(V2)
    if v2_family.state != FamilyState.DONE:
        v2_family = v2_family.model_copy(update={"state": FamilyState.DONE})
        store.write_family(v2_family)
        store.append_history(
            V2,
            "Family parked as DONE. 7 iterations, no positive Sharpe; best score "
            "-0.402 at iter_4 (6 trades). Stuck in CODE_REVISION_REQUIRED on iter_7. "
            "Continued exploration in v3 with harness fixes (revise_code fork mode, "
            "deadlock escape, BEST IN LINEAGE anchor) active.",
        )
        print(f"Parked {V2} as DONE.")
    else:
        print(f"{V2} was already DONE.")

    # --- Fork v1 → v3 ---
    child = fork_family(V1, FORK_REASON, store, artifact_store, configs_dir="configs")
    print(f"\nForked: {child.family_id} (state={child.state})")
    print(f"  next_iteration_mode = {child.next_iteration_mode}")

    # --- Update STATE.md to point at v3 ---
    state_path = store.root / "STATE.md"
    state_text = state_path.read_text()
    new_state = (
        "---\n"
        f"active_family: {child.family_id}\n"
        "best_qualified_score: 0.0\n"
        "best_score: 0.0\n"
        "current_iteration: 0\n"
        "family_queue: []\n"
        "last_transition_at: '2026-04-26T13:00:00+00:00'\n"
        "state: QUEUED\n"
        "---\n"
        "\n"
        "# Global State\n"
    )
    state_path.write_text(new_state)
    print(f"\nSTATE.md updated → active_family: {child.family_id}")

    # --- Verify Fix 1 worked: child has iter_0 with FORK MUTATION ---
    iter0 = store.read_iteration(child.family_id)
    assert iter0 is not None, "Fix 1 broken: child has no iter_0"
    assert iter0.iteration_id == "iter_0", f"Expected iter_0, got {iter0.iteration_id}"
    assert "## FORK MUTATION" in iter0.plan, "Fix 1 broken: iter_0 missing FORK MUTATION header"
    assert child.next_iteration_mode == "revise_code", (
        f"Fix 1 broken: next_iteration_mode={child.next_iteration_mode}"
    )
    print("\n[VERIFIED] Fix 1: fork preserves baseline")
    print(f"  - child.next_iteration_mode = 'revise_code'")
    print(f"  - synthetic iter_0 plan starts with: {iter0.plan[:100]!r}...")

    # --- Verify code on disk is iter_4 baseline ---
    child_features = (store.root / "families" / child.family_id / "research" / "features.py").read_text()
    assert "hist_pivot_low_confirmed" not in child_features, (
        "Fix 1 broken: child inherited strict-match version, not simplified iter_4"
    )
    print(f"  - child's features.py = iter_4 simplified baseline (no hist_pivot_low_confirmed)")


if __name__ == "__main__":
    main()
