"""Strike accounting and cancellation logic."""

from __future__ import annotations

from alpha_forge.app.domain.models import IdeaFamily, StrikeRecord

MAX_STRIKES = 3
MAX_RED_STRIKES = 2


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
    new_count = family.strike_count + 1
    new_red = family.red_strike_count + (1 if is_red else 0)
    return family.model_copy(
        update={
            "strike_count": new_count,
            "red_strike_count": new_red,
            "strike_history": [*family.strike_history, record],
        }
    )


def should_cancel(family: IdeaFamily) -> bool:
    """Check if a family should be cancelled due to strikes."""
    return family.strike_count >= MAX_STRIKES or family.red_strike_count >= MAX_RED_STRIKES


def reset_strikes(family: IdeaFamily) -> IdeaFamily:
    """Reset strikes after a qualified improvement. History is preserved."""
    return family.model_copy(
        update={
            "strike_count": 0,
            "red_strike_count": 0,
        }
    )
