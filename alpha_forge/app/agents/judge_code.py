"""Code Smell Judge: detects suspicious implementation patterns."""

from __future__ import annotations

from alpha_forge.app.agents.base_judge import BaseJudge
from alpha_forge.app.domain.models import CodeJudgeOutput


class CodeJudge(BaseJudge):
    judge_type = "code"
    prompt_file = "code_judge.md"

    def build_user_prompt(
        self,
        *,
        code: str = "",
        diff: str = "",
        changed_files: list[str] | None = None,
        allowed_files: list[str] | None = None,
        forbidden_files: list[str] | None = None,
        plan: str = "",
    ) -> str:
        changed_str = ", ".join(changed_files) if changed_files else "unknown"
        allowed_str = ", ".join(allowed_files) if allowed_files else "not specified"
        forbidden_str = ", ".join(forbidden_files) if forbidden_files else "not specified"
        return f"""## Plan
{plan}

## Full Code
```python
{code}
```

## Diff (changes from last iteration)
```diff
{diff}
```

## Changed Files
{changed_str}

## Allowed Files
{allowed_str}

## Forbidden Files
{forbidden_str}
"""

    def evaluate_code(self, **kwargs) -> CodeJudgeOutput:
        return self.evaluate(CodeJudgeOutput, **kwargs)
