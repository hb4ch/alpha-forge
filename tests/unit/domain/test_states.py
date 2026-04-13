"""Tests for alpha_forge.app.domain.states."""

from __future__ import annotations

from alpha_forge.app.domain.states import FamilyState


TERMINAL_STATES = {
    FamilyState.ARCHIVED_REJECTED,
    FamilyState.BUDGET_EXHAUSTED,
    FamilyState.DONE,
}

NON_TERMINAL_STATES = set(FamilyState) - TERMINAL_STATES


class TestFamilyStateIsTerminal:
    """FamilyState.is_terminal property tests."""

    def test_paused_for_review_is_not_terminal(self) -> None:
        assert FamilyState.PAUSED_FOR_REVIEW.is_terminal is False

    def test_archived_rejected_is_terminal(self) -> None:
        assert FamilyState.ARCHIVED_REJECTED.is_terminal is True

    def test_done_is_terminal(self) -> None:
        assert FamilyState.DONE.is_terminal is True

    def test_non_terminal_states_return_false(self) -> None:
        for state in NON_TERMINAL_STATES:
            assert state.is_terminal is False, f"{state} should not be terminal"

    def test_budget_exhausted_is_terminal(self) -> None:
        assert FamilyState.BUDGET_EXHAUSTED.is_terminal is True

    def test_non_terminal_count(self) -> None:
        assert len(NON_TERMINAL_STATES) == len(set(FamilyState)) - len(TERMINAL_STATES)


class TestFamilyStateStrEnum:
    """FamilyState StrEnum contract tests."""

    def test_all_values_are_strings(self) -> None:
        for state in FamilyState:
            assert isinstance(state, str)
            assert isinstance(state.value, str)

    def test_string_equality(self) -> None:
        assert FamilyState.NEW == "NEW"
        assert FamilyState.QUEUED == "QUEUED"
        assert FamilyState.DONE == "DONE"
