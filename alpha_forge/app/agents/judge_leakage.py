"""Leakage Judge: detects time leakage, label leakage, split contamination."""

from __future__ import annotations

from alpha_forge.app.agents.base_judge import BaseJudge
from alpha_forge.app.domain.models import LeakageJudgeOutput


class LeakageJudge(BaseJudge):
    judge_type = "leakage"
    prompt_file = "leakage_judge.md"

    def build_user_prompt(
        self,
        *,
        code: str = "",
        plan: str = "",
        history: list[str] | None = None,
        diff: str = "",
    ) -> str:
        history_str = "\n".join(history) if history else "No prior history."
        return f"""## Plan
{plan}

## Code
```python
{code}
```

## Code Diff
```diff
{diff}
```

## Prior History
{history_str}
"""

    def evaluate_leakage(self, **kwargs) -> LeakageJudgeOutput:
        return self.evaluate(LeakageJudgeOutput, **kwargs)
