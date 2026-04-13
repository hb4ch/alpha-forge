"""Deterministic transition engine for family state machine.

Pure logic: takes data in, returns data out. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from alpha_forge.app.domain.events import TRANSITION_TABLE, FamilyEvent
from alpha_forge.app.domain.models import IdeaFamily
from alpha_forge.app.domain.states import FamilyState


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
                - score: float - new composite score (for qualified improvement)

        Returns:
            TransitionResult with new state, updated family, and side effects.

        Raises:
            IllegalTransitionError if the transition is not in the table.
        """
        ctx = context or {}
        previous_state = family.state
        side_effects: list[SideEffect] = []

        # Look up the transition
        key = (family.state, event)
        if key not in TRANSITION_TABLE:
            raise IllegalTransitionError(family.state, event)

        new_state = TRANSITION_TABLE[key]
        updated_family = family.model_copy(update={"state": new_state})

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
        )
