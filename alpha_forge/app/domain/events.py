"""Family workflow events and the transition table."""

from enum import StrEnum

from alpha_forge.app.domain.states import FamilyState


class FamilyEvent(StrEnum):
    """Events that drive family state transitions."""

    SEED_ACCEPTED = "SEED_ACCEPTED"
    FAMILY_CREATED = "FAMILY_CREATED"
    PLAN_SUBMITTED = "PLAN_SUBMITTED"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_REJECTED = "PLAN_REJECTED"
    CODE_SUBMITTED = "CODE_SUBMITTED"
    CODE_APPROVED = "CODE_APPROVED"
    CODE_REJECTED = "CODE_REJECTED"
    GUARDS_PASSED = "GUARDS_PASSED"
    GUARDS_FAILED = "GUARDS_FAILED"
    BACKTEST_COMPLETED = "BACKTEST_COMPLETED"
    BACKTEST_FAILED = "BACKTEST_FAILED"
    RESULT_APPROVED = "RESULT_APPROVED"
    RESULT_REJECTED = "RESULT_REJECTED"
    PROMOTE_HOLDOUT = "PROMOTE_HOLDOUT"
    HOLDOUT_PASSED = "HOLDOUT_PASSED"
    HOLDOUT_FAILED = "HOLDOUT_FAILED"
    PROMOTE_PAPER = "PROMOTE_PAPER"
    PAPER_PASSED = "PAPER_PASSED"
    PAPER_FAILED = "PAPER_FAILED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_REJECTED = "HUMAN_REJECTED"
    ITERATE = "ITERATE"
    PAUSED_FOR_REVIEW = "PAUSED_FOR_REVIEW"


# Static transition table: (current_state, event) -> next_state
# If a pair is not in this table, the transition is illegal.
TRANSITION_TABLE: dict[tuple[FamilyState, FamilyEvent], FamilyState] = {
    # Seed -> family creation
    (FamilyState.NEW, FamilyEvent.FAMILY_CREATED): FamilyState.QUEUED,
    # Queue -> plan
    (FamilyState.QUEUED, FamilyEvent.PLAN_SUBMITTED): FamilyState.PLAN_IN_REVIEW,
    # Plan review
    (FamilyState.PLAN_IN_REVIEW, FamilyEvent.PLAN_APPROVED): FamilyState.PLAN_APPROVED,
    (FamilyState.PLAN_IN_REVIEW, FamilyEvent.PLAN_REJECTED): FamilyState.PLAN_REVISION_REQUIRED,
    (FamilyState.PLAN_IN_REVIEW, FamilyEvent.PAUSED_FOR_REVIEW): FamilyState.PAUSED_FOR_REVIEW,
    # Plan revision / re-entry
    (FamilyState.PLAN_REVISION_REQUIRED, FamilyEvent.PLAN_SUBMITTED): FamilyState.PLAN_IN_REVIEW,
    (FamilyState.ITERATE, FamilyEvent.PLAN_SUBMITTED): FamilyState.PLAN_IN_REVIEW,
    # Plan approved -> coding
    (FamilyState.PLAN_APPROVED, FamilyEvent.CODE_SUBMITTED): FamilyState.CODE_IN_REVIEW,
    (FamilyState.CODING, FamilyEvent.CODE_SUBMITTED): FamilyState.CODE_IN_REVIEW,
    # Code review
    (FamilyState.CODE_IN_REVIEW, FamilyEvent.CODE_APPROVED): FamilyState.CODE_APPROVED,
    (FamilyState.CODE_IN_REVIEW, FamilyEvent.CODE_REJECTED): FamilyState.CODE_REVISION_REQUIRED,
    (FamilyState.CODE_IN_REVIEW, FamilyEvent.PAUSED_FOR_REVIEW): FamilyState.PAUSED_FOR_REVIEW,
    # Code revision
    (FamilyState.CODE_REVISION_REQUIRED, FamilyEvent.CODE_SUBMITTED): FamilyState.CODE_IN_REVIEW,
    # Code approved -> guards/backtest
    (FamilyState.CODE_APPROVED, FamilyEvent.GUARDS_PASSED): FamilyState.BACKTEST_RUNNING,
    (FamilyState.CODE_APPROVED, FamilyEvent.GUARDS_FAILED): FamilyState.QUEUED,
    (FamilyState.CODE_APPROVED, FamilyEvent.PAUSED_FOR_REVIEW): FamilyState.PAUSED_FOR_REVIEW,
    (FamilyState.GUARDS_RUNNING, FamilyEvent.GUARDS_PASSED): FamilyState.BACKTEST_RUNNING,
    (FamilyState.GUARDS_RUNNING, FamilyEvent.GUARDS_FAILED): FamilyState.QUEUED,
    (FamilyState.GUARDS_RUNNING, FamilyEvent.PAUSED_FOR_REVIEW): FamilyState.PAUSED_FOR_REVIEW,
    # Backtest
    (FamilyState.BACKTEST_RUNNING, FamilyEvent.BACKTEST_COMPLETED): FamilyState.RESULTS_IN_REVIEW,
    (FamilyState.BACKTEST_RUNNING, FamilyEvent.BACKTEST_FAILED): FamilyState.CODE_REVISION_REQUIRED,
    (FamilyState.BACKTEST_RUNNING, FamilyEvent.PAUSED_FOR_REVIEW): FamilyState.PAUSED_FOR_REVIEW,
    # Results review
    (FamilyState.RESULTS_IN_REVIEW, FamilyEvent.RESULT_APPROVED): FamilyState.PROMOTE_TO_HOLDOUT,
    (FamilyState.RESULTS_IN_REVIEW, FamilyEvent.ITERATE): FamilyState.ITERATE,
    (FamilyState.RESULTS_IN_REVIEW, FamilyEvent.RESULT_REJECTED): FamilyState.ARCHIVED_REJECTED,
    (FamilyState.RESULTS_IN_REVIEW, FamilyEvent.PAUSED_FOR_REVIEW): FamilyState.PAUSED_FOR_REVIEW,
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
    # From PAUSED_FOR_REVIEW (user decides)
    (FamilyState.PAUSED_FOR_REVIEW, FamilyEvent.ITERATE): FamilyState.QUEUED,
    (FamilyState.PAUSED_FOR_REVIEW, FamilyEvent.HUMAN_REJECTED): FamilyState.ARCHIVED_REJECTED,
    (FamilyState.PAUSED_FOR_REVIEW, FamilyEvent.FAMILY_CREATED): FamilyState.QUEUED,
}
