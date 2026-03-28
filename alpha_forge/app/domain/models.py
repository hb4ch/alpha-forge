"""Pydantic models for seeds, families, iterations, judges, and results."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from alpha_forge.app.domain.states import (
    BudgetStatus,
    FamilyState,
    IterationStage,
    MutationCategory,
    PromotionRecommendation,
    RiskLevel,
    SeedVerdict,
    Verdict,
)


# ---------------------------------------------------------------------------
# Seed models
# ---------------------------------------------------------------------------


class SeedCard(BaseModel):
    """Structured research card distilled from a raw seed."""

    seed_id: str
    seed_type: str
    source_title: str
    raw_claim: str
    market: str
    horizon: str
    mechanism: str
    required_data: list[str] = Field(default_factory=list)
    testable_hypothesis: str
    ambiguities: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Judge models
# ---------------------------------------------------------------------------


class JudgeOutput(BaseModel):
    """Base structured output shared by all tier-1/2/3 judges.

    Judge-specific subclasses add their own risk fields.
    This base is used for aggregation and storage.
    """

    judge_type: str = ""
    verdict: Verdict = Verdict.REVISE
    must_fix: list[str] = Field(default_factory=list)
    required_tests: list[str] = Field(default_factory=list)
    taxonomy_tags: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""

    model_config = {"extra": "allow"}


class SeedJudgeOutput(BaseModel):
    """Output from the Seed Judge (prompt pack schema)."""

    verdict: SeedVerdict
    testability: RiskLevel = RiskLevel.MEDIUM
    mechanism_coherence: RiskLevel = RiskLevel.MEDIUM
    data_availability: RiskLevel = RiskLevel.MEDIUM
    scope_discipline: RiskLevel = RiskLevel.MEDIUM
    duplication_risk: RiskLevel = RiskLevel.LOW
    overfit_bait_risk: RiskLevel = RiskLevel.MEDIUM
    must_fix: list[str] = Field(default_factory=list)
    recommended_narrowing: list[str] = Field(default_factory=list)
    merge_target_family_id: str | None = None
    reasoning_summary: str = ""


class LeakageJudgeOutput(JudgeOutput):
    """Output from the Leakage Judge."""

    judge_type: str = "leakage"
    leakage_risk: RiskLevel = RiskLevel.MEDIUM
    time_leakage_risk: RiskLevel = RiskLevel.MEDIUM
    label_leakage_risk: RiskLevel = RiskLevel.LOW
    split_contamination_risk: RiskLevel = RiskLevel.LOW
    join_leakage_risk: RiskLevel = RiskLevel.LOW
    cache_contamination_risk: RiskLevel = RiskLevel.LOW
    holdout_contamination_risk: RiskLevel = RiskLevel.LOW


class OverfitJudgeOutput(JudgeOutput):
    """Output from the Overfitting Judge."""

    judge_type: str = "overfit"
    overfit_risk: RiskLevel = RiskLevel.MEDIUM
    search_abuse_risk: RiskLevel = RiskLevel.MEDIUM
    degrees_of_freedom_risk: RiskLevel = RiskLevel.MEDIUM
    family_drift_risk: RiskLevel = RiskLevel.LOW
    mechanism_discipline: RiskLevel = RiskLevel.MEDIUM


class RealismJudgeOutput(JudgeOutput):
    """Output from the Market Realism Judge."""

    judge_type: str = "realism"
    realism_risk: RiskLevel = RiskLevel.MEDIUM
    cost_risk: RiskLevel = RiskLevel.MEDIUM
    slippage_risk: RiskLevel = RiskLevel.MEDIUM
    turnover_risk: RiskLevel = RiskLevel.MEDIUM
    liquidity_risk: RiskLevel = RiskLevel.MEDIUM
    latency_risk: RiskLevel = RiskLevel.LOW
    venue_portability_risk: RiskLevel = RiskLevel.LOW


class CodeJudgeOutput(JudgeOutput):
    """Output from the Code Smell Judge."""

    judge_type: str = "code"
    code_risk: RiskLevel = RiskLevel.MEDIUM
    complexity_risk: RiskLevel = RiskLevel.MEDIUM
    statefulness_risk: RiskLevel = RiskLevel.LOW
    edit_surface_violation_risk: RiskLevel = RiskLevel.LOW
    traceability_risk: RiskLevel = RiskLevel.MEDIUM


class ResultJudgeOutput(JudgeOutput):
    """Output from the Result Judge."""

    judge_type: str = "result"
    result_quality: RiskLevel = RiskLevel.MEDIUM
    stability: RiskLevel = RiskLevel.MEDIUM
    concentration_risk: RiskLevel = RiskLevel.MEDIUM
    cost_fragility_risk: RiskLevel = RiskLevel.LOW
    perturbation_fragility_risk: RiskLevel = RiskLevel.MEDIUM
    promotion_recommendation: PromotionRecommendation = PromotionRecommendation.ITERATE
    qualified_improvement: bool = False


class MutationJudgeOutput(JudgeOutput):
    """Output from the Mutation Judge."""

    judge_type: str = "mutation"
    category: MutationCategory | str = ""
    mechanism_coherence: RiskLevel = RiskLevel.MEDIUM
    search_abuse_risk: RiskLevel = RiskLevel.MEDIUM
    degrees_of_freedom_risk: RiskLevel = RiskLevel.MEDIUM
    family_preservation_confidence: RiskLevel = RiskLevel.MEDIUM
    budget_status: BudgetStatus = BudgetStatus.WITHIN_BUDGET


# ---------------------------------------------------------------------------
# Mutation models
# ---------------------------------------------------------------------------


class MutationProposal(BaseModel):
    """A proposed mutation to a family's research code."""

    family_id: str
    category: MutationCategory
    description: str
    reason: str
    proposed_change: str
    falsification_test: str


