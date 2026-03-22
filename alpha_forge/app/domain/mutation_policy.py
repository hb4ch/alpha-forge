"""Mutation budget accounting, fork rules, and category policy."""

from __future__ import annotations

from alpha_forge.app.domain.models import IdeaFamily, MutationBudget, MutationProposal
from alpha_forge.app.domain.states import MutationCategory


def check_budget(family: IdeaFamily, category: MutationCategory) -> bool:
    """Check if budget remains for a mutation category."""
    return family.mutation_budget.remaining(category) > 0


def consume_budget(family: IdeaFamily, category: MutationCategory) -> IdeaFamily:
    """Consume one unit of mutation budget."""
    new_budget = family.mutation_budget.consume(category)
    return family.model_copy(update={"mutation_budget": new_budget})


def requires_fork(category: MutationCategory) -> bool:
    """Check if a mutation category always requires forking."""
    return category == MutationCategory.STRUCTURAL


def is_within_lanes(
    family: IdeaFamily,
    proposal: MutationProposal,
) -> bool:
    """Check if a proposed mutation falls within pre-registered lanes."""
    lanes = family.allowed_mutations
    category = proposal.category

    lane_map = {
        MutationCategory.HORIZON: lanes.horizon,
        MutationCategory.VENUE: lanes.venue,
        MutationCategory.REPRESENTATION: lanes.representation,
        MutationCategory.COMBINATION: lanes.combination,
        MutationCategory.REGIME: lanes.regime_filter,
    }

    allowed = lane_map.get(category)
    if allowed is None:
        return False

    # If no lanes are pre-registered for this category, reject
    if not allowed:
        return False

    return True


def validate_mutation(
    family: IdeaFamily,
    proposal: MutationProposal,
) -> tuple[bool, str]:
    """Validate a mutation proposal against policy.

    Returns (is_valid, reason).
    """
    if requires_fork(proposal.category):
        return False, f"Structural mutations require a fork, not in-family mutation"

    if not check_budget(family, proposal.category):
        return False, f"No budget remaining for {proposal.category.value} mutations"

    if not is_within_lanes(family, proposal):
        return False, f"Mutation not within pre-registered lanes for {proposal.category.value}"

    return True, "Mutation is within policy"
