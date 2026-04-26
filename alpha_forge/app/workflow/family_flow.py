"""Family lifecycle flow: drives one iteration of a family through the full pipeline.

Plan → Judge → Code → Judge → Guards → Backtest → Robustness → Judge → Score → Decide
"""

from __future__ import annotations

import difflib
import logging
import re
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
from alpha_forge.app.domain.scoring import (
    ROLLBACK_DEGRADATION_THRESHOLD,
    compute_composite_score,
    is_qualified_improvement,
    is_score_improvement,
)
from alpha_forge.app.domain.states import FamilyState, IterationStage, Verdict
from alpha_forge.app.domain.strikes import is_budget_exhausted
from alpha_forge.app.guards.runner import any_failed, run_all_guards
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

# Maximum tier-2 revision rounds before the loop force-promotes the iteration
# to guards/backtest with the unresolved code-judge complaints recorded as
# deferred items. Prevents the code judge from deadlocking the loop.
MAX_CODE_REVISIONS = 3

class BudgetExhaustedError(Exception):
    """Raised when a family has exhausted its iteration budget."""

    def __init__(self, family_id: str, max_iterations: int, best_score: float) -> None:
        self.family_id = family_id
        self.max_iterations = max_iterations
        self.best_score = best_score
        super().__init__(
            f"Family {family_id} exhausted iteration budget "
            f"({max_iterations} iterations, best score: {best_score:.3f})"
        )


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

    # Priority order for must_fix items: safety-critical first
    _JUDGE_PRIORITY: dict[str, int] = {
        "leakage": 0,
        "code": 1,
        "overfit": 2,
        "realism": 3,
        "result": 4,
    }

    @staticmethod
    def _detect_feedback_conflicts(
        items: list[tuple[str, str]],
    ) -> tuple[list[str], set[int]]:
        """Detect contradictory advice across judges and return conflict notes.

        Args:
            items: list of (judge_type, must_fix_text) tuples

        Returns:
            (conflict_messages, indices_to_remove) — conflict-framed messages and
            indices of original items that were merged into conflicts.
        """
        _REMOVE_VERBS = re.compile(
            r'\b(drop|remove|exclude|eliminate|strip|disable)\b', re.IGNORECASE,
        )
        _KEEP_VERBS = re.compile(
            r'\b(keep|add|include|restore|maintain|reintroduce|re-add)\b', re.IGNORECASE,
        )
        _TARGET_PATTERN = re.compile(r'\b([A-Z]{2,}(?:USDT)?)\b')

        # Classify each item as remove/keep with a target
        classified: list[tuple[str, str, str, int]] = []  # (action, target, judge, idx)
        for idx, (judge, text) in enumerate(items):
            targets = _TARGET_PATTERN.findall(text)
            if not targets:
                continue
            target = targets[0]
            if target in ("MUST", "FIX", "NOT", "AND", "THE", "ALL", "NAN", "SOL"):
                # Skip common false-positive words; keep SOL only if SOLUSDT form
                if target != "SOL" or "SOLUSDT" not in text:
                    if target not in ("SOL",):
                        continue
            if _REMOVE_VERBS.search(text):
                classified.append(("remove", target, judge, idx))
            elif _KEEP_VERBS.search(text):
                classified.append(("keep", target, judge, idx))

        conflicts: list[str] = []
        consumed: set[int] = set()
        for i, (action_a, target_a, judge_a, idx_a) in enumerate(classified):
            for j, (action_b, target_b, judge_b, idx_b) in enumerate(classified):
                if j <= i or judge_a == judge_b:
                    continue
                if target_a != target_b:
                    continue
                if action_a == action_b:
                    continue
                # Found opposing advice on the same target
                item_a = items[idx_a][1][:120]
                item_b = items[idx_b][1][:120]
                conflicts.append(
                    f"JUDGES DISAGREE on {target_a}: [{judge_a}] says: {item_a}... "
                    f"[{judge_b}] says: {item_b}... "
                    f"Make a principled choice and commit — do not flip-flop between iterations."
                )
                consumed.add(idx_a)
                consumed.add(idx_b)

        return conflicts, consumed

    @staticmethod
    def _extract_judge_feedback(
        judge_outputs: list,
        max_items: int = 3,
    ) -> list[str]:
        """Extract must_fix items and reasoning from judge outputs into actionable feedback.

        Prioritizes safety-critical items (leakage > code > overfit > realism > result),
        detects conflicting advice across judges, and caps total MUST_FIX items to prevent
        the researcher from attempting too many fixes at once.
        """
        # Collect reasoning summaries (always included, not capped)
        reasoning_parts: list[str] = []
        # Collect all must_fix items with attribution for prioritization
        all_items: list[tuple[str, str, int]] = []  # (judge_type, must_fix_text, priority)
        for output in judge_outputs:
            judge_type = getattr(output, "judge_type", "unknown")
            verdict = getattr(output, "verdict", "")
            reasoning = getattr(output, "reasoning_summary", "") or getattr(output, "reasoning", "")
            must_fix = getattr(output, "must_fix", []) or []
            if reasoning:
                reasoning_parts.append(f"[{judge_type}] {verdict}\n  Reasoning: {reasoning}")
            priority = FamilyFlow._JUDGE_PRIORITY.get(judge_type, 5)
            for item in must_fix:
                all_items.append((judge_type, item, priority))

        # Detect and resolve conflicting advice
        conflict_input = [(jt, text) for jt, text, _ in all_items]
        conflict_msgs, consumed_indices = FamilyFlow._detect_feedback_conflicts(conflict_input)

        # Remove consumed items and sort remaining by priority
        remaining = [
            (jt, text, pri)
            for idx, (jt, text, pri) in enumerate(all_items)
            if idx not in consumed_indices
        ]
        remaining.sort(key=lambda x: x[2])

        # Build output: reasoning summaries + conflict messages + prioritized must_fix
        feedback: list[str] = []
        if reasoning_parts:
            feedback.extend(reasoning_parts)
        for msg in conflict_msgs:
            feedback.append(msg)

        items_added = len(conflict_msgs)
        deferred = 0
        for judge_type, item, _ in remaining:
            if items_added >= max_items:
                deferred += 1
                continue
            feedback.append(f"[{judge_type}] MUST FIX: {item}")
            items_added += 1

        if deferred > 0:
            feedback.append(f"({deferred} lower-priority items deferred to next iteration)")

        return feedback

    def _detect_simplification_needs(self, family_id: str) -> list[str]:
        """Scan prior judge outputs for dead-complexity flags.

        Returns a list of human-readable simplification directives.
        """
        items: list[str] = []
        try:
            prev_iter = self.store.read_iteration(family_id)
            if not prev_iter or not prev_iter.judge_outputs:
                return items
            for output in prev_iter.judge_outputs:
                dof_risk = getattr(output, "degrees_of_freedom_risk", "")
                if dof_risk in ("high", "critical"):
                    for fix in getattr(output, "must_fix", []) or []:
                        items.append(fix)
                reasoning = getattr(output, "reasoning_summary", "") or ""
                lower = reasoning.lower()
                if "0% short exposure" in lower or "dead code" in lower or "dead complexity" in lower:
                    items.append("Strip regime/short complexity that produces 0% activation")
                if "never activates" in lower or "mechanism is dead" in lower:
                    items.append("Remove mechanism components that never activate in backtests")
        except Exception:
            pass
        # Deduplicate
        return list(dict.fromkeys(items))

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
        code_changed: bool = True,
        code: str = "",
        plan: str = "",
        code_judge_deferred: list[str] | None = None,
    ) -> dict[str, Any]:
        """Assemble tier-3 review context."""
        ctx = {
            "metrics": {r.symbol: r.all_metrics for r in bt_results},
            "robustness": robustness.model_dump(),
            "history": prior_feedback,
            "config": self._load_costs_config(),
            "prior_best": family.best_score,
            "code": code,
            "plan": plan,
        }
        if not code_changed:
            ctx["code_changed"] = False
            ctx["code_change_note"] = (
                "Code was unchanged from the prior iteration. "
                "Identical results are expected and should NOT be "
                "treated as evidence of stagnation or overfit."
            )
        if code_judge_deferred:
            ctx["code_judge_deferred"] = list(code_judge_deferred)
            ctx["code_judge_deferred_note"] = (
                f"Tier-2 code judge requested {len(code_judge_deferred)} "
                f"MUST_FIX items that could not be addressed after "
                f"{MAX_CODE_REVISIONS} revision attempts. Score this run on its "
                "merits; if the deferred items would plausibly explain the "
                "result, downgrade. If not, treat them as advisory."
            )
        return ctx

    def _get_history_context(self, family_id: str) -> list[str]:
        """Get recent iteration history (last 3) plus a score trajectory summary.

        Prevents judge poisoning from accumulated failure history by
        windowing: only the most recent 3 iteration entries are shown
        in full; older entries are reduced to a score trajectory line.
        Always surfaces the best-scoring iteration as an empirical anchor so
        judges can recognize regressions from a previously working baseline.
        """
        history_path = self.store.root / "families" / family_id / "HISTORY.md"
        if not history_path.exists():
            return []

        full_text = history_path.read_text()
        # Each iteration entry starts with ## [timestamp]
        sections = re.split(r'(?=^## \[)', full_text, flags=re.MULTILINE)
        entries = sections[1:] if len(sections) > 1 else []

        # Identify the best-scoring entry across the full history so the
        # judges anchor on what already worked, not the most recent attempt.
        best_anchor = ""
        scored_entries: list[tuple[float, str]] = []
        for entry in entries:
            m = re.search(r'Score:\s*(-?[\d.]+)', entry)
            if m:
                scored_entries.append((float(m.group(1)), entry))
        if scored_entries:
            best_val, best_entry = max(scored_entries, key=lambda x: x[0])
            best_anchor = (
                f"\n[BEST IN LINEAGE: scored {best_val:+.3f} — what worked there is "
                f"empirically validated; regressing from this baseline requires "
                f"justification]\n{best_entry[:600]}\n"
            )

        if len(entries) <= 3:
            # Short history — show full text, prepend the best anchor for emphasis.
            prefix = (best_anchor + "\n") if best_anchor else ""
            return [prefix + full_text]

        # Extract score trajectory from older entries, filtering out guard/error failures
        older = entries[:-3]
        recent = entries[-3:]

        scored = [e for e in older if re.search(r'Score:\s*-?[\d.]+', e)]

        scores: list[str] = []
        for entry in scored:
            score_match = re.search(r'Score:\s*(-?[\d.]+)', entry)
            if score_match:
                scores.append(score_match.group(1))
        score_trajectory = " → ".join(scores) if scores else "no scores"
        summary = (
            f"[HISTORY: {len(scored)} scored iterations, scores: {score_trajectory}]"
            f"{best_anchor}\n"
        )

        return [summary + "".join(recent)]

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

        # Budget check — archive if exhausted
        if family.state in NEW_ITERATION_STATES and is_budget_exhausted(family):
            logger.info(
                "Family %s exhausted iteration budget (%d/%d). Best score: %.3f",
                family.family_id, family.current_iteration, family.max_iterations, family.best_score,
            )
            self._append_history(
                family.family_id,
                f"Iteration budget ({family.max_iterations}) exhausted. Best score: {family.best_score:.3f}",
            )
            family = family.model_copy(update={"state": FamilyState.BUDGET_EXHAUSTED})
            self.store.write_family(family)
            self._update_global_state(family)
            raise BudgetExhaustedError(family.family_id, family.max_iterations, family.best_score)

        if family.state in NEW_ITERATION_STATES:
            family = family.model_copy(update={"current_iteration": family.current_iteration + 1})
            self.store.write_family(family)
            self._update_global_state(family)

            # Use next_iteration_mode to decide routing
            mode = family.next_iteration_mode or "replan"
            if mode in ("revise_code", "adjust_config") and existing and existing.plan:
                # Skip plan phase — reuse previous plan, go straight to code
                iteration = Iteration(
                    iteration_id=f"iter_{family.current_iteration}",
                    family_id=family.family_id,
                    proposal_type=existing.proposal_type if existing else "initial",
                    mutation_category=existing.mutation_category if existing else None,
                    plan=existing.plan,
                )
                return family, iteration, False, True
            else:
                # Full replan — draft new plan
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
            # Increment the tier-2 revision counter so the deadlock escape in
            # run_iteration can detect when this iteration has been bouncing.
            revision_count = (existing.code_revision_count or 0) + 1
            iteration = Iteration(
                iteration_id=existing.iteration_id,
                family_id=existing.family_id,
                proposal_type=existing.proposal_type,
                mutation_category=existing.mutation_category,
                plan=existing.plan,
                code_revision_count=revision_count,
                code_judge_deferred=list(existing.code_judge_deferred or []),
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
                    prev_iter = self.store.read_iteration(family_id)
                    if prev_iter and prev_iter.judge_outputs:
                        judge_fb = self._extract_judge_feedback(prev_iter.judge_outputs)
                        if judge_fb:
                            plan_feedback.append(
                                "## REVISION REQUIRED — Judge feedback to address:\n" + "\n\n".join(judge_fb)
                            )
                except Exception:
                    pass
            # Detect dead complexity from prior judge outputs → simplification directive
            simplify_items = self._detect_simplification_needs(family_id)
            if simplify_items:
                plan_feedback.append(
                    "## SIMPLIFICATION REQUIRED\n"
                    "Judges have flagged the following dead or unnecessary complexity. "
                    "Your plan MUST remove it entirely (not tune it):\n"
                    + "\n".join(f"- {item}" for item in simplify_items)
                )
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
            # Tier-1 gets a lean trajectory summary, not full history
            tier1_history = [f"Best score so far: {family.best_score:.3f}"]
            tier1_outputs = run_tier(1, {
                "plan": plan,
                "history": tier1_history,
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
            if tier1_verdict == Verdict.REVISE:
                logger.info("[%s] Plan revision requested by tier-1 judges", iter_id)
                family = self._transition_family(family, FamilyEvent.PLAN_REVISION_REQUIRED)
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
        iteration_mode = family.next_iteration_mode or "replan"
        # When revising with a known good score, emphasize targeted fixes
        if family.best_score > 0 and iteration_mode in ("revise_code", "adjust_config"):
            code_feedback.insert(0, (
                f"IMPORTANT: Your best score is {family.best_score:.3f}. "
                "Make TARGETED fixes only — address the top issues below. "
                "Do NOT restructure working code."
            ))
        judge_feedback = self._extract_judge_feedback(iteration.judge_outputs)
        if judge_feedback:
            code_feedback.append("## Judge feedback from this iteration (address these):\n" + "\n\n".join(judge_feedback))
        # Pass existing code for revision modes so researcher can see what to modify
        pass_existing = (
            family.state == FamilyState.CODE_REVISION_REQUIRED
            or iteration_mode in ("revise_code", "adjust_config")
        )
        code_write_context = self._build_code_write_context(
            family,
            plan,
            code_feedback,
            existing_code=old_code if pass_existing else None,
        )
        code_files = self.researcher.write_code(
            code_write_context["family"],
            code_write_context["plan"],
            code_write_context["prior_feedback"],
            existing_code=code_write_context["existing_code"],
            mode=iteration_mode,
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
        if tier2_verdict == Verdict.REVISE:
            if iteration.code_revision_count >= MAX_CODE_REVISIONS:
                # Deadlock escape: the code judge has REVISEd this iteration
                # MAX_CODE_REVISIONS times in a row. Force-promote with the
                # unresolved complaints recorded as deferred items so the
                # result judge can factor them in. Deterministic guards still
                # gate everything downstream.
                deferred = [
                    f"[{o.judge_type}] {fix}"
                    for o in tier2_outputs
                    if o.verdict == Verdict.REVISE
                    for fix in (o.must_fix or [])
                ]
                iteration = iteration.model_copy(update={"code_judge_deferred": deferred})
                self.store.write_iteration(iteration)
                self._append_history(
                    family_id,
                    f"Iteration {iter_id}: code-judge deadlock after "
                    f"{iteration.code_revision_count} revisions; force-promoted "
                    f"with {len(deferred)} deferred items.",
                )
                logger.warning(
                    "[%s] Code-judge deadlock escape triggered (%d revisions); "
                    "promoting to guards with deferred items: %s",
                    iter_id, iteration.code_revision_count, deferred[:2],
                )
                family = self._transition_family(family, FamilyEvent.CODE_APPROVED)
                # fall through to guards/backtest stage
            else:
                logger.info("[%s] Code revision requested by tier-2 judges", iter_id)
                family = self._transition_family(family, FamilyEvent.CODE_REVISION_REQUIRED)
                iteration = iteration.model_copy(update={
                    "stage": IterationStage.ITERATION_FAILED,
                    "verdict": tier2_verdict,
                })
                return self._finalize_iteration(family_id, iteration, family)
        else:
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
            violations = [v for g in guard_results for v in g.violations]
            logger.warning("[%s] Guards failed: %s", iter_id, violations)
            self._append_history(
                family_id,
                f"Iteration {iter_id}: GUARD FAILURE\n  {', '.join(violations[:5])}",
            )
            family = self._transition_family(family, FamilyEvent.GUARDS_FAILED)
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
        # Determine if code actually changed this iteration
        current_code = self._read_research_code_dict(family_id)
        code_changed = old_code != current_code if old_code else True
        result_review_context = self._build_result_review_context(
            bt_results,
            robustness,
            prior_feedback,
            iter_num,
            family,
            code_changed=code_changed,
            code=self._format_code_bundle(current_code) if current_code else "",
            plan=plan,
            code_judge_deferred=iteration.code_judge_deferred,
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

        # Track raw best score (any improvement, unconditional)
        if is_score_improvement(score, family.best_score):
            logger.info("[%s] New best score: %.3f (was %.3f)", iter_id, score.total, family.best_score)
            family = family.model_copy(update={"best_score": score.total})
            # Save known-good code as checkpoint
            good_code = self._read_research_code_dict(family_id)
            if good_code:
                self.artifact_store.save_checkpoint(family_id, good_code)

        # Decide next action based on score quality.
        # Promotion depends on qualified improvement (score + robustness), not tier-3 verdict.
        # Tier-3 coaching feedback is still recorded but does not block promotion.
        if qualified:
            logger.info("[%s] Qualified improvement! Score: %.3f → holdout promotion", iter_id, score.total)
            family = self._transition_family(family, FamilyEvent.RESULT_APPROVED, context={
                "score": score.total,
            })
            iteration = iteration.model_copy(update={
                "stage": IterationStage.ITERATION_SUCCESS,
            })
        else:
            # Not yet promotable — iterate with coaching feedback
            if tier3_verdict == Verdict.REVISE:
                logger.info("[%s] Tier-3 revision requested. Score: %.3f, iterating with coaching", iter_id, score.total)
            else:
                logger.info("[%s] No qualified improvement. Score: %.3f, iterating", iter_id, score.total)

            # Rollback to checkpoint only on severe degradation
            if code_changed and family.best_score > 0:
                if score.total < family.best_score * ROLLBACK_DEGRADATION_THRESHOLD:
                    checkpoint = self.artifact_store.load_checkpoint(family_id)
                    if checkpoint:
                        research_dir = self.store.root / "families" / family_id / "research"
                        for fname, content in checkpoint.items():
                            (research_dir / fname).write_text(content)
                        logger.info("[%s] Severe degradation — rolled back code to checkpoint", iter_id)

            # Ask researcher to choose iteration mode for next cycle
            judge_fb = self._extract_judge_feedback(tier3_outputs)
            try:
                mode_decision = self.researcher.choose_iteration_mode(judge_fb, family)
                next_mode = mode_decision.get("mode", "replan")
                logger.info("[%s] Researcher chose iteration mode: %s (%s)", iter_id, next_mode, mode_decision.get("reasoning", ""))
            except Exception as e:
                logger.warning("[%s] Failed to get iteration mode, defaulting to replan: %s", iter_id, e)
                next_mode = "replan"
            family = family.model_copy(update={"next_iteration_mode": next_mode})

            family = self._transition_family(family, FamilyEvent.ITERATE)
            iteration = iteration.model_copy(update={
                "stage": IterationStage.ITERATION_FAILED,
                "iteration_mode": next_mode,
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
            "best_score": family.best_score,
            "family_state": str(family.state),
        }
        self.store.write_ledger_entry(family_id, iter_id, entry)
        self.store.append_history(family_id, self._build_history_entry(iteration, family))

    def _build_history_entry(self, iteration: Iteration, family: IdeaFamily) -> str:
        """Build a rich history entry including judge feedback."""
        lines = [
            f"Iteration {iteration.iteration_id}: stage={iteration.stage}, "
            f"verdict={iteration.verdict}, qualified={iteration.qualified_improvement}",
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
            "best_score": family.best_score,
            "best_qualified_score": family.best_qualified_score,
            "last_transition_at": datetime.now(tz=timezone.utc).isoformat(),
        })
