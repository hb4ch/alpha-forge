"""Tests for the split isolation guard."""

from __future__ import annotations

import pytest

from alpha_forge.app.domain.states import FamilyState
from alpha_forge.app.guards.split_guard import check_split_isolation
from tests.conftest import make_family


class TestSplitGuard:
    """Tests for check_split_isolation."""

    def test_allows_validation_in_any_state(self) -> None:
        """Allows requested_split='validation' in any state."""
        for state in FamilyState:
            family = make_family(state=state)
            result = check_split_isolation(family, requested_split="validation")
            assert result.passed is True, f"Should allow validation in state {state}"

    @pytest.mark.parametrize(
        "state",
        [
            FamilyState.QUEUED,
            FamilyState.PLAN_IN_REVIEW,
            FamilyState.CODING,
            FamilyState.CODE_IN_REVIEW,
            FamilyState.CODE_APPROVED,
            FamilyState.GUARDS_RUNNING,
            FamilyState.BACKTEST_RUNNING,
            FamilyState.RESULTS_IN_REVIEW,
            FamilyState.ITERATE,
            FamilyState.NEW,
            FamilyState.PLAN_REVISION_REQUIRED,
            FamilyState.CODE_REVISION_REQUIRED,
            FamilyState.PLAN_APPROVED,
        ],
    )
    def test_blocks_holdout_in_pre_promotion_states(self, state: FamilyState) -> None:
        """Blocks holdout access in pre-promotion states."""
        family = make_family(state=state)
        result = check_split_isolation(family, requested_split="holdout")

        assert result.passed is False
        assert result.is_red_strike is True
        assert any("Holdout data requested" in v for v in result.violations)

    @pytest.mark.parametrize(
        "state",
        [
            FamilyState.HOLDOUT_RUNNING,
            FamilyState.PROMOTE_TO_PAPER,
            FamilyState.PAPER_FORWARD_RUNNING,
            FamilyState.HUMAN_REVIEW,
            FamilyState.DONE,
        ],
    )
    def test_allows_holdout_in_post_promotion_states(self, state: FamilyState) -> None:
        """Allows holdout access in post-promotion states."""
        family = make_family(state=state)
        result = check_split_isolation(family, requested_split="holdout")

        assert result.passed is True
        assert result.violations == []
