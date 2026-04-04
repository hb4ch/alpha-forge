"""Family lifecycle flow: drives one iteration of a family through the full pipeline.

Plan → Judge → Code → Judge → Guards → Backtest → Robustness → Judge → Score → Strike update
"""

from __future__ import annotations

import difflib
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
from alpha_forge.app.domain.strikes import reset_strikes
from alpha_forge.app.guards.runner import any_failed, has_red_strike, run_all_guards
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.transitions import TransitionEngine
from alpha_forge.engine.backtest_runner import run_backtest
from alpha_forge.engine.robustness_runner import run_robustness_battery

logger = logging.getLogger(__name__)

NEW_ITERATION_STATES = {
    FamilyState.QUEUED,
    FamilyState.ITERATE,
}

PLAN_ENTRY_STATES = {
    FamilyState.QUEUED,
    FamilyState.ITERATE,
    FamilyState.PLAN_REVISION_REQUIRED,
}

ALLOWED_RESEARCH_FILES = [
    "features.py",
    "labels.py",
    "model_config.py",
    "signal_combiner.py",
]


class FamilyFlow:
    """Drives one complete iteration of a family through the pipeline."""

    def __init__(
        self,
        store: MarkdownStore,
        artifact_store: ArtifactStore,
        configs_dir: str | Path = "configs",
        client: LLMClient | None = None,
        bus=None,  # EventBus | None
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.configs_dir = Path(configs_dir).resolve()
        if client is None:
            from alpha_forge.app.agents.llm_config import get_client_for_role
            client = get_client_for_role("researcher")
        self.client = client
        self.engine = TransitionEngine()
        self.researcher = ResearcherAgent(self.client)
        self.bus = bus

    def _emit(self, event: str, data: dict) -> None:
        """Emit an event to the EventBus if available."""
        if self.bus:
            self.bus.emit_sync(event, data)

    def _stream_cb(self, role: str):
        """Create a stream callback that emits tokens to the TUI."""
        if not self.bus:
            return None
        def cb(token: str) -> None:
            self.bus.emit_sync("llm_token", {"role": role, "token": token})
        return cb

    def _read_research_code_dict(self, family_id: str) -> dict[str, str] | None:
        """Read all research/ files as a dict of {filename: content}."""
        research_dir = self.store.root / "families" / family_id / "research"
        if not research_dir.exists():
            return None
        files = {}
        for py_file in sorted(research_dir.glob("*.py")):
            files[py_file.name] = py_file.read_text()
        return files if files else None

    def _read_research_code(self, family_id: str) -> str:
        """Read all research/ files as a single string."""
        return self._format_code_bundle(self._read_research_code_dict(family_id))

    @staticmethod
    def _format_code_bundle(code_files: dict[str, str] | None) -> str:
        """Format research code for prompt context."""
        if not code_files:
            return ""
        parts: list[str] = []
        for filename in sorted(code_files):
            parts.append(f"# === {filename} ===\n{code_files[filename]}")
        return "\n\n".join(parts)

    @staticmethod
    def _forbidden_files_context() -> list[str]:
        """Describe the forbidden edit surface outside the research files."""
        return [
            "Any file outside families/<family_id>/research/",
            "alpha_forge/app/*",
            "alpha_forge/engine/*",
            "alpha_forge/tui/*",
            "configs/*",
            "scripts/*",
            "judges/prompts/*",
            "tests/*",
        ]

    def _load_prior_code_snapshot(
        self,
        family_id: str,
        iteration_num: int,
        started_new_cycle: bool,
    ) -> dict[str, str] | None:
        """Load the prior saved code snapshot when available."""
        snapshot_iteration = iteration_num - 1 if started_new_cycle else iteration_num
        if snapshot_iteration <= 0:
            return None
        return self.artifact_store.load_code_snapshot(family_id, snapshot_iteration)

    @staticmethod
    def _generate_code_diff(
        previous_code: dict[str, str] | None,
        current_code: dict[str, str],
    ) -> str:
        """Generate a unified diff between code bundles."""
        if not previous_code:
            return "No prior snapshot available."

        diff_parts: list[str] = []
        for filename in sorted(set(previous_code) | set(current_code)):
            old_text = previous_code.get(filename, "")
            new_text = current_code.get(filename, "")
            if old_text == new_text:
                continue
            diff_parts.extend(
                difflib.unified_diff(
                    old_text.splitlines(keepends=True),
                    new_text.splitlines(keepends=True),
                    fromfile=f"a/{filename}",
                    tofile=f"b/{filename}",
                )
            )

        return "".join(diff_parts) or "No textual changes detected."

    @staticmethod
    def _extract_judge_feedback(judge_outputs: list) -> list[str]:
        """Extract must_fix items and reasoning from judge outputs into actionable feedback."""
        feedback: list[str] = []
        for output in judge_outputs:
            judge_type = getattr(output, "judge_type", "unknown")
            verdict = getattr(output, "verdict", "")
            reasoning = getattr(output, "reasoning_summary", "") or getattr(output, "reasoning", "")
            must_fix = getattr(output, "must_fix", []) or []
            if must_fix or reasoning:
                parts = [f"[{judge_type}] verdict={verdict}"]
                if reasoning:
                    parts.append(f"  Reasoning: {reasoning}")
                for item in must_fix:
                    parts.append(f"  MUST FIX: {item}")
                feedback.append("\n".join(parts))
        return feedback

    def _build_plan_context(
        self,
        family: IdeaFamily,
        seed: SeedCard,
        prior_feedback: list[str] | None,
    ) -> dict[str, Any]:
        """Assemble the planning context from workflow state."""
        return {
            "family": family,
            "seed": seed,
            "prior_feedback": prior_feedback,
        }

    def _build_code_write_context(
        self,
        family: IdeaFamily,
        plan: str,
        prior_feedback: list[str] | None,
        existing_code: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Assemble the researcher code-writing context."""
        return {
            "family": family,
            "plan": plan,
            "prior_feedback": prior_feedback,
            "existing_code": existing_code,
        }

    def _build_code_review_context(
        self,
        plan: str,
        prior_feedback: list[str] | None,
        previous_code: dict[str, str] | None,
        current_code: dict[str, str],
    ) -> dict[str, Any]:
        """Assemble tier-2 review context."""
        return {
            "plan": plan,
            "code": self._format_code_bundle(current_code),
            "diff": self._generate_code_diff(previous_code, current_code),
            "history": prior_feedback,
            "changed_files": sorted(current_code.keys()),
            "allowed_files": list(ALLOWED_RESEARCH_FILES),
            "forbidden_files": self._forbidden_files_context(),
        }

    def _build_result_review_context(
        self,
        bt_results,
        robustness,
        prior_feedback: list[str] | None,
        iter_num: int,
        family: IdeaFamily,
    ) -> dict[str, Any]:
        """Assemble tier-3 review context."""
        return {
            "metrics": {r.symbol: r.all_metrics for r in bt_results},
            "robustness": robustness.model_dump(),
            "history": prior_feedback,
            "iteration_count": iter_num,
            "config": self._load_costs_config(),
            "prior_best": family.best_qualified_score,
        }

    def _get_history_context(self, family_id: str) -> list[str]:
        """Get prior iteration history for judge context."""
        history_path = self.store.root / "families" / family_id / "HISTORY.md"
        if not history_path.exists():
            return []
        return [history_path.read_text()]

    def _append_history(self, family_id: str, entry: str) -> None:
        """Append a free-form entry to the family's HISTORY.md."""
        from datetime import datetime, timezone
        history_path = self.store.root / "families" / family_id / "HISTORY.md"
        timestamp = datetime.now(timezone.utc).isoformat()
        with open(history_path, "a") as f:
            f.write(f"\n## [{timestamp}]\n{entry}\n")

    def _transition_family(
        self,
        family: IdeaFamily,
        event: FamilyEvent,
        context: dict[str, Any] | None = None,
    ) -> IdeaFamily:
        """Apply a transition and persist the updated family."""
        result = self.engine.apply(family, event, context=context)
        family = result.family
        self.store.write_family(family)
        return family

    def _load_seed_context(self, family: IdeaFamily) -> SeedCard:
        """Load accepted seed context, falling back to family metadata."""
        try:
            return self.store.read_seed(family.seed_id, stage="accepted")
        except Exception:
            return SeedCard(
                seed_id=family.seed_id,
                seed_type="unknown",
                source_title="unknown",
                raw_claim=family.base_hypothesis,
                market="crypto_spot",
                horizon="5min",
                mechanism=family.mechanism,
                testable_hypothesis=family.base_hypothesis,
            )

    def _prepare_iteration(self, family: IdeaFamily) -> tuple[IdeaFamily, Iteration, bool, bool]:
        """Prepare the iteration object and family state for the current entry path."""
        existing = self.store.read_iteration(family.family_id)

        if family.state in NEW_ITERATION_STATES:
            family = family.model_copy(update={"current_iteration": family.current_iteration + 1})
            self.store.write_family(family)
            self._update_global_state(family)
            iteration = Iteration(
                iteration_id=f"iter_{family.current_iteration}",
                family_id=family.family_id,
            )
            return family, iteration, True, True

        if family.state == FamilyState.PLAN_REVISION_REQUIRED:
            if family.current_iteration <= 0:
                raise ValueError("Plan revision requires an active iteration number")
            iteration_id = existing.iteration_id if existing else f"iter_{family.current_iteration}"
            iteration = Iteration(
                iteration_id=iteration_id,
                family_id=family.family_id,
                proposal_type=existing.proposal_type if existing else "initial",
                mutation_category=existing.mutation_category if existing else None,
            )
            return family, iteration, True, False

        if family.state == FamilyState.CODE_REVISION_REQUIRED:
            if existing is None:
                raise ValueError("Code revision requires an existing current iteration")
            if not existing.plan:
                raise ValueError("Code revision requires a persisted plan")
            iteration = Iteration(
                iteration_id=existing.iteration_id,
                family_id=existing.family_id,
                proposal_type=existing.proposal_type,
                mutation_category=existing.mutation_category,
                plan=existing.plan,
            )
            return family, iteration, False, False

        raise ValueError(f"Family {family.family_id} is not runnable from state {family.state}")

    def _finalize_iteration(
        self,
        family_id: str,
        iteration: Iteration,
        family: IdeaFamily,
        score: float = 0.0,
    ) -> Iteration:
        """Persist final iteration state, ledger, and global state."""
        self.store.write_family(family)
        self.store.write_iteration(iteration)
        self._write_ledger(family_id, iteration.iteration_id, iteration, family)
        self._update_global_state(family)
        self._emit("iteration_complete", {
            "family_id": family_id,
            "iteration": iteration.iteration_id,
            "stage": str(iteration.stage),
            "score": score,
            "qualified": iteration.qualified_improvement,
        })
        return iteration

    def run_iteration(self, family_id: str) -> Iteration:
        """Run one complete iteration for a family.

        Executes: plan → tier-1 judge → code → tier-2 judge → guards → backtest → robustness → tier-3 judge → score

        Returns the completed Iteration object.
        """
        family = self.store.read_family(family_id)
        family, iteration, needs_plan_phase, started_new_cycle = self._prepare_iteration(family)
        iter_num = family.current_iteration
        iter_id = iteration.iteration_id

        logger.info("Starting iteration %s for family %s", iter_id, family_id)
        prior_feedback = self._get_history_context(family_id)

        if needs_plan_phase:
            seed = self._load_seed_context(family)

            # --- Step 1: Draft plan ---
            logger.info("[%s] Drafting plan...", iter_id)
            self._emit("stage_changed", {"stage": "DRAFT_PLAN", "family_id": family_id, "iteration": iter_id})
            self._emit("llm_start", {"role": "Researcher", "task": "Drafting plan"})
            # On plan revision, surface the judge feedback that triggered the revision
            plan_feedback = list(prior_feedback) if prior_feedback else []
            if not started_new_cycle:
                # This is a revision — load previous iteration's judge outputs
                try:
                    prev_iter = self.store.read_current_iteration(family_id)
                    if prev_iter and prev_iter.judge_outputs:
                        judge_fb = self._extract_judge_feedback(prev_iter.judge_outputs)
                        if judge_fb:
                            plan_feedback.append(
                                "## REVISION REQUIRED — Judge feedback to address:\n" + "\n\n".join(judge_fb)
                            )
                except Exception:
                    pass
            plan_context = self._build_plan_context(family, seed, plan_feedback)
            plan = self.researcher.draft_plan(
                plan_context["family"],
                plan_context["seed"],
                plan_context["prior_feedback"],
                stream_callback=self._stream_cb("Researcher"),
            )
            self._emit("llm_end", {"role": "Researcher"})
            iteration = iteration.model_copy(update={
                "plan": plan,
                "stage": IterationStage.DRAFT_PLAN,
            })
            self.store.write_iteration(iteration)

            family = self._transition_family(family, FamilyEvent.PLAN_SUBMITTED)

            # --- Step 2: Tier-1 judge (plan review) ---
            self._emit("stage_changed", {"stage": "PLAN_JUDGED", "family_id": family_id, "iteration": iter_id, "detail": "Awaiting tier-1 judges..."})
            self._emit("llm_start", {"role": "Judges", "task": "Tier-1 plan review (Leakage, Overfit, Realism)"})
            logger.info("[%s] Running tier-1 judges (plan review)...", iter_id)
            tier1_outputs = run_tier(1, {
                "plan": plan,
                "history": prior_feedback,
                "iteration_count": iter_num,
                "config": self._load_costs_config(),
            })
            iteration = iteration.model_copy(update={
                "judge_outputs": [*iteration.judge_outputs, *tier1_outputs],
                "stage": IterationStage.PLAN_JUDGED,
            })
            self.store.write_iteration(iteration)

            self._emit("llm_end", {"role": "Judges"})
            tier1_verdict = aggregate_verdict(tier1_outputs)
            self._emit("verdict_received", {
                "tier": 1, "verdict": str(tier1_verdict), "outputs": [o.model_dump() for o in tier1_outputs],
                "family_id": family_id, "iteration": iter_id,
            })
            if tier1_verdict == Verdict.REJECT:
                logger.warning("[%s] Plan rejected by tier-1 judges", iter_id)
                family = self._transition_family(family, FamilyEvent.PLAN_REJECTED, context={
                    "strike_reason": "Plan rejected",
                    "iteration_id": iter_id,
                })
                iteration = iteration.model_copy(update={
                    "stage": IterationStage.ITERATION_FAILED,
                    "verdict": tier1_verdict,
                })
                return self._finalize_iteration(family_id, iteration, family)
            elif tier1_verdict == Verdict.REVISE:
                logger.info("[%s] Plan revision requested by tier-1 judges (no strike)", iter_id)
                family = self._transition_family(family, FamilyEvent.PLAN_REJECTED)
                iteration = iteration.model_copy(update={
                    "stage": IterationStage.ITERATION_FAILED,
                    "verdict": tier1_verdict,
                })
                return self._finalize_iteration(family_id, iteration, family)

            family = self._transition_family(family, FamilyEvent.PLAN_APPROVED)
        else:
            plan = iteration.plan

        # --- Step 3: Write code ---
        logger.info("[%s] Researcher writing code...", iter_id)
        self._emit("stage_changed", {"stage": "CODE_WRITE", "family_id": family_id, "iteration": iter_id})
        self._emit("llm_start", {"role": "Researcher", "task": "Writing research code"})
        # Save code snapshot before overwriting
        old_code = self._read_research_code_dict(family_id)
        if started_new_cycle and old_code and iter_num > 1:
            self.artifact_store.save_code_snapshot(family_id, iter_num - 1, old_code)
        # Enrich feedback with judge must_fix items from this iteration
        code_feedback = list(prior_feedback) if prior_feedback else []
        judge_feedback = self._extract_judge_feedback(iteration.judge_outputs)
        if judge_feedback:
            code_feedback.append("## Judge feedback from this iteration (address these):\n" + "\n\n".join(judge_feedback))
        code_write_context = self._build_code_write_context(
            family,
            plan,
            code_feedback,
            existing_code=old_code if family.state == FamilyState.CODE_REVISION_REQUIRED else None,
        )
        code_files = self.researcher.write_code(
            code_write_context["family"],
            code_write_context["plan"],
            code_write_context["prior_feedback"],
            existing_code=code_write_context["existing_code"],
            stream_callback=self._stream_cb("Researcher"),
        )
        self._emit("llm_end", {"role": "Researcher"})
        research_dir = self.store.root / "families" / family_id / "research"
        changed = self.researcher.apply_code(family_id, code_files, research_dir)
        # Save new code snapshot
        self.artifact_store.save_code_snapshot(family_id, iter_num, code_files)
        iteration = iteration.model_copy(update={
            "changed_files": changed,
            "stage": IterationStage.CODE_WRITE,
        })
        self.store.write_iteration(iteration)

        family = self._transition_family(family, FamilyEvent.CODE_SUBMITTED)

        # --- Step 4: Tier-2 judge (code review) ---
        logger.info("[%s] Running tier-2 judges (code review)...", iter_id)
        self._emit("stage_changed", {"stage": "CODE_JUDGED", "family_id": family_id, "iteration": iter_id, "detail": "Awaiting tier-2 judges..."})
        self._emit("llm_start", {"role": "Judges", "task": "Tier-2 code review (Leakage, Code)"})
        prior_code_for_review = old_code or self._load_prior_code_snapshot(family_id, iter_num, started_new_cycle)
        code_review_context = self._build_code_review_context(
            plan,
            prior_feedback,
            prior_code_for_review,
            code_files,
        )
        tier2_outputs = run_tier(2, code_review_context)
        self._emit("llm_end", {"role": "Judges"})
        iteration = iteration.model_copy(update={
            "judge_outputs": [*iteration.judge_outputs, *tier2_outputs],
            "stage": IterationStage.CODE_JUDGED,
        })
        self.store.write_iteration(iteration)

        tier2_verdict = aggregate_verdict(tier2_outputs)
        self._emit("verdict_received", {
            "tier": 2, "verdict": str(tier2_verdict), "outputs": [o.model_dump() for o in tier2_outputs],
            "family_id": family_id, "iteration": iter_id,
        })
        if tier2_verdict == Verdict.REJECT:
            logger.warning("[%s] Code rejected by tier-2 judges", iter_id)
            family = self._transition_family(family, FamilyEvent.CODE_REJECTED, context={
                "strike_reason": "Code rejected by tier-2 judges",
                "iteration_id": iter_id,
            })
            iteration = iteration.model_copy(update={
                "stage": IterationStage.ITERATION_FAILED,
                "verdict": tier2_verdict,
            })
            return self._finalize_iteration(family_id, iteration, family)
        elif tier2_verdict == Verdict.REVISE:
            logger.info("[%s] Code revision requested by tier-2 judges (no strike)", iter_id)
            family = self._transition_family(family, FamilyEvent.CODE_REJECTED)
            iteration = iteration.model_copy(update={
                "stage": IterationStage.ITERATION_FAILED,
                "verdict": tier2_verdict,
            })
            return self._finalize_iteration(family_id, iteration, family)

        family = self._transition_family(family, FamilyEvent.CODE_APPROVED)

        # --- Step 5: Run guards ---
        logger.info("[%s] Running guards...", iter_id)
        self._emit("stage_changed", {"stage": "RUN_GUARDS", "family_id": family_id, "iteration": iter_id, "detail": "Running deterministic guards..."})
        guard_results = run_all_guards(family, self.store, self.artifact_store, self.configs_dir)
        iteration = iteration.model_copy(update={
            "guard_results": [g.model_dump() for g in guard_results],
            "stage": IterationStage.RUN_GUARDS,
        })
        self.store.write_iteration(iteration)

        self._emit("guards_complete", {
            "family_id": family_id, "iteration": iter_id,
            "results": [g.model_dump() for g in guard_results],
        })
        if any_failed(guard_results):
            is_red = has_red_strike(guard_results)
            violations = [v for g in guard_results for v in g.violations]
            logger.warning("[%s] Guards failed: %s", iter_id, violations)
            family = self._transition_family(family, FamilyEvent.GUARDS_FAILED, context={
                "strike_reason": f"Guard failures: {', '.join(violations[:3])}",
                "iteration_id": iter_id,
                "is_red_strike": is_red,
            })
            iteration = iteration.model_copy(update={"stage": IterationStage.ITERATION_FAILED})
            return self._finalize_iteration(family_id, iteration, family)

        family = self._transition_family(family, FamilyEvent.GUARDS_PASSED)

        # --- Step 6: Run backtest ---
        logger.info("[%s] Running validation backtest...", iter_id)
        self._emit("stage_changed", {"stage": "RUN_BACKTEST", "family_id": family_id, "iteration": iter_id, "detail": "Running validation backtest..."})
        iteration = iteration.model_copy(update={"stage": IterationStage.RUN_BACKTEST})
        self.store.write_iteration(iteration)

        try:
            bt_results = run_backtest(family_id, self.store, self.configs_dir)
        except Exception as e:
            error_msg = f"Backtest runtime error: {type(e).__name__}: {e}"
            logger.error("[%s] %s", iter_id, error_msg)
            self._emit("stage_changed", {"stage": "BACKTEST_FAILED", "family_id": family_id, "iteration": iter_id, "detail": error_msg})
            # Append error to history so the researcher sees it on revision
            self._append_history(family_id, f"Iteration {iter_id}: BACKTEST RUNTIME ERROR\n  {error_msg}")
            family = self._transition_family(family, FamilyEvent.BACKTEST_FAILED)
            iteration = iteration.model_copy(update={
                "stage": IterationStage.ITERATION_FAILED,
                "verdict": Verdict.REVISE,
            })
            return self._finalize_iteration(family_id, iteration, family)

        iteration = iteration.model_copy(update={
            "backtest_results": bt_results,
        })
        self.store.write_iteration(iteration)
        self._emit("backtest_complete", {
            "family_id": family_id, "iteration": iter_id,
            "results": [r.model_dump() for r in bt_results],
        })
        self.artifact_store.save_backtest_result(
            family_id, iter_num,
            [r.model_dump() for r in bt_results],
        )

        # Transition after backtest
        family = self._transition_family(family, FamilyEvent.BACKTEST_COMPLETED)

        # --- Step 7: Run robustness ---
        logger.info("[%s] Running robustness battery...", iter_id)
        self._emit("stage_changed", {"stage": "RUN_ROBUSTNESS", "family_id": family_id, "iteration": iter_id, "detail": "Running robustness battery..."})
        iteration = iteration.model_copy(update={"stage": IterationStage.RUN_ROBUSTNESS})
        self.store.write_iteration(iteration)

        try:
            robustness = run_robustness_battery(family_id, self.store, bt_results, self.configs_dir)
        except Exception as e:
            error_msg = f"Robustness runtime error: {type(e).__name__}: {e}"
            logger.error("[%s] %s", iter_id, error_msg)
            self._emit("stage_changed", {"stage": "ROBUSTNESS_FAILED", "family_id": family_id, "iteration": iter_id, "detail": error_msg})
            self._append_history(family_id, f"Iteration {iter_id}: ROBUSTNESS RUNTIME ERROR\n  {error_msg}")
            # Still have backtest results — proceed to tier-3 with empty robustness
            from alpha_forge.app.domain.models import RobustnessResult
            robustness = RobustnessResult(tests=[])
            logger.warning("[%s] Proceeding to tier-3 with empty robustness results", iter_id)

        iteration = iteration.model_copy(update={"robustness_results": robustness})
        self.store.write_iteration(iteration)
        self.artifact_store.save_robustness_result(
            family_id, iter_num,
            robustness.model_dump(),
        )

        # --- Step 8: Tier-3 judge (result review) ---
        logger.info("[%s] Running tier-3 judges (result review)...", iter_id)
        self._emit("stage_changed", {"stage": "RESULT_JUDGED", "family_id": family_id, "iteration": iter_id, "detail": "Awaiting tier-3 judges..."})
        self._emit("llm_start", {"role": "Judges", "task": "Tier-3 result review (Performance, Risk)"})
        result_review_context = self._build_result_review_context(
            bt_results,
            robustness,
            prior_feedback,
            iter_num,
            family,
        )
        tier3_outputs = run_tier(3, result_review_context)
        self._emit("llm_end", {"role": "Judges"})
        iteration = iteration.model_copy(update={
            "judge_outputs": [*iteration.judge_outputs, *tier3_outputs],
            "stage": IterationStage.RESULT_JUDGED,
        })
        self.store.write_iteration(iteration)

        tier3_verdict = aggregate_verdict(tier3_outputs)
        self._emit("verdict_received", {
            "tier": 3, "verdict": str(tier3_verdict), "outputs": [o.model_dump() for o in tier3_outputs],
            "family_id": family_id, "iteration": iter_id,
        })

        # --- Step 9: Score and decide ---
        score = compute_composite_score(bt_results, robustness)
        iteration = iteration.model_copy(update={
            "composite_score": score,
            "verdict": tier3_verdict,
        })

        qualified = is_qualified_improvement(score, family.best_qualified_score, robustness)
        iteration = iteration.model_copy(update={"qualified_improvement": qualified})

        if tier3_verdict in (Verdict.REJECT,):
            logger.info("[%s] Results rejected", iter_id)
            family = self._transition_family(family, FamilyEvent.RESULT_REJECTED, context={
                "strike_reason": "Results rejected by tier-3 judges",
                "iteration_id": iter_id,
            })
            iteration = iteration.model_copy(update={"stage": IterationStage.ITERATION_FAILED})
        elif tier3_verdict in (Verdict.APPROVE, Verdict.APPROVE_WITH_CONSTRAINTS) and qualified:
            logger.info("[%s] Qualified improvement! Score: %.3f", iter_id, score.total)
            family = reset_strikes(family)
            family = self._transition_family(family, FamilyEvent.RESULT_APPROVED, context={
                "score": score.total,
            })
            iteration = iteration.model_copy(update={
                "stage": IterationStage.ITERATION_SUCCESS,
            })
        else:
            logger.info("[%s] No qualified improvement, iterating. Score: %.3f", iter_id, score.total)
            family = self._transition_family(family, FamilyEvent.ITERATE, context={
                "strike_reason": "No qualified improvement" if not qualified else None,
                "iteration_id": iter_id,
            })
            iteration = iteration.model_copy(update={
                "stage": IterationStage.ITERATION_FAILED if not qualified else IterationStage.ITERATION_SUCCESS,
            })

        return self._finalize_iteration(family_id, iteration, family, score=score.total if score else 0.0)

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
        self.store.append_history(family_id, self._build_history_entry(iteration, family))

    def _build_history_entry(self, iteration: Iteration, family: IdeaFamily) -> str:
        """Build a rich history entry including judge feedback."""
        lines = [
            f"Iteration {iteration.iteration_id}: stage={iteration.stage}, "
            f"verdict={iteration.verdict}, qualified={iteration.qualified_improvement}, "
            f"strikes={family.strike_count}",
        ]

        if iteration.composite_score:
            lines.append(f"Score: {iteration.composite_score.total:.3f}")

        for output in iteration.judge_outputs:
            judge = getattr(output, "judge_type", "unknown")
            verdict = getattr(output, "verdict", "")
            reasoning = getattr(output, "reasoning_summary", "") or getattr(output, "reasoning", "")
            must_fix = getattr(output, "must_fix", []) or []

            lines.append(f"  [{judge}] {verdict}")
            if reasoning:
                lines.append(f"    Reasoning: {reasoning}")
            for item in must_fix:
                lines.append(f"    - MUST FIX: {item}")

        # Include guard failures if any
        for g in iteration.guard_results or []:
            if isinstance(g, dict) and not g.get("passed", True):
                name = g.get("guard_name", "unknown")
                violations = g.get("violations", [])
                lines.append(f"  [guard:{name}] FAILED: {', '.join(str(v) for v in violations)}")

        return "\n".join(lines)

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
