"""Seed Judge: evaluates whether a distilled seed is worth creating a family for."""

from __future__ import annotations

from alpha_forge.app.agents.base_judge import BaseJudge
from alpha_forge.app.domain.models import SeedCard, SeedJudgeOutput


class SeedJudge(BaseJudge):
    judge_type = "seed"
    prompt_file = "seed_judge.md"

    def build_user_prompt(self, *, card: SeedCard, existing_families: list[str] | None = None) -> str:
        families_str = ", ".join(existing_families) if existing_families else "none"
        return f"""## Seed Card
```json
{card.model_dump_json(indent=2)}
```

## Existing Families
{families_str}
"""

    def evaluate_seed(
        self,
        card: SeedCard,
        existing_families: list[str] | None = None,
    ) -> SeedJudgeOutput:
        raw = self.evaluate_raw(card=card, existing_families=existing_families)
        return SeedJudgeOutput.model_validate(raw)
