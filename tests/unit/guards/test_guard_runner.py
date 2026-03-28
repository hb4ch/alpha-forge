"""Tests for the guard runner utility functions."""

from __future__ import annotations

from alpha_forge.app.domain.models import GuardResult
from alpha_forge.app.guards.runner import any_failed, has_red_strike


class TestAnyFailed:
    """Tests for any_failed."""

    def test_returns_false_when_all_pass(self) -> None:
        """Returns False when all GuardResults pass."""
        results = [
            GuardResult(guard_name="a", passed=True, is_red_strike=False),
            GuardResult(guard_name="b", passed=True, is_red_strike=False),
            GuardResult(guard_name="c", passed=True, is_red_strike=False),
        ]
        assert any_failed(results) is False

    def test_returns_true_when_one_fails(self) -> None:
        """Returns True when at least one GuardResult fails."""
        results = [
            GuardResult(guard_name="a", passed=True, is_red_strike=False),
            GuardResult(guard_name="b", passed=False, is_red_strike=False),
            GuardResult(guard_name="c", passed=True, is_red_strike=False),
        ]
        assert any_failed(results) is True


class TestHasRedStrike:
    """Tests for has_red_strike."""

    def test_returns_false_when_no_red_strikes(self) -> None:
        """Returns False when no results have red strikes."""
        results = [
            GuardResult(guard_name="a", passed=True, is_red_strike=False),
            GuardResult(guard_name="b", passed=False, is_red_strike=False),
        ]
        assert has_red_strike(results) is False

    def test_returns_true_when_failed_with_red_strike(self) -> None:
        """Returns True when a failed guard has is_red_strike=True."""
        results = [
            GuardResult(guard_name="a", passed=True, is_red_strike=False),
            GuardResult(guard_name="b", passed=False, is_red_strike=True),
        ]
        assert has_red_strike(results) is True

    def test_returns_false_when_passed_with_red_strike(self) -> None:
        """Returns False when a passed guard has is_red_strike=True (passed guards don't count)."""
        results = [
            GuardResult(guard_name="a", passed=True, is_red_strike=True),
            GuardResult(guard_name="b", passed=True, is_red_strike=False),
        ]
        assert has_red_strike(results) is False
