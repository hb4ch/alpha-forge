"""Tests for alpha_forge.app.domain.strikes."""

from __future__ import annotations

from alpha_forge.app.domain.strikes import add_strike, should_cancel, reset_strikes
from tests.conftest import make_family


class TestAddStrike:
    """add_strike increments counts and grows history."""

    def test_increments_strike_count(self) -> None:
        family = make_family(strike_count=0, red_strike_count=0)
        updated = add_strike(family, "iter_1", "bad plan")
        assert updated.strike_count == 1
        assert updated.red_strike_count == 0

    def test_red_strike_increments_both(self) -> None:
        family = make_family(strike_count=0, red_strike_count=0)
        updated = add_strike(family, "iter_1", "leakage detected", is_red=True)
        assert updated.strike_count == 1
        assert updated.red_strike_count == 1

    def test_history_grows(self) -> None:
        family = make_family(strike_count=0, red_strike_count=0)
        f1 = add_strike(family, "iter_1", "reason_1")
        f2 = add_strike(f1, "iter_2", "reason_2")
        assert len(f2.strike_history) == 2
        assert f2.strike_history[0].reason == "reason_1"
        assert f2.strike_history[1].reason == "reason_2"

    def test_original_family_unchanged(self) -> None:
        family = make_family(strike_count=0, red_strike_count=0)
        add_strike(family, "iter_1", "reason")
        assert family.strike_count == 0
        assert len(family.strike_history) == 0


class TestShouldCancel:
    """should_cancel checks strike thresholds."""

    def test_true_at_3_strikes(self) -> None:
        family = make_family(strike_count=3, red_strike_count=0)
        assert should_cancel(family) is True

    def test_true_at_2_red_strikes(self) -> None:
        family = make_family(strike_count=2, red_strike_count=2)
        assert should_cancel(family) is True

    def test_false_at_2_strikes_0_red(self) -> None:
        family = make_family(strike_count=2, red_strike_count=0)
        assert should_cancel(family) is False

    def test_false_at_0_strikes(self) -> None:
        family = make_family(strike_count=0, red_strike_count=0)
        assert should_cancel(family) is False

    def test_true_above_3_strikes(self) -> None:
        family = make_family(strike_count=5, red_strike_count=1)
        assert should_cancel(family) is True


class TestResetStrikes:
    """reset_strikes zeroes counts but preserves history."""

    def test_resets_counts(self) -> None:
        family = make_family(strike_count=2, red_strike_count=1)
        updated = reset_strikes(family)
        assert updated.strike_count == 0
        assert updated.red_strike_count == 0

    def test_preserves_history(self) -> None:
        family = make_family(strike_count=0, red_strike_count=0)
        f1 = add_strike(family, "iter_1", "some reason")
        f2 = reset_strikes(f1)
        assert f2.strike_count == 0
        assert len(f2.strike_history) == 1
        assert f2.strike_history[0].reason == "some reason"
