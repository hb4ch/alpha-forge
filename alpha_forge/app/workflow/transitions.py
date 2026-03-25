"""Deterministic transition engine for family state machine.

Pure logic: takes data in, returns data out. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from alpha_forge.app.domain.events import TRANSITION_TABLE, FamilyEvent
from alpha_forge.app.domain.models import IdeaFamily, StrikeRecord
from alpha_forge.app.domain.states import FamilyState
from alpha_forge.app.domain.strikes import add_strike, should_pause_for_review


@dataclass
class SideEffect:
    """Descriptive side effect to be executed by the caller."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransitionResult:
    """Result of applying a transition."""

    previous_state: FamilyState
    new_state: FamilyState
    family: IdeaFamily
    side_effects: list[SideEffect] = field(default_factory=list)
    strike_added: StrikeRecord | None = None
    paused_for_review: bool = False


class IllegalTransitionError(Exception):
    """Raised when a transition is not legal."""

    def __init__(self, state: FamilyState, event: FamilyEvent) -> None:
        self.state = state
        self.event = event
        super().__init__(f"Illegal transition: {state} + {event}")


class TransitionEngine:
    """Deterministic state machine for family lifecycle."""

    def apply(
        self,
        family: IdeaFamily,
        event: FamilyEvent,
        context: dict[str, Any] | None = None,
    ) -> TransitionResult:
        """Apply an event to a family and return the transition result.

        Args:
            family: Current family state.
            event: The event being applied.
            context: Optional context dict with:
                - strike_reason: str - reason for adding a strike
                - is_red_strike: bool - whether this is a red strike
                - iteration_id: str - current iteration ID
                - score: float - new composite score (for qualified improvement)

        Returns:
            TransitionResult with new state, updated family, and side effects.

        Raises:
            IllegalTransitionError if the transition is not in the table.
        """
        ctx = context or {}
        previous_state = family.state
        side_effects: list[SideEffect] = []
        strike_added: StrikeRecord | None = None
        updated_family = family

        # Handle strike addition if context indicates one
        if ctx.get("strike_reason"):
            iteration_id = ctx.get("iteration_id", "unknown")
            is_red = ctx.get("is_red_strike", False)
            updated_family = add_strike(
                updated_family,
                iteration_id=iteration_id,
                reason=ctx["strike_reason"],
                is_red=is_red,
            )
            strike_added = updated_family.strike_history[-1]
            side_effects.append(SideEffect(
                type="strike_added",
                data={"reason": ctx["strike_reason"], "is_red": is_red},
            ))

        # Check if strikes trigger pause for review (overrides normal transition)
        if should_pause_for_review(updated_family):
            new_state = FamilyState.PAUSED_FOR_REVIEW
            updated_family = updated_family.model_copy(update={"state": new_state})
            side_effects.append(SideEffect(
                type="family_paused_for_review",
                data={"strike_count": updated_family.strike_count, "red_strike_count": updated_family.red_strike_count},
            ))
            return TransitionResult(
                previous_state=previous_state,
                new_state=new_state,
                family=updated_family,
                side_effects=side_effects,
                strike_added=strike_added,
                paused_for_review=True,
            )

        # Look up the transition
        key = (updated_family.state, event)
        if key not in TRANSITION_TABLE:
            raise IllegalTransitionError(updated_family.state, event)

        new_state = TRANSITION_TABLE[key]
        updated_family = updated_family.model_copy(update={"state": new_state})

        # Handle iteration increment on ITERATE
        if event == FamilyEvent.ITERATE:
            updated_family = updated_family.model_copy(
                update={"current_iteration": updated_family.current_iteration + 1}
            )

        # Handle best score update
        if ctx.get("score") is not None and ctx["score"] > updated_family.best_qualified_score:
            updated_family = updated_family.model_copy(
                update={"best_qualified_score": ctx["score"]}
            )

        side_effects.append(SideEffect(
            type="state_transition",
            data={"from": previous_state, "to": new_state, "event": event},
        ))

        return TransitionResult(
            previous_state=previous_state,
            new_state=new_state,
            family=updated_family,
            side_effects=side_effects,
            strike_added=strike_added,
            paused_for_review=False,
        )
