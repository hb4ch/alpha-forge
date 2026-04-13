"""Iteration budget management.

Replaces the old strike-based system. Families iterate until they
succeed or exhaust their iteration budget.
"""
from __future__ import annotations

from alpha_forge.app.domain.models import IdeaFamily


def is_budget_exhausted(family: IdeaFamily) -> bool:
    """Check if a family has used up its iteration budget."""
    return family.current_iteration >= family.max_iterations
