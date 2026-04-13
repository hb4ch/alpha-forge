"""Tests for alpha_forge.app.domain.events."""

from __future__ import annotations

from alpha_forge.app.domain.events import TRANSITION_TABLE, FamilyEvent
from alpha_forge.app.domain.states import FamilyState


TERMINAL_STATES = {
    FamilyState.ARCHIVED_REJECTED,
    FamilyState.BUDGET_EXHAUSTED,
    FamilyState.DONE,
}


class TestTransitionTableValidity:
    """All entries in TRANSITION_TABLE have valid enum keys."""

    def test_all_keys_are_valid_enums(self) -> None:
        for (state, event), target in TRANSITION_TABLE.items():
            assert isinstance(state, FamilyState), f"Invalid state: {state}"
            assert isinstance(event, FamilyEvent), f"Invalid event: {event}"
            assert isinstance(target, FamilyState), f"Invalid target: {target}"

    def test_no_duplicate_keys(self) -> None:
        keys = list(TRANSITION_TABLE.keys())
        assert len(keys) == len(set(keys))


class TestTerminalStatesNoOutgoing:
    """Terminal states have no outgoing transitions."""

    def test_no_outgoing_from_archived_rejected(self) -> None:
        outgoing = [
            k for k in TRANSITION_TABLE if k[0] == FamilyState.ARCHIVED_REJECTED
        ]
        assert outgoing == []

    def test_no_outgoing_from_done(self) -> None:
        outgoing = [k for k in TRANSITION_TABLE if k[0] == FamilyState.DONE]
        assert outgoing == []

    def test_no_outgoing_from_budget_exhausted(self) -> None:
        outgoing = [
            k for k in TRANSITION_TABLE if k[0] == FamilyState.BUDGET_EXHAUSTED
        ]
        assert outgoing == []


class TestNonTerminalStatesHaveTransitions:
    """Every non-terminal state has at least one outgoing transition."""

    def test_all_non_terminal_have_outgoing(self) -> None:
        # States that exist in the enum but have no outgoing transitions
        # because they're either unused legacy or handled specially
        EXCLUDED = TERMINAL_STATES | {
            FamilyState.PAUSED_FOR_REVIEW,  # removed from new architecture
        }
        non_terminal = set(FamilyState) - EXCLUDED
        for state in non_terminal:
            outgoing = [k for k in TRANSITION_TABLE if k[0] == state]
            assert len(outgoing) >= 1, f"{state} has no outgoing transitions"
