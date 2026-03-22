"""Judge router: dispatches to the correct judges for each tier."""

from __future__ import annotations

import logging
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


def run_tier(
    tier: int,
    context: dict[str, Any],
    client: LLMClient | None = None,
) -> list[JudgeOutput]:
    """Run all judges for a given tier.

    Tier 1 (plan review): Leakage + Overfit + Realism
    Tier 2 (code review): Leakage + Code
    Tier 3 (result review): Result + Overfit + Realism

    All judge-specific output models inherit from JudgeOutput, so the
    return type is compatible.
    """
    client = client or LLMClient()
    outputs: list[JudgeOutput] = []

    if tier == 1:
        judges_and_kwargs: list[tuple] = [
            (LeakageJudge(client), {"plan": context.get("plan", ""), "code": context.get("code", ""), "history": context.get("history")}),
            (OverfitJudge(client), {"plan": context.get("plan", ""), "history": context.get("history"), "iteration_count": context.get("iteration_count", 0)}),
            (RealismJudge(client), {"plan": context.get("plan", ""), "config": context.get("config")}),
        ]
    elif tier == 2:
        judges_and_kwargs = [
            (LeakageJudge(client), {"code": context.get("code", ""), "diff": context.get("diff", ""), "history": context.get("history")}),
            (CodeJudge(client), {"code": context.get("code", ""), "diff": context.get("diff", "")}),
        ]
    elif tier == 3:
        judges_and_kwargs = [
            (ResultJudge(client), {"metrics": context.get("metrics"), "robustness": context.get("robustness"), "history": context.get("history")}),
            (OverfitJudge(client), {"metrics": context.get("metrics"), "history": context.get("history"), "iteration_count": context.get("iteration_count", 0)}),
            (RealismJudge(client), {"metrics": context.get("metrics"), "config": context.get("config")}),
        ]
    else:
        raise ValueError(f"Unknown tier: {tier}")

    for judge, kwargs in judges_and_kwargs:
        try:
            method_name = f"evaluate_{judge.judge_type}"
            method = getattr(judge, method_name)
            output = method(**kwargs)
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
    """Aggregate multiple judge verdicts into a single verdict.

    Rules:
    - Any REJECT -> REJECT
    - Any FORK_REQUIRED -> FORK_REQUIRED
    - Any REVISE -> REVISE
    - All APPROVE or APPROVE_WITH_CONSTRAINTS -> APPROVE_WITH_CONSTRAINTS if any constraints
    - All APPROVE -> APPROVE
    """
    if any(o.verdict == Verdict.REJECT for o in outputs):
        return Verdict.REJECT
    if any(o.verdict == Verdict.FORK_REQUIRED for o in outputs):
        return Verdict.FORK_REQUIRED
    if any(o.verdict == Verdict.REVISE for o in outputs):
        return Verdict.REVISE
    if any(o.verdict == Verdict.APPROVE_WITH_CONSTRAINTS for o in outputs):
        return Verdict.APPROVE_WITH_CONSTRAINTS
    return Verdict.APPROVE
