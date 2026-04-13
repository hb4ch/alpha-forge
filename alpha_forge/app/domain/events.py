"""Family workflow events and the transition table."""

from enum import StrEnum

from alpha_forge.app.domain.states import FamilyState


class FamilyEvent(StrEnum):
    """Events that drive family state transitions."""

    SEED_ACCEPTED = "SEED_ACCEPTED"
    FAMILY_CREATED = "FAMILY_CREATED"
    PLAN_SUBMITTED = "PLAN_SUBMITTED"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_REVISION_REQUIRED = "PLAN_REVISION_REQUIRED"
    CODE_SUBMITTED = "CODE_SUBMITTED"
    CODE_APPROVED = "CODE_APPROVED"
    CODE_REVISION_REQUIRED = "CODE_REVISION_REQUIRED"
    GUARDS_PASSED = "GUARDS_PASSED"
    GUARDS_FAILED = "GUARDS_FAILED"
    BACKTEST_COMPLETED = "BACKTEST_COMPLETED"
    BACKTEST_FAILED = "BACKTEST_FAILED"
    RESULT_APPROVED = "RESULT_APPROVED"
    ITERATE = "ITERATE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    PROMOTE_HOLDOUT = "PROMOTE_HOLDOUT"
    HOLDOUT_PASSED = "HOLDOUT_PASSED"
    HOLDOUT_FAILED = "HOLDOUT_FAILED"
    PROMOTE_PAPER = "PROMOTE_PAPER"
    PAPER_PASSED = "PAPER_PASSED"
    PAPER_FAILED = "PAPER_FAILED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_REJECTED = "HUMAN_REJECTED"


# Static transition table: (current_state, event) -> next_state
# If a pair is not in this table, the transition is illegal.
TRANSITION_TABLE: dict[tuple[FamilyState, FamilyEvent], FamilyState] = {
    # Seed -> family creation
    (FamilyState.NEW, FamilyEvent.FAMILY_CREATED): FamilyState.QUEUED,
    # Queue -> plan
    (FamilyState.QUEUED, FamilyEvent.PLAN_SUBMITTED): FamilyState.PLAN_IN_REVIEW,
    # Plan review outcomes
    (FamilyState.PLAN_IN_REVIEW, FamilyEvent.PLAN_APPROVED): FamilyState.PLAN_APPROVED,
    (FamilyState.PLAN_IN_REVIEW, FamilyEvent.PLAN_REVISION_REQUIRED): FamilyState.PLAN_REVISION_REQUIRED,
    # Plan revision / re-entry
    (FamilyState.PLAN_REVISION_REQUIRED, FamilyEvent.PLAN_SUBMITTED): FamilyState.PLAN_IN_REVIEW,
    (FamilyState.ITERATE, FamilyEvent.PLAN_SUBMITTED): FamilyState.PLAN_IN_REVIEW,
    # Plan approved -> coding
    (FamilyState.PLAN_APPROVED, FamilyEvent.CODE_SUBMITTED): FamilyState.CODE_IN_REVIEW,
    (FamilyState.CODING, FamilyEvent.CODE_SUBMITTED): FamilyState.CODE_IN_REVIEW,
    # Code review outcomes
    (FamilyState.CODE_IN_REVIEW, FamilyEvent.CODE_APPROVED): FamilyState.CODE_APPROVED,
    (FamilyState.CODE_IN_REVIEW, FamilyEvent.CODE_REVISION_REQUIRED): FamilyState.CODE_REVISION_REQUIRED,
    # Code revision
    (FamilyState.CODE_REVISION_REQUIRED, FamilyEvent.CODE_SUBMITTED): FamilyState.CODE_IN_REVIEW,
    # Code approved -> guards/backtest
    (FamilyState.CODE_APPROVED, FamilyEvent.GUARDS_PASSED): FamilyState.BACKTEST_RUNNING,
    (FamilyState.CODE_APPROVED, FamilyEvent.GUARDS_FAILED): FamilyState.CODE_REVISION_REQUIRED,
    (FamilyState.GUARDS_RUNNING, FamilyEvent.GUARDS_PASSED): FamilyState.BACKTEST_RUNNING,
    (FamilyState.GUARDS_RUNNING, FamilyEvent.GUARDS_FAILED): FamilyState.CODE_REVISION_REQUIRED,
    # Backtest
    (FamilyState.BACKTEST_RUNNING, FamilyEvent.BACKTEST_COMPLETED): FamilyState.RESULTS_IN_REVIEW,
    (FamilyState.BACKTEST_RUNNING, FamilyEvent.BACKTEST_FAILED): FamilyState.CODE_REVISION_REQUIRED,
    # Results review
    (FamilyState.RESULTS_IN_REVIEW, FamilyEvent.RESULT_APPROVED): FamilyState.PROMOTE_TO_HOLDOUT,
    (FamilyState.RESULTS_IN_REVIEW, FamilyEvent.ITERATE): FamilyState.ITERATE,
    # Holdout
    (FamilyState.PROMOTE_TO_HOLDOUT, FamilyEvent.PROMOTE_HOLDOUT): FamilyState.HOLDOUT_RUNNING,
    (FamilyState.HOLDOUT_RUNNING, FamilyEvent.HOLDOUT_PASSED): FamilyState.PROMOTE_TO_PAPER,
    (FamilyState.HOLDOUT_RUNNING, FamilyEvent.HOLDOUT_FAILED): FamilyState.ARCHIVED_REJECTED,
    # Paper forward
    (FamilyState.PROMOTE_TO_PAPER, FamilyEvent.PROMOTE_PAPER): FamilyState.PAPER_FORWARD_RUNNING,
    (FamilyState.PAPER_FORWARD_RUNNING, FamilyEvent.PAPER_PASSED): FamilyState.HUMAN_REVIEW,
    (FamilyState.PAPER_FORWARD_RUNNING, FamilyEvent.PAPER_FAILED): FamilyState.ARCHIVED_REJECTED,
    # Human review
    (FamilyState.HUMAN_REVIEW, FamilyEvent.HUMAN_APPROVED): FamilyState.DONE,
    (FamilyState.HUMAN_REVIEW, FamilyEvent.HUMAN_REJECTED): FamilyState.ARCHIVED_REJECTED,
    # Budget exhaustion (terminal)
    (FamilyState.ITERATE, FamilyEvent.BUDGET_EXHAUSTED): FamilyState.BUDGET_EXHAUSTED,
}
