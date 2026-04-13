"""Judge router: dispatches to the correct judges for each tier."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from alpha_forge.app.agents.judge_code import CodeJudge
from alpha_forge.app.agents.judge_leakage import LeakageJudge
from alpha_forge.app.agents.judge_overfit import OverfitJudge
from alpha_forge.app.agents.judge_realism import RealismJudge
from alpha_forge.app.agents.judge_result import ResultJudge
from alpha_forge.app.agents.llm_client import LLMClient
from alpha_forge.app.domain.models import JudgeOutput
from alpha_forge.app.domain.states import Verdict

logger = logging.getLogger(__name__)

# Max concurrent LLM calls per tier
MAX_JUDGE_WORKERS = 3


def _run_single_judge(judge, kwargs) -> JudgeOutput:
    """Execute a single judge evaluation (called from a thread)."""
    method_name = f"evaluate_{judge.judge_type}"
    method = getattr(judge, method_name)
    return method(**kwargs)


def _build_judge(judge_cls, client: LLMClient | None):
    """Instantiate a judge, defaulting to its own configured role client."""
    if client is not None:
        return judge_cls(client)
    return judge_cls()


def run_tier(
    tier: int,
    context: dict[str, Any],
    client: LLMClient | None = None,
    max_workers: int = MAX_JUDGE_WORKERS,
) -> list[JudgeOutput]:
    """Run all judges for a given tier concurrently.

    Tier 1 (plan review): Leakage + Overfit + Realism
    Tier 2 (code review): Leakage + Code
    Tier 3 (result review): Result + Overfit + Realism

    Judges within a tier are independent and run in parallel via
    ThreadPoolExecutor (up to max_workers concurrent LLM calls).
    """
    if tier == 1:
        judges_and_kwargs: list[tuple] = [
            (_build_judge(LeakageJudge, client), {
                "plan": context.get("plan", ""),
                "code": context.get("code", ""),
                "history": context.get("history"),
                "diff": context.get("diff", ""),
            }),
            (_build_judge(OverfitJudge, client), {
                "plan": context.get("plan", ""),
                "history": context.get("history"),
                "code": context.get("code", ""),
            }),
            (_build_judge(RealismJudge, client), {"plan": context.get("plan", ""), "config": context.get("config")}),
        ]
    elif tier == 2:
        judges_and_kwargs = [
            (_build_judge(LeakageJudge, client), {
                "plan": context.get("plan", ""),
                "code": context.get("code", ""),
                "diff": context.get("diff", ""),
                "history": context.get("history"),
            }),
            (_build_judge(CodeJudge, client), {
                "plan": context.get("plan", ""),
                "code": context.get("code", ""),
                "diff": context.get("diff", ""),
                "changed_files": context.get("changed_files"),
                "allowed_files": context.get("allowed_files"),
                "forbidden_files": context.get("forbidden_files"),
            }),
        ]
    elif tier == 3:
        judges_and_kwargs = [
            (_build_judge(ResultJudge, client), {
                "metrics": context.get("metrics"),
                "robustness": context.get("robustness"),
                "history": context.get("history"),
                "score_decomposition": context.get("score_decomposition"),
                "prior_best": context.get("prior_best"),
            }),
            (_build_judge(OverfitJudge, client), {
                "metrics": context.get("metrics"),
                "history": context.get("history"),
                "code": context.get("code", ""),
            }),
            (_build_judge(RealismJudge, client), {"metrics": context.get("metrics"), "config": context.get("config")}),
        ]
    else:
        raise ValueError(f"Unknown tier: {tier}")

    workers = min(max_workers, len(judges_and_kwargs))
    outputs: list[JudgeOutput] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_judge = {
            pool.submit(_run_single_judge, judge, kwargs): judge
            for judge, kwargs in judges_and_kwargs
        }
        for future in as_completed(future_to_judge):
            judge = future_to_judge[future]
            try:
                output = future.result()
                outputs.append(output)
                logger.info("Judge %s verdict: %s", judge.judge_type, output.verdict)
            except Exception as e:
                logger.error("Judge %s failed: %s", judge.judge_type, e)
                outputs.append(JudgeOutput(
                    judge_type=judge.judge_type,
                    verdict=Verdict.REVISE,
                    reasoning_summary=f"Judge execution failed: {e}",
                ))

    return outputs


def aggregate_verdict(outputs: list[JudgeOutput]) -> Verdict:
    """Unified aggregation for all tiers.

    No judge can REJECT or FORK — those verdicts are clamped to REVISE
    as a safety net (judges should no longer emit them after prompt updates).

    Rules:
    - Any REVISE (or clamped REJECT/FORK_REQUIRED) → REVISE
    - Any APPROVE_WITH_CONSTRAINTS → APPROVE_WITH_CONSTRAINTS
    - All APPROVE → APPROVE
    """
    # Clamp legacy REJECT/FORK_REQUIRED to REVISE
    for o in outputs:
        if o.verdict in (Verdict.REJECT, Verdict.FORK_REQUIRED):
            logger.warning(
                "Judge %s returned %s — clamping to REVISE",
                o.judge_type, o.verdict,
            )
            o.verdict = Verdict.REVISE

    if any(o.verdict == Verdict.REVISE for o in outputs):
        return Verdict.REVISE
    if any(o.verdict == Verdict.APPROVE_WITH_CONSTRAINTS for o in outputs):
        return Verdict.APPROVE_WITH_CONSTRAINTS
    return Verdict.APPROVE
