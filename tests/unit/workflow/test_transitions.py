"""Comprehensive tests for the TransitionEngine state machine."""

from __future__ import annotations

import pytest

from alpha_forge.app.domain.events import TRANSITION_TABLE, FamilyEvent
from alpha_forge.app.domain.states import FamilyState
from alpha_forge.app.workflow.transitions import (
    IllegalTransitionError,
    SideEffect,
    TransitionEngine,
    TransitionResult,
)
from tests.conftest import make_family


@pytest.fixture
def engine() -> TransitionEngine:
    return TransitionEngine()


# ---------------------------------------------------------------------------
# 1. All 31 legal transitions produce correct next state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state_event,expected_next",
    list(TRANSITION_TABLE.items()),
    ids=[f"{s.value}+{e.value}" for (s, e) in TRANSITION_TABLE],
)
def test_legal_transition_produces_correct_next_state(
    engine: TransitionEngine,
    state_event: tuple[FamilyState, FamilyEvent],
    expected_next: FamilyState,
) -> None:
    """Every entry in TRANSITION_TABLE must yield the expected next state."""
    current_state, event = state_event
    family = make_family(state=current_state)
    result = engine.apply(family, event)

    assert result.new_state == expected_next
    assert result.previous_state == current_state
    assert result.family.state == expected_next
    assert result.cancelled is False


# ---------------------------------------------------------------------------
# 2. Illegal transition raises IllegalTransitionError
# ---------------------------------------------------------------------------


def test_illegal_transition_raises(engine: TransitionEngine) -> None:
    """NEW + CODE_APPROVED is not in the table and must raise."""
    family = make_family(state=FamilyState.NEW)
    with pytest.raises(IllegalTransitionError) as exc_info:
        engine.apply(family, FamilyEvent.CODE_APPROVED)

    assert exc_info.value.state == FamilyState.NEW
    assert exc_info.value.event == FamilyEvent.CODE_APPROVED


# ---------------------------------------------------------------------------
# 3. Strike context adds a strike and records in side_effects
# ---------------------------------------------------------------------------


def test_strike_context_adds_strike(engine: TransitionEngine) -> None:
    """Passing strike_reason in context must add a strike to the family."""
    family = make_family(state=FamilyState.PLAN_IN_REVIEW)
    ctx = {"strike_reason": "test", "iteration_id": "iter_1"}

    result = engine.apply(family, FamilyEvent.PLAN_REJECTED, context=ctx)

    assert result.family.strike_count == 1
    assert result.strike_added is not None
    assert result.strike_added.reason == "test"
    assert result.strike_added.iteration_id == "iter_1"
    assert result.strike_added.is_red is False

    strike_effects = [se for se in result.side_effects if se.type == "strike_added"]
    assert len(strike_effects) == 1
    assert strike_effects[0].data["reason"] == "test"
    assert strike_effects[0].data["is_red"] is False


# ---------------------------------------------------------------------------
# 4. Red strike context increments red_strike_count
# ---------------------------------------------------------------------------


def test_red_strike_context_increments_red_count(engine: TransitionEngine) -> None:
    """is_red_strike=True must increment red_strike_count."""
    family = make_family(state=FamilyState.CODE_IN_REVIEW)
    ctx = {
        "strike_reason": "bad",
        "is_red_strike": True,
        "iteration_id": "iter_1",
    }

    result = engine.apply(family, FamilyEvent.CODE_REJECTED, context=ctx)

    assert result.family.red_strike_count == 1
    assert result.family.strike_count == 1
    assert result.strike_added is not None
    assert result.strike_added.is_red is True

    strike_effects = [se for se in result.side_effects if se.type == "strike_added"]
    assert len(strike_effects) == 1
    assert strike_effects[0].data["is_red"] is True


# ---------------------------------------------------------------------------
# 5. Cancellation override at 3 strikes
# ---------------------------------------------------------------------------


def test_cancellation_at_three_strikes(engine: TransitionEngine) -> None:
    """Third strike must override normal transition with cancellation."""
    family = make_family(state=FamilyState.PLAN_IN_REVIEW, strike_count=2)
    ctx = {"strike_reason": "third strike", "iteration_id": "iter_3"}

    result = engine.apply(family, FamilyEvent.PLAN_REJECTED, context=ctx)

    assert result.cancelled is True
    assert result.new_state == FamilyState.CANCELLED_3_STRIKES
    assert result.family.state == FamilyState.CANCELLED_3_STRIKES
    assert result.family.strike_count == 3
    assert result.strike_added is not None

    cancel_effects = [se for se in result.side_effects if se.type == "family_cancelled"]
    assert len(cancel_effects) == 1
    assert cancel_effects[0].data["strike_count"] == 3


