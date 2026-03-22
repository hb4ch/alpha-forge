"""Overfitting Judge: detects result-chasing, hidden sweeps, excessive degrees of freedom."""

from __future__ import annotations

from alpha_forge.app.agents.base_judge import BaseJudge
from alpha_forge.app.domain.models import OverfitJudgeOutput


class OverfitJudge(BaseJudge):
    judge_type = "overfit"
    prompt_file = "overfit_judge.md"

    def build_user_prompt(
        self,
        *,
        plan: str = "",
        metrics: dict | None = None,
        history: list[str] | None = None,
        iteration_count: int = 0,
        code: str = "",
    ) -> str:
        history_str = "\n".join(history) if history else "No prior history."
        metrics_str = str(metrics) if metrics else "No metrics yet."
        return f"""## Plan
{plan}

## Code
```python
{code}
```

## Metrics
{metrics_str}

## Iteration Count
{iteration_count}

## Prior History
{history_str}
"""

    def evaluate_overfit(self, **kwargs) -> OverfitJudgeOutput:
        return self.evaluate(OverfitJudgeOutput, **kwargs)
