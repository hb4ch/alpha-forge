"""Pattern-based strike accounting.

Strikes only prevent overfitting death loops, not normal iteration.
Individual REVISE/REJECT verdicts do not add strikes.
"""
from __future__ import annotations

from alpha_forge.app.domain.models import IdeaFamily, StrikeRecord

MAX_STRIKES = 3
MAX_RED_STRIKES = 2
CONSECUTIVE_OVERFIT_THRESHOLD = 3
DEATH_SPIRAL_LENGTH = 3


def add_strike(
    family: IdeaFamily,
    iteration_id: str,
    reason: str,
    is_red: bool = False,
) -> IdeaFamily:
    """Add a strike to a family and return the updated family."""
    record = StrikeRecord(
        iteration_id=iteration_id,
        reason=reason,
        is_red=is_red,
    )
    return family.model_copy(
        update={
            "strike_count": family.strike_count + 1,
            "red_strike_count": family.red_strike_count + (1 if is_red else 0),
            "strike_history": [*family.strike_history, record],
        }
    )


def detect_overfit_loop(family: IdeaFamily, latest_tags: list[str]) -> bool:
    """Check if the same overfit flag appears in 3+ consecutive iterations."""
    if not latest_tags:
        return False
    history = [*family.overfit_flag_history, *latest_tags]
    if len(history) < CONSECUTIVE_OVERFIT_THRESHOLD:
        return False
    tail = history[-(CONSECUTIVE_OVERFIT_THRESHOLD):]
    return len(set(tail)) == 1


def detect_death_spiral(family: IdeaFamily, latest_score: float) -> bool:
    """Check if composite score has decreased N iterations in a row."""
    scores = [*family.score_history, latest_score]
    if len(scores) < DEATH_SPIRAL_LENGTH + 1:
        return False
    tail = scores[-(DEATH_SPIRAL_LENGTH + 1):]
    return all(tail[i] > tail[i + 1] for i in range(len(tail) - 1))


def should_pause_for_review(family: IdeaFamily) -> bool:
    """Check if a family should pause for human review."""
    return family.strike_count >= MAX_STRIKES or family.red_strike_count >= MAX_RED_STRIKES


def reset_strikes(family: IdeaFamily) -> IdeaFamily:
    """Reset strikes and pattern trackers after qualified improvement."""
    return family.model_copy(
        update={
            "strike_count": 0,
            "red_strike_count": 0,
            "overfit_flag_history": [],
            "score_history": [],
        }
    )