# ---------------------------------------------------------------------------
# 6. Cancellation override at 2 red strikes
# ---------------------------------------------------------------------------


def test_cancellation_at_two_red_strikes(engine: TransitionEngine) -> None:
    """Second red strike must trigger cancellation regardless of total count."""
    family = make_family(
        state=FamilyState.CODE_IN_REVIEW,
        strike_count=1,
        red_strike_count=1,
    )
    ctx = {
        "strike_reason": "second red",
        "is_red_strike": True,
        "iteration_id": "iter_2",
    }

    result = engine.apply(family, FamilyEvent.CODE_REJECTED, context=ctx)

    assert result.cancelled is True
    assert result.new_state == FamilyState.CANCELLED_3_STRIKES
    assert result.family.red_strike_count == 2
    assert result.strike_added is not None
    assert result.strike_added.is_red is True


# ---------------------------------------------------------------------------
# 7. ITERATE event increments current_iteration
# ---------------------------------------------------------------------------


def test_iterate_increments_current_iteration(engine: TransitionEngine) -> None:
    """ITERATE event must bump current_iteration by 1."""
    family = make_family(state=FamilyState.RESULTS_IN_REVIEW, current_iteration=3)

    result = engine.apply(family, FamilyEvent.ITERATE)

    assert result.family.current_iteration == 4
    assert result.new_state == FamilyState.ITERATE


# ---------------------------------------------------------------------------
# 8. Score context updates best_qualified_score when higher
# ---------------------------------------------------------------------------


def test_score_updates_when_higher(engine: TransitionEngine) -> None:
    """Context score higher than current best must update best_qualified_score."""
    family = make_family(
        state=FamilyState.RESULTS_IN_REVIEW,
        best_qualified_score=0.5,
    )
    ctx = {"score": 0.8}

    result = engine.apply(family, FamilyEvent.RESULT_APPROVED, context=ctx)

    assert result.family.best_qualified_score == 0.8


# ---------------------------------------------------------------------------
# 9. Score does NOT update when lower
# ---------------------------------------------------------------------------


def test_score_does_not_update_when_lower(engine: TransitionEngine) -> None:
    """Context score lower than current best must not change best_qualified_score."""
    family = make_family(
        state=FamilyState.RESULTS_IN_REVIEW,
        best_qualified_score=0.9,
    )
    ctx = {"score": 0.4}

    result = engine.apply(family, FamilyEvent.RESULT_APPROVED, context=ctx)

    assert result.family.best_qualified_score == 0.9


# ---------------------------------------------------------------------------
# 10. Side effects always contain state_transition for legal, non-cancelled
# ---------------------------------------------------------------------------


def test_side_effects_contain_state_transition(engine: TransitionEngine) -> None:
    """Every legal non-cancelled transition must emit a state_transition side effect."""
    family = make_family(state=FamilyState.QUEUED)

    result = engine.apply(family, FamilyEvent.PLAN_SUBMITTED)

    transition_effects = [se for se in result.side_effects if se.type == "state_transition"]
    assert len(transition_effects) == 1
    assert transition_effects[0].data["from"] == FamilyState.QUEUED
    assert transition_effects[0].data["to"] == FamilyState.PLAN_IN_REVIEW
    assert transition_effects[0].data["event"] == FamilyEvent.PLAN_SUBMITTED


@pytest.mark.parametrize(
    "state_event",
    list(TRANSITION_TABLE.keys()),
    ids=[f"{s.value}+{e.value}" for (s, e) in TRANSITION_TABLE],
)
def test_all_legal_transitions_have_state_transition_side_effect(
    engine: TransitionEngine,
    state_event: tuple[FamilyState, FamilyEvent],
) -> None:
    """Verify every legal transition emits at least one state_transition side effect."""
    current_state, event = state_event
    family = make_family(state=current_state)

    result = engine.apply(family, event)

    transition_effects = [se for se in result.side_effects if se.type == "state_transition"]
    assert len(transition_effects) >= 1
