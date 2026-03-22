"""Family lifecycle flow: drives one iteration of a family through the full pipeline.

Plan → Judge → Code → Judge → Guards → Backtest → Robustness → Judge → Score → Strike update
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from alpha_forge.app.agents.judge_router import aggregate_verdict, run_tier
from alpha_forge.app.agents.llm_client import LLMClient
from alpha_forge.app.agents.researcher import ResearcherAgent
from alpha_forge.app.domain.events import FamilyEvent
from alpha_forge.app.domain.models import (
    IdeaFamily,
    Iteration,
    SeedCard,
)
from alpha_forge.app.domain.scoring import compute_composite_score, is_qualified_improvement
from alpha_forge.app.domain.states import FamilyState, IterationStage, Verdict
from alpha_forge.app.domain.strikes import add_strike, reset_strikes, should_cancel
from alpha_forge.app.guards.runner import any_failed, has_red_strike, run_all_guards
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.transitions import TransitionEngine
from alpha_forge.engine.backtest_runner import run_backtest
from alpha_forge.engine.robustness_runner import run_robustness_battery

logger = logging.getLogger(__name__)


class FamilyFlow:
    """Drives one complete iteration of a family through the pipeline."""

    def __init__(
        self,
        store: MarkdownStore,
        artifact_store: ArtifactStore,
        configs_dir: str | Path = "configs",
        client: LLMClient | None = None,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.configs_dir = Path(configs_dir).resolve()
        self.client = client or LLMClient()
        self.engine = TransitionEngine()
        self.researcher = ResearcherAgent(self.client)

    def _read_research_code(self, family_id: str) -> str:
        """Read all research/ files as a single string."""
        research_dir = self.store.root / "families" / family_id / "research"
        parts: list[str] = []
        for py_file in sorted(research_dir.glob("*.py")):
            parts.append(f"# === {py_file.name} ===\n{py_file.read_text()}")
        return "\n\n".join(parts)

    def _get_history_context(self, family_id: str) -> list[str]:
        """Get prior iteration history for judge context."""
        history_path = self.store.root / "families" / family_id / "HISTORY.md"
        if not history_path.exists():
            return []
        return [history_path.read_text()]

    def run_iteration(self, family_id: str) -> Iteration:
        """Run one complete iteration for a family.

        Executes: plan → tier-1 judge → code → tier-2 judge → guards → backtest → robustness → tier-3 judge → score

        Returns the completed Iteration object.
        """
        family = self.store.read_family(family_id)
        iter_num = family.current_iteration + 1
        iter_id = f"iter_{iter_num}"

        iteration = Iteration(
            iteration_id=iter_id,
            family_id=family_id,
        )

        logger.info("Starting iteration %s for family %s", iter_id, family_id)

        # Load seed for context
        try:
            seed = self.store.read_seed(family.seed_id, stage="accepted")
        except Exception:
            seed = SeedCard(
                seed_id=family.seed_id,
                seed_type="unknown",
                source_title="unknown",
                raw_claim=family.base_hypothesis,
                market="crypto_spot",
                horizon="5min",
                mechanism=family.mechanism,
                testable_hypothesis=family.base_hypothesis,
            )

        # --- Step 1: Draft plan ---
        logger.info("[%s] Drafting plan...", iter_id)
        prior_feedback = self._get_history_context(family_id)
        plan = self.researcher.draft_plan(family, seed, prior_feedback)
        iteration = iteration.model_copy(update={
            "plan": plan,
            "stage": IterationStage.DRAFT_PLAN,
        })
        self.store.write_iteration(iteration)

        # Transition to PLAN_IN_REVIEW
        result = self.engine.apply(family, FamilyEvent.PLAN_SUBMITTED)
        family = result.family
        self.store.write_family(family)

        # --- Step 2: Tier-1 judge (plan review) ---
        logger.info("[%s] Running tier-1 judges (plan review)...", iter_id)
        tier1_outputs = run_tier(1, {
            "plan": plan,
            "history": prior_feedback,
            "iteration_count": iter_num,
            "config": self._load_costs_config(),
        }, self.client)
        iteration = iteration.model_copy(update={
            "judge_outputs": [*iteration.judge_outputs, *tier1_outputs],
            "stage": IterationStage.PLAN_JUDGED,
        })
        self.store.write_iteration(iteration)

        tier1_verdict = aggregate_verdict(tier1_outputs)
        if tier1_verdict in (Verdict.REJECT, Verdict.REVISE):
            logger.warning("[%s] Plan rejected by tier-1 judges", iter_id)
            family = add_strike(family, iter_id, "Plan rejected by tier-1 judges")
            result = self.engine.apply(family, FamilyEvent.PLAN_REJECTED, context={
                "strike_reason": "Plan rejected",
                "iteration_id": iter_id,
            })
            family = result.family
            self.store.write_family(family)
            iteration = iteration.model_copy(update={"stage": IterationStage.ITERATION_FAILED})
            self.store.write_iteration(iteration)
            self._write_ledger(family_id, iter_id, iteration, family)
            return iteration

        # Transition to PLAN_APPROVED
        result = self.engine.apply(family, FamilyEvent.PLAN_APPROVED)
        family = result.family
        self.store.write_family(family)

        # --- Step 3: Write code ---
        logger.info("[%s] Researcher writing code...", iter_id)
        code_files = self.researcher.write_code(family, plan, prior_feedback)
        research_dir = self.store.root / "families" / family_id / "research"
        changed = self.researcher.apply_code(family_id, code_files, research_dir)
        iteration = iteration.model_copy(update={
            "changed_files": changed,
            "stage": IterationStage.CODE_WRITE,
        })
        self.store.write_iteration(iteration)

        # Transition to CODE_IN_REVIEW
        result = self.engine.apply(family, FamilyEvent.CODE_SUBMITTED)
        family = result.family
        result = self.engine.apply(family, FamilyEvent.CODE_SUBMITTED)
        family = result.family
        self.store.write_family(family)

        # --- Step 4: Tier-2 judge (code review) ---
        logger.info("[%s] Running tier-2 judges (code review)...", iter_id)
        code = self._read_research_code(family_id)
        tier2_outputs = run_tier(2, {
            "code": code,
            "diff": "",
            "history": prior_feedback,
        }, self.client)
        iteration = iteration.model_copy(update={
            "judge_outputs": [*iteration.judge_outputs, *tier2_outputs],
            "stage": IterationStage.CODE_JUDGED,
        })
        self.store.write_iteration(iteration)

        tier2_verdict = aggregate_verdict(tier2_outputs)
        if tier2_verdict in (Verdict.REJECT, Verdict.REVISE):
            logger.warning("[%s] Code rejected by tier-2 judges", iter_id)
            result = self.engine.apply(family, FamilyEvent.CODE_REJECTED, context={
                "strike_reason": "Code rejected by tier-2 judges",
                "iteration_id": iter_id,
            })
            family = result.family
            self.store.write_family(family)
            iteration = iteration.model_copy(update={"stage": IterationStage.ITERATION_FAILED})
            self.store.write_iteration(iteration)
            self._write_ledger(family_id, iter_id, iteration, family)
            return iteration

        # Transition to CODE_APPROVED
        result = self.engine.apply(family, FamilyEvent.CODE_APPROVED)
        family = result.family
        self.store.write_family(family)

        # --- Step 5: Run guards ---
        logger.info("[%s] Running guards...", iter_id)
        guard_results = run_all_guards(family, self.store, self.artifact_store, self.configs_dir)
        iteration = iteration.model_copy(update={
            "guard_results": [g.model_dump() for g in guard_results],
            "stage": IterationStage.RUN_GUARDS,
        })
        self.store.write_iteration(iteration)

        if any_failed(guard_results):
            is_red = has_red_strike(guard_results)
            violations = [v for g in guard_results for v in g.violations]
            logger.warning("[%s] Guards failed: %s", iter_id, violations)
            result = self.engine.apply(family, FamilyEvent.GUARDS_FAILED, context={
                "strike_reason": f"Guard failures: {', '.join(violations[:3])}",
                "iteration_id": iter_id,
                "is_red_strike": is_red,
            })
            family = result.family
            self.store.write_family(family)
            iteration = iteration.model_copy(update={"stage": IterationStage.ITERATION_FAILED})
            self.store.write_iteration(iteration)
            self._write_ledger(family_id, iter_id, iteration, family)
            return iteration

        # Guards passed
        result = self.engine.apply(family, FamilyEvent.GUARDS_PASSED)
        family = result.family
        self.store.write_family(family)

        # --- Step 6: Run backtest ---
        logger.info("[%s] Running validation backtest...", iter_id)
        iteration = iteration.model_copy(update={"stage": IterationStage.RUN_BACKTEST})
        self.store.write_iteration(iteration)

        bt_results = run_backtest(family_id, self.store, self.configs_dir)
        iteration = iteration.model_copy(update={
            "backtest_results": bt_results,
        })
        self.store.write_iteration(iteration)
        self.artifact_store.save_backtest_result(
            family_id, iter_num,
            [r.model_dump() for r in bt_results],
        )

        # Transition after backtest
        result = self.engine.apply(family, FamilyEvent.BACKTEST_COMPLETED)
        family = result.family
        self.store.write_family(family)

        # --- Step 7: Run robustness ---
        logger.info("[%s] Running robustness battery...", iter_id)
        iteration = iteration.model_copy(update={"stage": IterationStage.RUN_ROBUSTNESS})
        self.store.write_iteration(iteration)

        robustness = run_robustness_battery(family_id, self.store, bt_results, self.configs_dir)
        iteration = iteration.model_copy(update={"robustness_results": robustness})
        self.store.write_iteration(iteration)
        self.artifact_store.save_robustness_result(
            family_id, iter_num,
            robustness.model_dump(),
        )

        # --- Step 8: Tier-3 judge (result review) ---
        logger.info("[%s] Running tier-3 judges (result review)...", iter_id)
        all_metrics = {r.symbol: r.all_metrics for r in bt_results}
        tier3_outputs = run_tier(3, {
            "metrics": all_metrics,
            "robustness": robustness.model_dump(),
            "history": prior_feedback,
            "iteration_count": iter_num,
            "config": self._load_costs_config(),
        }, self.client)
        iteration = iteration.model_copy(update={
            "judge_outputs": [*iteration.judge_outputs, *tier3_outputs],
            "stage": IterationStage.RESULT_JUDGED,
        })
        self.store.write_iteration(iteration)

        tier3_verdict = aggregate_verdict(tier3_outputs)

        # --- Step 9: Score and decide ---
        score = compute_composite_score(bt_results, robustness)
        iteration = iteration.model_copy(update={"composite_score": score})

        qualified = is_qualified_improvement(score, family.best_qualified_score, robustness)
        iteration = iteration.model_copy(update={"qualified_improvement": qualified})

        if tier3_verdict in (Verdict.REJECT,):
            logger.info("[%s] Results rejected", iter_id)
            result = self.engine.apply(family, FamilyEvent.RESULT_REJECTED, context={
                "strike_reason": "Results rejected by tier-3 judges",
                "iteration_id": iter_id,
            })
            family = result.family
            iteration = iteration.model_copy(update={"stage": IterationStage.ITERATION_FAILED})
        elif tier3_verdict in (Verdict.APPROVE, Verdict.APPROVE_WITH_CONSTRAINTS) and qualified:
            logger.info("[%s] Qualified improvement! Score: %.3f", iter_id, score.total)
            family = reset_strikes(family)
            family = family.model_copy(update={"best_qualified_score": score.total})
            result = self.engine.apply(family, FamilyEvent.RESULT_APPROVED, context={
                "score": score.total,
            })
            family = result.family
            iteration = iteration.model_copy(update={
                "stage": IterationStage.ITERATION_SUCCESS,
                "verdict": tier3_verdict,
            })
        else:
            logger.info("[%s] No qualified improvement, iterating. Score: %.3f", iter_id, score.total)
            result = self.engine.apply(family, FamilyEvent.ITERATE, context={
                "strike_reason": "No qualified improvement" if not qualified else None,
                "iteration_id": iter_id,
            })
            family = result.family
            iteration = iteration.model_copy(update={
                "stage": IterationStage.ITERATION_FAILED if not qualified else IterationStage.ITERATION_SUCCESS,
                "verdict": tier3_verdict,
            })

        self.store.write_family(family)
        self.store.write_iteration(iteration)
        self._write_ledger(family_id, iter_id, iteration, family)
        self._update_global_state(family)

        return iteration

    def _load_costs_config(self) -> dict:
        """Load costs config as dict."""
        import yaml
        with open(self.configs_dir / "costs.yaml") as f:
            return yaml.safe_load(f)

    def _write_ledger(
        self,
        family_id: str,
        iter_id: str,
        iteration: Iteration,
        family: IdeaFamily,
    ) -> None:
        """Write a ledger entry for this iteration."""
        entry = {
            "family_id": family_id,
            "iteration_id": iter_id,
            "stage": str(iteration.stage),
            "verdict": str(iteration.verdict) if iteration.verdict else "none",
            "qualified_improvement": iteration.qualified_improvement,
            "composite_score": iteration.composite_score.total if iteration.composite_score else 0.0,
            "strike_count": family.strike_count,
            "red_strike_count": family.red_strike_count,
            "family_state": str(family.state),
        }
        self.store.write_ledger_entry(family_id, iter_id, entry)
        self.store.append_history(family_id, (
            f"Iteration {iter_id}: stage={iteration.stage}, "
            f"verdict={iteration.verdict}, qualified={iteration.qualified_improvement}, "
            f"strikes={family.strike_count}"
        ))

    def _update_global_state(self, family: IdeaFamily) -> None:
        """Update STATE.md with current family state."""
        from datetime import datetime, timezone
        self.store.write_global_state({
            "active_family": family.family_id,
            "state": str(family.state),
            "current_iteration": family.current_iteration,
            "strike_count": family.strike_count,
            "red_strike_count": family.red_strike_count,
            "best_qualified_score": family.best_qualified_score,
            "last_transition_at": datetime.now(tz=timezone.utc).isoformat(),
        })
