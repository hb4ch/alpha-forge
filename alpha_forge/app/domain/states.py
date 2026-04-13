"""Enums for family states, iteration stages, and mutation categories."""

from enum import StrEnum


class FamilyState(StrEnum):
    """Lifecycle states for an idea family."""

    NEW = "NEW"
    QUEUED = "QUEUED"
    PLAN_IN_REVIEW = "PLAN_IN_REVIEW"
    PLAN_REVISION_REQUIRED = "PLAN_REVISION_REQUIRED"
    PLAN_APPROVED = "PLAN_APPROVED"
    CODING = "CODING"
    CODE_IN_REVIEW = "CODE_IN_REVIEW"
    CODE_REVISION_REQUIRED = "CODE_REVISION_REQUIRED"
    CODE_APPROVED = "CODE_APPROVED"
    GUARDS_RUNNING = "GUARDS_RUNNING"
    BACKTEST_RUNNING = "BACKTEST_RUNNING"
    RESULTS_IN_REVIEW = "RESULTS_IN_REVIEW"
    ITERATE = "ITERATE"
    PROMOTE_TO_HOLDOUT = "PROMOTE_TO_HOLDOUT"
    HOLDOUT_RUNNING = "HOLDOUT_RUNNING"
    PROMOTE_TO_PAPER = "PROMOTE_TO_PAPER"
    PAPER_FORWARD_RUNNING = "PAPER_FORWARD_RUNNING"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    PAUSED_FOR_REVIEW = "PAUSED_FOR_REVIEW"
    ARCHIVED_REJECTED = "ARCHIVED_REJECTED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    DONE = "DONE"

    @property
    def is_terminal(self) -> bool:
        return self in {
            FamilyState.ARCHIVED_REJECTED,
            FamilyState.BUDGET_EXHAUSTED,
            FamilyState.DONE,
        }


class IterationStage(StrEnum):
    """Stages within a single iteration."""

    DRAFT_PLAN = "DRAFT_PLAN"
    PLAN_JUDGED = "PLAN_JUDGED"
    CODE_WRITE = "CODE_WRITE"
    CODE_JUDGED = "CODE_JUDGED"
    RUN_GUARDS = "RUN_GUARDS"
    RUN_BACKTEST = "RUN_BACKTEST"
    RUN_ROBUSTNESS = "RUN_ROBUSTNESS"
    BACKTEST_FAILED = "BACKTEST_FAILED"
    ROBUSTNESS_FAILED = "ROBUSTNESS_FAILED"
    RESULT_JUDGED = "RESULT_JUDGED"
    ITERATION_SUCCESS = "ITERATION_SUCCESS"
    ITERATION_FAILED = "ITERATION_FAILED"


class MutationCategory(StrEnum):
    """Categories for classifying proposed mutations."""

    HORIZON = "horizon"
    VENUE = "venue"
    REPRESENTATION = "representation"
    COMBINATION = "combination"
    REGIME = "regime"
    STRUCTURAL = "structural"


class Verdict(StrEnum):
    """Judge verdict outcomes."""

    APPROVE = "approve"
    APPROVE_WITH_CONSTRAINTS = "approve_with_constraints"
    REVISE = "revise"
    # REJECT and FORK_REQUIRED kept for backward compat (deserialization of old data)
    # but are clamped to REVISE at aggregation time.
    REJECT = "reject"
    FORK_REQUIRED = "fork_required"


class IterationMode(StrEnum):
    """How deeply the next iteration should re-evaluate."""

    REPLAN = "replan"
    REVISE_CODE = "revise_code"
    ADJUST_CONFIG = "adjust_config"


class SeedVerdict(StrEnum):
    """Seed screening outcomes."""

    ACCEPT = "accept"
    ACCEPT_WITH_NARROWING = "accept_with_narrowing"
    REJECT = "reject"
    MERGE_WITH_EXISTING = "merge_with_existing_family"


class RiskLevel(StrEnum):
    """Risk assessment levels used by judges."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PromotionRecommendation(StrEnum):
    """Result judge promotion recommendations."""

    ITERATE = "iterate"
    HOLDOUT = "holdout"
    ARCHIVE = "archive"
    REJECT = "reject"


class BudgetStatus(StrEnum):
    """Mutation budget status."""

    WITHIN_BUDGET = "within_budget"
    FINAL_ALLOWED_USE = "final_allowed_use"
    OVER_BUDGET = "over_budget"
    NOT_APPLICABLE = "not_applicable"
