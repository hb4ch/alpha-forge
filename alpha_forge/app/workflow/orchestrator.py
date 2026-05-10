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
from alpha_forge.app.workflow.family_flow import BudgetExhaustedError, FamilyFlow
from alpha_forge.engine.holdout_runner import run_holdout
from alpha_forge.engine.paper_runner import (
    PaperResult,
    run_paper_forward as default_run_paper_forward,
)

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
    FamilyState.ARCHIVED_REJECTED,
    FamilyState.BUDGET_EXHAUSTED,
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
        bus=None,  # EventBus | None
        paper_forward_runner=None,  # Callable for tests; defaults to paper_runner.run_paper_forward
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.configs_dir = Path(configs_dir).resolve()
        if client is None:
            from alpha_forge.app.agents.llm_config import get_client_for_role
            client = get_client_for_role("researcher")
        self.client = client
        self.max_iterations = max_iterations
        self.bus = bus
        self.flow = FamilyFlow(store, artifact_store, configs_dir, self.client, bus=bus)
        self._paused = False
        self._run_paper_forward_fn = paper_forward_runner or default_run_paper_forward

    def pause(self) -> None:
        """Request loop pause at next iteration boundary."""
        self._paused = True

    def resume(self) -> None:
        """Clear the pause flag."""
        self._paused = False

    def run(self, family_id: str | None = None) -> dict[str, Any]:
        """Run the orchestrator loop for a family.

        If no family_id is provided, reads the active family from STATE.md.

        Returns a summary dict of what happened.
        """
        if family_id is None:
            global_state = self.store.read_global_state()
            family_id = global_state.get("active_family")
            if not family_id:
                # Try the queue
                family_id = self.store.pop_from_queue()
            if not family_id:
                logger.info("No active family and queue is empty")
                return {"status": "no_active_family"}

        family = self.store.read_family(family_id)
        logger.info("Orchestrator starting for family %s (state: %s)", family_id, family.state)

        iterations_run = 0
        results: list[dict[str, Any]] = []

        while iterations_run < self.max_iterations:
            if self._paused:
                if self.bus:
                    self.bus.emit_sync("loop_paused", {"family_id": family_id})
                break

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
                logger.info("Running paper-forward for family %s", family_id)
                result = self._run_paper_forward(family_id)
                results.append(result)
                iterations_run += 1
                continue

            # Mid-iteration states from a crashed/aborted run — reset to QUEUED
            MID_ITERATION_STATES = {
                FamilyState.PLAN_IN_REVIEW,
                FamilyState.PLAN_APPROVED,
                FamilyState.CODE_IN_REVIEW,
                FamilyState.CODE_APPROVED,
                FamilyState.BACKTEST_RUNNING,
            }
            if family.state in MID_ITERATION_STATES:
                logger.warning(
                    "Family %s in mid-iteration state %s, resetting to QUEUED",
                    family_id, family.state,
                )
                family = family.model_copy(update={"state": FamilyState.QUEUED})
                self.store.write_family(family)

            if family.state in (FamilyState.QUEUED, FamilyState.ITERATE,
                                FamilyState.PLAN_REVISION_REQUIRED,
                                FamilyState.CODE_REVISION_REQUIRED):
                logger.info("Running iteration %d for family %s", iterations_run + 1, family_id)
                try:
                    iteration = self.flow.run_iteration(family_id)
                except BudgetExhaustedError as e:
                    logger.info("Family %s: %s", family_id, e)
                    results.append({"step": "budget_exhausted", "best_score": e.best_score})
                    break
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
        result = {
            "family_id": family_id,
            "final_state": str(family.state),
            "iterations_run": iterations_run,
            "best_score": family.best_score,
            "best_qualified_score": family.best_qualified_score,
            "results": results,
        }

        # If this family finished, try the next one from the queue
        if family.state.is_terminal or family.state in WAITING_STATES:
            next_id = self.store.pop_from_queue()
            if next_id:
                logger.info("Advancing to next family in queue: %s", next_id)
                next_result = self.run(next_id)
                result["next_family"] = next_result

        return result

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

    def _run_paper_forward(self, family_id: str) -> dict[str, Any]:
        """Execute paper-forward sim via alpha-trader subprocess and apply
        PAPER_PASSED / PAPER_FAILED transition based on the verdict.

        Idempotent: if the family already has a paper_forward_result.json,
        the verdict is replayed without re-running the trader. Mirrors
        ``_run_holdout`` in shape.
        """
        from datetime import datetime, timezone

        import yaml

        from alpha_forge.app.domain.events import FamilyEvent
        from alpha_forge.app.workflow.transitions import TransitionEngine

        engine = TransitionEngine()
        family = self.store.read_family(family_id)

        # Idempotency: replay verdict from persisted artifact.
        existing = self.artifact_store.load_paper_forward_result(family_id)
        if existing is not None:
            logger.info(
                "Paper-forward verdict already exists for %s (verdict=%s); "
                "re-applying transition",
                family_id, existing.get("verdict"),
            )
            # Family may already be in PAPER_FORWARD_RUNNING from a prior
            # crash mid-handoff. Move it forward via the recorded verdict.
            if family.state == FamilyState.PROMOTE_TO_PAPER:
                family = engine.apply(family, FamilyEvent.PROMOTE_PAPER).family
                self.store.write_family(family)
            return self._apply_paper_verdict(family_id, existing)

        # 1. Transition to PAPER_FORWARD_RUNNING
        family = engine.apply(family, FamilyEvent.PROMOTE_PAPER).family
        self.store.write_family(family)

        # 2. Export bundle
        bundle_path = self._export_bundle_for(family_id)

        # 3. Determine paper window: holdout_end → now
        with open(self.configs_dir / "splits.yaml") as f:
            splits = yaml.safe_load(f)
        holdout_end_str = splits["holdout"]["end"]
        holdout_end = datetime.fromisoformat(
            holdout_end_str if "T" in holdout_end_str else holdout_end_str + "T00:00:00+00:00"
        )
        if holdout_end.tzinfo is None:
            holdout_end = holdout_end.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now <= holdout_end:
            # Operator started paper-forward before any post-holdout data
            # exists. Save a synthetic FAIL so subsequent runs can detect.
            payload = {
                "schema_version": 1,
                "verdict": "FAIL",
                "reasons": ["empty_paper_window"],
                "metrics": {},
                "synthetic": True,
            }
            self.artifact_store.save_paper_forward_result(family_id, payload)
            return self._apply_paper_verdict(family_id, payload)

        # 4. Per-run output dir
        output_dir = self.artifact_store.paper_forward_run_dir(family_id)

        # 5. Resolve universe + timeframe from bundle's manifest
        import json as _json
        with open(bundle_path / "manifest.json") as f:
            manifest = _json.load(f)
        universe = manifest["universe"]
        timeframe = manifest["timeframe"]

        # 6. Run the trader
        result: PaperResult = self._run_paper_forward_fn(
            family_id=family_id,
            bundle_path=bundle_path,
            output_dir=output_dir,
            paper_window=(holdout_end, now),
            universe=universe,
            timeframe=timeframe,
        )

        # 7. Persist verdict + transition
        self.artifact_store.save_paper_forward_result(family_id, result.raw)
        return self._apply_paper_verdict(family_id, result.raw)

    def _apply_paper_verdict(
        self, family_id: str, result_json: dict[str, Any]
    ) -> dict[str, Any]:
        """Translate the trader's verdict into a state transition."""
        from alpha_forge.app.domain.events import FamilyEvent
        from alpha_forge.app.workflow.transitions import TransitionEngine

        engine = TransitionEngine()
        family = self.store.read_family(family_id)
        verdict = result_json.get("verdict", "FAIL")
        event = (
            FamilyEvent.PAPER_PASSED if verdict == "PASS"
            else FamilyEvent.PAPER_FAILED
        )
        family = engine.apply(family, event).family
        self.store.write_family(family)
        reasons = result_json.get("reasons") or []
        self.store.append_history(
            family_id,
            f"Paper forward {verdict}: "
            f"{', '.join(reasons) if reasons else 'all auto-promote checks passed'}",
        )
        return {"step": "paper_forward", "status": verdict.lower(),
                "verdict": verdict, "reasons": list(reasons),
                "metrics": dict(result_json.get("metrics", {}))}

    def _export_bundle_for(self, family_id: str) -> Path:
        """Build a strategy bundle for ``family_id`` by reusing the existing
        ``scripts/export_strategy.py`` helpers (they are pure callables, no
        alpha_trader.* imports inside)."""
        # Repo root = parent of alpha_forge package
        from alpha_forge.app.workflow import orchestrator as _self_mod
        repo_root = Path(_self_mod.__file__).resolve().parents[3]
        # Make scripts/ importable
        import sys
        scripts_dir = repo_root / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from export_strategy import (  # type: ignore[import-not-found]
            build_bundle, load_family,
        )
        workspace = self.store.root
        family_dir, family_meta = load_family(workspace, family_id)
        bundles_dir = workspace / "bundles"
        bundles_dir.mkdir(parents=True, exist_ok=True)
        return build_bundle(
            family_id=family_id,
            family_dir=family_dir,
            family_meta=family_meta,
            output_dir=bundles_dir,
        )
