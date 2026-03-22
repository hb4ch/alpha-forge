"""Split isolation guard: ensures holdout data isn't used prematurely."""

from __future__ import annotations

from alpha_forge.app.domain.models import GuardResult, IdeaFamily
from alpha_forge.app.domain.states import FamilyState


# States that are allowed to access holdout data
HOLDOUT_ALLOWED_STATES = {
    FamilyState.HOLDOUT_RUNNING,
    FamilyState.PROMOTE_TO_PAPER,
    FamilyState.PAPER_FORWARD_RUNNING,
    FamilyState.HUMAN_REVIEW,
    FamilyState.DONE,
}


def check_split_isolation(
    family: IdeaFamily,
    requested_split: str = "validation",
) -> GuardResult:
    """Ensure the family is in a state that allows access to the requested split.

    Holdout data can only be accessed after promotion (HOLDOUT_RUNNING and later).
    """
    violations: list[str] = []

    if requested_split == "holdout" and family.state not in HOLDOUT_ALLOWED_STATES:
        violations.append(
            f"Holdout data requested but family is in state {family.state}. "
            f"Holdout access is only allowed in states: {', '.join(s.value for s in HOLDOUT_ALLOWED_STATES)}"
        )

    return GuardResult(
        guard_name="split_isolation",
        passed=not violations,
        violations=violations,
        is_red_strike=len(violations) > 0,
    )
