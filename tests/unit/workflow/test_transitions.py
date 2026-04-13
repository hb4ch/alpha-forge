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
# 1. All legal transitions produce correct next state
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
# 3. ITERATE event does not mutate current_iteration
# ---------------------------------------------------------------------------


def test_iterate_does_not_increment_current_iteration(engine: TransitionEngine) -> None:
    """ITERATE bookkeeping belongs to FamilyFlow, not TransitionEngine."""
    family = make_family(state=FamilyState.RESULTS_IN_REVIEW, current_iteration=3)

    result = engine.apply(family, FamilyEvent.ITERATE)

    assert result.family.current_iteration == 3
    assert result.new_state == FamilyState.ITERATE


# ---------------------------------------------------------------------------
# 4. Score context updates best_qualified_score when higher
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
# 5. Score does NOT update when lower
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
# 6. Side effects always contain state_transition for legal transitions
# ---------------------------------------------------------------------------


def test_side_effects_contain_state_transition(engine: TransitionEngine) -> None:
    """Every legal transition must emit a state_transition side effect."""
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


# ---------------------------------------------------------------------------
# 7. Guard failure routes to CODE_REVISION_REQUIRED
# ---------------------------------------------------------------------------


def test_guard_failure_routes_to_code_revision(engine: TransitionEngine) -> None:
    """Guard failures should route to CODE_REVISION_REQUIRED, not archive."""
    family = make_family(state=FamilyState.CODE_APPROVED)
    result = engine.apply(family, FamilyEvent.GUARDS_FAILED)
    assert result.new_state == FamilyState.CODE_REVISION_REQUIRED


# ---------------------------------------------------------------------------
# 8. Budget exhaustion from ITERATE state
# ---------------------------------------------------------------------------


def test_budget_exhaustion_from_iterate(engine: TransitionEngine) -> None:
    """BUDGET_EXHAUSTED event should transition to terminal BUDGET_EXHAUSTED state."""
    family = make_family(state=FamilyState.ITERATE)
    result = engine.apply(family, FamilyEvent.BUDGET_EXHAUSTED)
    assert result.new_state == FamilyState.BUDGET_EXHAUSTED
    assert result.family.state.is_terminal


# ---------------------------------------------------------------------------
# 9. Plan revision required routes back to plan submission
# ---------------------------------------------------------------------------


def test_plan_revision_routes_to_plan_review(engine: TransitionEngine) -> None:
    """PLAN_REVISION_REQUIRED should accept PLAN_SUBMITTED to re-enter review."""
    family = make_family(state=FamilyState.PLAN_IN_REVIEW)
    result = engine.apply(family, FamilyEvent.PLAN_REVISION_REQUIRED)
    assert result.new_state == FamilyState.PLAN_REVISION_REQUIRED

    result2 = engine.apply(result.family, FamilyEvent.PLAN_SUBMITTED)
    assert result2.new_state == FamilyState.PLAN_IN_REVIEW
