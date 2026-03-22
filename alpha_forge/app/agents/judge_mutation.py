"""Mutation Judge: evaluates whether a proposed mutation is disciplined refinement."""

from __future__ import annotations

import json

from alpha_forge.app.agents.base_judge import BaseJudge
from alpha_forge.app.domain.models import IdeaFamily, MutationJudgeOutput, MutationProposal


class MutationJudge(BaseJudge):
    judge_type = "mutation"
    prompt_file = "mutation_judge.md"

    def build_user_prompt(
        self,
        *,
        proposal: MutationProposal,
        family: IdeaFamily,
    ) -> str:
        return f"""## Family Context
- Family ID: {family.family_id}
- Base Hypothesis: {family.base_hypothesis}
- Original Mechanism: {family.mechanism}
- Current Iteration: {family.current_iteration}
- Strike Count: {family.strike_count}
- Mutation Budget: {json.dumps(family.mutation_budget.model_dump())}
- Allowed Mutations: {json.dumps(family.allowed_mutations.model_dump())}

## Mutation Proposal
- Category: {proposal.category}
- Description: {proposal.description}
- Reason: {proposal.reason}
- Proposed Change: {proposal.proposed_change}
- Falsification Test: {proposal.falsification_test}
"""

    def evaluate_mutation(self, **kwargs) -> MutationJudgeOutput:
        return self.evaluate(MutationJudgeOutput, **kwargs)
