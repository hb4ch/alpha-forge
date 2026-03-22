"""Top-level orchestrator: drives families through the lifecycle.

Reads global state, picks the active family, drives iterations until
the family reaches a terminal or waiting state. Resumable from any state.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from alpha_forge.app.agents.llm_client import LLMClient
from alpha_forge.app.domain.states import FamilyState
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.family_flow import FamilyFlow
from alpha_forge.engine.holdout_runner import run_holdout

logger = logging.getLogger(__name__)

# States that need orchestrator action
ACTIONABLE_STATES = {
    FamilyState.QUEUED,
    FamilyState.PLAN_REVISION_REQUIRED,
    FamilyState.CODE_REVISION_REQUIRED,
    FamilyState.ITERATE,
    FamilyState.PROMOTE_TO_HOLDOUT,
    FamilyState.PROMOTE_TO_PAPER,
}

# States that are terminal or require human input
WAITING_STATES = {
    FamilyState.HUMAN_REVIEW,
    FamilyState.PAPER_FORWARD_RUNNING,
    FamilyState.CANCELLED_3_STRIKES,
    FamilyState.ARCHIVED_REJECTED,
    FamilyState.DONE,
}

MAX_ITERATIONS = 10


class Orchestrator:
    """Top-level family execution loop."""

    def __init__(
        self,
        store: MarkdownStore,
        artifact_store: ArtifactStore,
        configs_dir: str | Path = "configs",
        client: LLMClient | None = None,
        max_iterations: int = MAX_ITERATIONS,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.configs_dir = Path(configs_dir).resolve()
        self.client = client or LLMClient()
        self.max_iterations = max_iterations
        self.flow = FamilyFlow(store, artifact_store, configs_dir, self.client)

    def run(self, family_id: str | None = None) -> dict[str, Any]:
        """Run the orchestrator loop for a family.

        If no family_id is provided, reads the active family from STATE.md.

        Returns a summary dict of what happened.
        """
        if family_id is None:
            global_state = self.store.read_global_state()
            family_id = global_state.get("active_family")
            if not family_id:
                logger.info("No active family found in STATE.md")
                return {"status": "no_active_family"}

        family = self.store.read_family(family_id)
        logger.info("Orchestrator starting for family %s (state: %s)", family_id, family.state)

        iterations_run = 0
        results: list[dict[str, Any]] = []

        while iterations_run < self.max_iterations:
            family = self.store.read_family(family_id)

            if family.state.is_terminal:
                logger.info("Family %s reached terminal state: %s", family_id, family.state)
                break

            if family.state in WAITING_STATES:
                logger.info("Family %s in waiting state: %s", family_id, family.state)
                break

            if family.state == FamilyState.PROMOTE_TO_HOLDOUT:
                logger.info("Running holdout for family %s", family_id)
                result = self._run_holdout(family_id)
                results.append(result)
                iterations_run += 1
                continue

            if family.state == FamilyState.PROMOTE_TO_PAPER:
                logger.info("Paper-forward stage: awaiting manual confirmation")
                # Stub: transition to HUMAN_REVIEW
                from alpha_forge.app.domain.events import FamilyEvent
                from alpha_forge.app.workflow.transitions import TransitionEngine
                engine = TransitionEngine()
                tr = engine.apply(family, FamilyEvent.PROMOTE_PAPER)
                family = tr.family
                # Immediately mark as passed (stub)
                tr = engine.apply(family, FamilyEvent.PAPER_PASSED)
                family = tr.family
                self.store.write_family(family)
                results.append({"step": "paper_forward", "status": "stub_passed"})
                continue

            if family.state in (FamilyState.QUEUED, FamilyState.ITERATE,
                                FamilyState.PLAN_REVISION_REQUIRED,
                                FamilyState.CODE_REVISION_REQUIRED):
                logger.info("Running iteration %d for family %s", iterations_run + 1, family_id)
                iteration = self.flow.run_iteration(family_id)
                results.append({
                    "step": "iteration",
                    "iteration_id": iteration.iteration_id,
                    "stage": str(iteration.stage),
                    "qualified": iteration.qualified_improvement,
                    "score": iteration.composite_score.total if iteration.composite_score else 0.0,
                })
                iterations_run += 1
                continue

            # Unknown state - shouldn't happen
            logger.warning("Family %s in unexpected state: %s", family_id, family.state)
            break

        family = self.store.read_family(family_id)
        return {
            "family_id": family_id,
            "final_state": str(family.state),
            "iterations_run": iterations_run,
            "best_score": family.best_qualified_score,
            "strike_count": family.strike_count,
            "results": results,
        }

    def _run_holdout(self, family_id: str) -> dict[str, Any]:
        """Run holdout evaluation and handle the transition."""
        from alpha_forge.app.domain.events import FamilyEvent
        from alpha_forge.app.domain.scoring import compute_composite_score
        from alpha_forge.app.workflow.transitions import TransitionEngine

        family = self.store.read_family(family_id)
        engine = TransitionEngine()

        # Transition to HOLDOUT_RUNNING
        tr = engine.apply(family, FamilyEvent.PROMOTE_HOLDOUT)
        family = tr.family
        self.store.write_family(family)

        # Run holdout
        results = run_holdout(family_id, self.store, str(self.configs_dir))
        score = compute_composite_score(results)

        # Save results
        self.artifact_store.save_holdout_result(
            family_id,
            family.current_iteration,
            [r.model_dump() for r in results],
        )

        # Evaluate
        if score.total > 0 and score.alpha_quality > 0:
            tr = engine.apply(family, FamilyEvent.HOLDOUT_PASSED, context={"score": score.total})
            family = tr.family
            self.store.write_family(family)
            self.store.append_history(family_id, f"Holdout passed. Score: {score.total:.3f}")
            return {"step": "holdout", "status": "passed", "score": score.total}
        else:
            tr = engine.apply(family, FamilyEvent.HOLDOUT_FAILED)
            family = tr.family
            self.store.write_family(family)
            self.store.append_history(family_id, f"Holdout failed. Score: {score.total:.3f}")
            return {"step": "holdout", "status": "failed", "score": score.total}
