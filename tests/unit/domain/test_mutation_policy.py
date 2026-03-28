"""Tests for alpha_forge.app.domain.mutation_policy."""

from __future__ import annotations

import pytest

from alpha_forge.app.domain.models import AllowedMutations, MutationBudget, MutationProposal
from alpha_forge.app.domain.mutation_policy import (
    check_budget,
    consume_budget,
    is_within_lanes,
    requires_fork,
    validate_mutation,
)
from alpha_forge.app.domain.states import MutationCategory
from tests.conftest import make_family


def _make_proposal(category: MutationCategory, family_id: str = "fam_001") -> MutationProposal:
    """Helper to create a MutationProposal with required fields."""
    return MutationProposal(
        family_id=family_id,
        category=category,
        description="test mutation",
        reason="test reason",
        proposed_change="change something",
        falsification_test="check it works",
    )


class TestCheckBudget:
    """check_budget: True when budget > 0, False when 0."""

    def test_true_when_budget_available(self) -> None:
        family = make_family()
        assert check_budget(family, MutationCategory.HORIZON) is True
        assert check_budget(family, MutationCategory.VENUE) is True
        assert check_budget(family, MutationCategory.REPRESENTATION) is True

    def test_false_when_budget_zero(self) -> None:
        family = make_family()
        assert check_budget(family, MutationCategory.STRUCTURAL) is False

    def test_false_after_exhaustion(self) -> None:
        family = make_family(mutation_budget=MutationBudget(horizon=0))
        assert check_budget(family, MutationCategory.HORIZON) is False


class TestConsumeBudget:
    """consume_budget decrements and raises on exhaustion."""

    def test_decrements_correct_category(self) -> None:
        family = make_family()
        updated = consume_budget(family, MutationCategory.HORIZON)
        assert updated.mutation_budget.remaining(MutationCategory.HORIZON) == 1
        # Other categories unchanged
        assert updated.mutation_budget.remaining(MutationCategory.VENUE) == 2
        assert updated.mutation_budget.remaining(MutationCategory.REPRESENTATION) == 1

    def test_raises_when_exhausted(self) -> None:
        family = make_family()
        with pytest.raises(ValueError, match="No budget remaining"):
            consume_budget(family, MutationCategory.STRUCTURAL)


class TestRequiresFork:
    """requires_fork: True only for STRUCTURAL."""

    def test_structural_requires_fork(self) -> None:
        assert requires_fork(MutationCategory.STRUCTURAL) is True

    def test_non_structural_do_not_require_fork(self) -> None:
        for cat in MutationCategory:
            if cat != MutationCategory.STRUCTURAL:
                assert requires_fork(cat) is False, f"{cat} should not require fork"


class TestIsWithinLanes:
    """is_within_lanes checks pre-registered lanes."""

    def test_true_when_lanes_registered(self) -> None:
        family = make_family()  # default has horizon=["5min","15min"]
        proposal = _make_proposal(MutationCategory.HORIZON)
        assert is_within_lanes(family, proposal) is True

    def test_false_when_lanes_empty(self) -> None:
        family = make_family(
            allowed_mutations=AllowedMutations(
                horizon=[],
                venue=[],
                representation=[],
                combination=[],
                regime_filter=[],
            )
        )
        proposal = _make_proposal(MutationCategory.HORIZON)
        assert is_within_lanes(family, proposal) is False

    def test_false_for_structural(self) -> None:
        family = make_family()
        proposal = _make_proposal(MutationCategory.STRUCTURAL)
        assert is_within_lanes(family, proposal) is False


class TestValidateMutation:
    """validate_mutation rejects/approves based on policy."""

    def test_rejects_structural(self) -> None:
        family = make_family(mutation_budget=MutationBudget(structural=1))
        proposal = _make_proposal(MutationCategory.STRUCTURAL)
        valid, reason = validate_mutation(family, proposal)
        assert valid is False
        assert "fork" in reason.lower() or "require a fork" in reason.lower()

    def test_rejects_no_budget(self) -> None:
        family = make_family(mutation_budget=MutationBudget(horizon=0))
        proposal = _make_proposal(MutationCategory.HORIZON)
        valid, reason = validate_mutation(family, proposal)
        assert valid is False
        assert "No budget remaining" in reason

    def test_rejects_out_of_lane(self) -> None:
        family = make_family(
            allowed_mutations=AllowedMutations(
                horizon=[],
                venue=["binance"],
                representation=["ema"],
                combination=["equal_weight"],
                regime_filter=["volatility"],
            )
        )
        proposal = _make_proposal(MutationCategory.HORIZON)
        valid, reason = validate_mutation(family, proposal)
        assert valid is False
        assert "not within pre-registered lanes" in reason

    def test_approves_valid_mutation(self) -> None:
        family = make_family()
        proposal = _make_proposal(MutationCategory.HORIZON)
        valid, reason = validate_mutation(family, proposal)
        assert valid is True
        assert "within policy" in reason