class MutationBudget(BaseModel):
    """Tracks remaining mutation budget per category."""

    horizon: int = 2
    venue: int = 2
    representation: int = 1
    combination: int = 1
    regime: int = 1
    structural: int = 0

    def remaining(self, category: MutationCategory) -> int:
        return getattr(self, category.value)

    def consume(self, category: MutationCategory) -> MutationBudget:
        current = self.remaining(category)
        if current <= 0:
            raise ValueError(f"No budget remaining for {category.value}")
        return self.model_copy(update={category.value: current - 1})


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class BacktestResultSummary(BaseModel):
    """Summary of backtest results for one symbol."""

    symbol: str
    timeframe: str
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0
    total_return: float = 0.0
    calmar: float = 0.0
    turnover: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    exposure_pct: float = 0.0
    all_metrics: dict[str, Any] = Field(default_factory=dict)


class RobustnessTest(BaseModel):
    """Result of a single robustness test."""

    test_name: str
    passed: bool
    degradation_pct: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict)


class RobustnessResult(BaseModel):
    """Aggregated results from the robustness battery."""

    tests: list[RobustnessTest] = Field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(t.passed for t in self.tests)

    @property
    def pass_rate(self) -> float:
        if not self.tests:
            return 0.0
        return sum(1 for t in self.tests if t.passed) / len(self.tests)


class CompositeScore(BaseModel):
    """Composite quality score for an iteration."""

    alpha_quality: float = 0.0
    stability_bonus: float = 0.0
    turnover_penalty: float = 0.0
    drawdown_penalty: float = 0.0
    concentration_penalty: float = 0.0
    fragility_penalty: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.alpha_quality
            + self.stability_bonus
            - self.turnover_penalty
            - self.drawdown_penalty
            - self.concentration_penalty
            - self.fragility_penalty
        )


# ---------------------------------------------------------------------------
# Strike models
# ---------------------------------------------------------------------------


class StrikeRecord(BaseModel):
    """Record of a single strike event."""

    iteration_id: str
    reason: str
    is_red: bool = False
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Iteration model
# ---------------------------------------------------------------------------


class Iteration(BaseModel):
    """One specific attempt to refine or test a family."""

    iteration_id: str
    family_id: str
    stage: IterationStage = IterationStage.DRAFT_PLAN
    proposal_type: str = "initial"
    mutation_category: MutationCategory | None = None
    plan: str = ""
    judge_outputs: list[JudgeOutput] = Field(default_factory=list)
    guard_results: list[dict[str, Any]] = Field(default_factory=list)
    backtest_results: list[BacktestResultSummary] = Field(default_factory=list)
    robustness_results: RobustnessResult | None = None
    verdict: Verdict | None = None
    composite_score: CompositeScore | None = None
    qualified_improvement: bool = False
    strikes_added: list[StrikeRecord] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Family model
# ---------------------------------------------------------------------------


class AllowedMutations(BaseModel):
    """Pre-registered mutation lanes for a family."""

    horizon: list[str] = Field(default_factory=list)
    venue: list[str] = Field(default_factory=list)
    representation: list[str] = Field(default_factory=list)
    combination: list[str] = Field(default_factory=list)
    regime_filter: list[str] = Field(default_factory=list)


class IdeaFamily(BaseModel):
    """Represents one research family descended from a seed or fork."""

    family_id: str
    parent_family_id: str | None = None
    seed_id: str
    base_hypothesis: str
    mechanism: str
    allowed_mutations: AllowedMutations = Field(default_factory=AllowedMutations)
    mutation_budget: MutationBudget = Field(default_factory=MutationBudget)
    state: FamilyState = FamilyState.NEW
    strike_count: int = 0
    red_strike_count: int = 0
    strike_history: list[StrikeRecord] = Field(default_factory=list)
    best_qualified_score: float = 0.0
    current_iteration: int = 0
    failure_taxonomy: list[str] = Field(default_factory=list)
    overfit_flag_history: list[str] = Field(default_factory=list)
    score_history: list[float] = Field(default_factory=list)
    editable_files: list[str] = Field(default_factory=lambda: [
        "research/features.py",
        "research/labels.py",
        "research/model_config.py",
        "research/signal_combiner.py",
    ])
    forbidden_files: list[str] = Field(default_factory=lambda: [
        "engine/*",
        "configs/splits.yaml",
        "configs/costs.yaml",
        "judges/prompts/*",
        "reports/*",
    ])
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def validate_strikes(self) -> IdeaFamily:
        if self.red_strike_count > self.strike_count:
            raise ValueError("red_strike_count cannot exceed strike_count")
        return self


# ---------------------------------------------------------------------------
# Guard result
# ---------------------------------------------------------------------------


class GuardResult(BaseModel):
    """Result from a deterministic guard check."""

    guard_name: str
    passed: bool
    violations: list[str] = Field(default_factory=list)
    is_red_strike: bool = False
