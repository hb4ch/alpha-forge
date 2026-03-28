"""Tests for pattern-based strike detection."""
from __future__ import annotations

from alpha_forge.app.domain.strikes import (
    add_strike,
    detect_death_spiral,
    detect_overfit_loop,
    reset_strikes,
    should_pause_for_review,
)
from tests.conftest import make_family


class TestDetectOverfitLoop:
    def test_no_loop_with_different_tags(self) -> None:
        family = make_family(overfit_flag_history=["tag_a", "tag_b", "tag_c"])
        assert detect_overfit_loop(family, ["tag_d"]) is False

    def test_detects_3_consecutive_same_tag(self) -> None:
        family = make_family(overfit_flag_history=["tag_a", "tag_a"])
        assert detect_overfit_loop(family, ["tag_a"]) is True

    def test_no_loop_with_only_2_same(self) -> None:
        family = make_family(overfit_flag_history=["tag_a"])
        assert detect_overfit_loop(family, ["tag_a"]) is False

    def test_empty_history(self) -> None:
        family = make_family(overfit_flag_history=[])
        assert detect_overfit_loop(family, ["tag_a"]) is False


class TestDetectDeathSpiral:
    def test_no_spiral_with_improving_scores(self) -> None:
        family = make_family(score_history=[0.3, 0.4, 0.5])
        assert detect_death_spiral(family, 0.6) is False

    def test_detects_3_consecutive_declines(self) -> None:
        family = make_family(score_history=[0.5, 0.4, 0.3])
        assert detect_death_spiral(family, 0.2) is True

    def test_no_spiral_with_recovery(self) -> None:
        family = make_family(score_history=[0.5, 0.4, 0.3])
        assert detect_death_spiral(family, 0.35) is False

    def test_short_history(self) -> None:
        family = make_family(score_history=[0.5])
        assert detect_death_spiral(family, 0.3) is False


class TestShouldPauseForReview:
    def test_pause_at_3_yellow(self) -> None:
        family = make_family(strike_count=3, red_strike_count=0)
        assert should_pause_for_review(family) is True

    def test_pause_at_2_red(self) -> None:
        family = make_family(strike_count=2, red_strike_count=2)
        assert should_pause_for_review(family) is True

    def test_no_pause_below_threshold(self) -> None:
        family = make_family(strike_count=2, red_strike_count=0)
        assert should_pause_for_review(family) is False


class TestResetStrikesV2:
    def test_clears_pattern_trackers(self) -> None:
        family = make_family(
            strike_count=2,
            red_strike_count=1,
            overfit_flag_history=["a", "a"],
            score_history=[0.5, 0.4],
        )
        updated = reset_strikes(family)
        assert updated.strike_count == 0
        assert updated.red_strike_count == 0
        assert updated.overfit_flag_history == []
        assert updated.score_history == []
        assert len(updated.strike_history) == len(family.strike_history)
