"""Market Realism Judge: evaluates fee realism, turnover, liquidity, execution plausibility."""

from __future__ import annotations

from alpha_forge.app.agents.base_judge import BaseJudge
from alpha_forge.app.domain.models import RealismJudgeOutput


class RealismJudge(BaseJudge):
    judge_type = "realism"
    prompt_file = "realism_judge.md"

    def build_user_prompt(
        self,
        *,
        metrics: dict | None = None,
        config: dict | None = None,
        plan: str = "",
    ) -> str:
        metrics_str = str(metrics) if metrics else "No metrics."
        config_str = str(config) if config else "No config."
        return f"""## Plan
{plan}

## Backtest Metrics
{metrics_str}

## Configuration (fees, slippage, capital)
{config_str}
"""

    def evaluate_realism(self, **kwargs) -> RealismJudgeOutput:
        return self.evaluate(RealismJudgeOutput, **kwargs)
