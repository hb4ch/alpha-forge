"""Result Judge: evaluates backtest results for suspicious patterns."""

from __future__ import annotations

from alpha_forge.app.agents.base_judge import BaseJudge
from alpha_forge.app.domain.models import ResultJudgeOutput


class ResultJudge(BaseJudge):
    judge_type = "result"
    prompt_file = "result_judge.md"

    def build_user_prompt(
        self,
        *,
        metrics: dict | None = None,
        robustness: dict | None = None,
        history: list[str] | None = None,
        score_decomposition: dict | None = None,
        prior_best: float | None = None,
    ) -> str:
        metrics_str = str(metrics) if metrics else "No metrics."
        robustness_str = str(robustness) if robustness else "No robustness results."
        history_str = "\n".join(history) if history else "No prior history."
        score_str = str(score_decomposition) if score_decomposition else "Not computed."
        prior_str = str(prior_best) if prior_best is not None else "None"
        return f"""## Backtest Metrics
{metrics_str}

## Robustness Battery Results
{robustness_str}

## Score Decomposition
{score_str}

## Prior Family Best Score
{prior_str}

## Prior History
{history_str}
"""

    def evaluate_result(self, **kwargs) -> ResultJudgeOutput:
        return self.evaluate(ResultJudgeOutput, **kwargs)
