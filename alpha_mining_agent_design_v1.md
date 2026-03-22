# Alpha Mining Agent System v1
## Adversarial, file-based, mutation-bounded research loop for crypto strategy discovery

## 1. Purpose

This system is a local, file-based crypto alpha research loop designed to test seeded ideas under strict anti-overfitting controls.

Its job is to:

- ingest seed ideas from papers, tweets, forum posts, articles, or user observations,
- convert raw seeds into structured research families,
- let a coding agent implement bounded refinements,
- use tiered LLM judges to reject weak, leaky, or overfit-prone ideas,
- run deterministic guards and evaluation stages,
- enforce mutation budgets and fork rules,
- cancel weak idea families automatically using a **3-strikes rule**,
- preserve all system state in **Markdown files** plus small JSON artifacts,
- operate without a database and without Docker.

This document is intended to be fed into a **coding agent** so it can generate an implementation plan and build the repo scaffold.

---

## 2. Design stance

The design assumes the main failure mode in autonomous alpha mining is **not lack of idea generation**, but **search-space abuse**.

Therefore, the system is designed around:

- search governance over raw creativity,
- acceptance discipline over backtest optimism,
- family-level memory over isolated trial evaluation,
- deterministic policy over agent improvisation.

The system does **not** assume an LLM judge can determine whether alpha is truly real.  
Instead, it tries to prevent the most common ways false alpha is manufactured:

- leakage,
- repeated local mutation,
- hidden hyperparameter sweeps,
- post hoc narrative rescue,
- regime carving,
- evaluator tampering,
- holdout contamination.

---

## 3. Goals

### 3.1 Primary goals

- Create a reproducible workflow for evaluating seeded crypto strategy ideas.
- Support iterative refinement of a hypothesis while keeping refinement bounded.
- Use adversarial LLM judges before and after coding.
- Kill weak ideas early and automatically.
- Keep all workflow state legible and auditable through files.

### 3.2 Secondary goals

- Make the system easy for a human to inspect and steer.
- Make the architecture simple enough for a coding agent to implement reliably.
- Keep the first version local, explicit, and low-magic.

---

## 4. Non-goals

This version does **not** aim to:

- run live trading,
- manage distributed workers,
- use a database,
- use Docker or container sandboxing,
- support arbitrary codebase mutation,
- support unrestricted autonomous idea generation,
- replace human judgment in promotion decisions,
- guarantee discovery of real alpha.

---

## 5. Core principles

### 5.1 File-native state
All important state must live in Markdown files with YAML frontmatter, plus machine-readable JSON artifacts where needed.

### 5.2 Deterministic control, probabilistic review
The workflow engine must be deterministic.  
LLMs may propose, review, summarize, and criticize, but they do not directly control legal transitions.

### 5.3 Constrained edit surface
The coding agent may edit only a small whitelist of files under `research/`.  
The evaluator, accounting, split configuration, and cost assumptions are immutable during ordinary iterations.

### 5.4 Family-level accountability
The unit of survival is the **idea family**, not the individual backtest.

### 5.5 Mutation must be governed
A mutation is acceptable only if it stays within category, budget, and mechanism.  
Otherwise it must be rejected or forked into a child family.

### 5.6 Seed-first research
Ideas should start from seeds, not from unconstrained brainstorming.

### 5.7 Anti-overfitting by structure
The system reduces false alpha by policy:
- tiered judges,
- hard guards,
- mutation budgets,
- fork rules,
- history-aware review,
- 3-strikes cancellation.

---

## 6. System overview

The system has seven major layers:

1. **Seed Intake**
2. **Seed Distillation and Screening**
3. **Idea Family Lifecycle**
4. **Tiered Judge Pipeline**
5. **Deterministic Guard Layer**
6. **Evaluation Pipeline**
7. **Mutation Review and 3-Strikes Policy**

---

## 7. Implementation stack

## 7.1 Programming language

- **Python 3.12+**

## 7.2 Core libraries and components

- **Pydantic v2** for schemas and validation
- **DuckDB** for querying Parquet-based research data
- **Git** for versioning, diff inspection, rollback, and auditability
- **Markdown + YAML frontmatter** for state storage
- **Plain Python orchestrator** for state transitions and workflow execution

## 7.3 Explicit exclusions

- No database
- No Docker
- No Temporal
- No heavy workflow engine
- No generalized FSM framework required for v1

---

## 8. Repo layout

```text
alpha_research/
  README.md
  CLAUDE.md
  STATE.md
  IDEAS.md
  STRIKES.md

  seeds/
    inbox/
    distilled/
    accepted/
    rejected/

  families/
    <family_id>/
      FAMILY.md
      PLAN.md
      HISTORY.md
      CURRENT_ITERATION.md
      MUTATION_POLICY.md
      best_result.json
      strike_count.json

  ledger/
    <timestamp>_<family_id>_<iter_id>.md

  judges/
    prompts/
      seed_judge.md
      leakage_judge.md
      overfit_judge.md
      realism_judge.md
      code_judge.md
      result_judge.md
      mutation_judge.md
    verdicts/
      <family_id>_<iter_id>_<stage>.md

  reports/
    <family_id>/
      iter_<n>_backtest.json
      iter_<n>_backtest.md
      iter_<n>_robustness.json
      iter_<n>_robustness.md
      iter_<n>_holdout.json
      iter_<n>_holdout.md

  configs/
    universe.yaml
    costs.yaml
    splits.yaml
    guardrails.yaml

  engine/
    backtest_core.py
    accounting.py
    evaluator.py
    robustness.py

  research/
    features.py
    labels.py
    model_config.py
    signal_combiner.py

  app/
    domain/
      states.py
      events.py
      models.py
      scoring.py
      strikes.py
      mutation_policy.py
    workflow/
      transitions.py
      orchestrator.py
      seed_flow.py
      family_flow.py
    guards/
      check_edit_surface.py
      timestamp_guard.py
      split_guard.py
      config_guard.py
      reproducibility_guard.py
    agents/
      researcher.py
      judge_seed.py
      judge_leakage.py
      judge_overfit.py
      judge_realism.py
      judge_code.py
      judge_result.py
      judge_mutation.py
    storage/
      markdown_store.py
      artifact_store.py

  scripts/
    intake_seed.py
    distill_seed.py
    screen_seed.py
    create_family.py
    run_loop.py
    run_iteration.py
    run_guards.py
    run_backtest.py
    run_holdout.py
    update_state.py
```

---

## 9. State model

There are two main domain objects:

### 9.1 IdeaFamily

Represents one research family descended from one seed or one fork.

Required fields:

- `family_id`
- `parent_family_id` optional
- `seed_id`
- `base_hypothesis`
- `mechanism`
- `allowed_mutations`
- `state`
- `strike_count`
- `red_strike_count`
- `best_qualified_score`
- `current_iteration`
- `failure_taxonomy`
- `editable_files`
- `forbidden_files`

### 9.2 Iteration

Represents one specific attempt to refine or test a family.

Required fields:

- `iteration_id`
- `family_id`
- `proposal_type`
- `mutation_category` optional
- `plan`
- `judge_outputs`
- `guard_results`
- `backtest_results`
- `robustness_results`
- `verdict`
- `qualified_improvement`
- `strikes_added`

---

## 10. File-native state contracts

Markdown is the human-facing source of truth.  
YAML frontmatter is the machine-facing state contract.

### 10.1 `STATE.md`

Purpose:
- global active family pointer,
- latest top-level state,
- resume point after interruption.

Example structure:

```md
---
active_family: micro_momo_5m_v1
state: code_in_review
current_iteration: 2
strike_count: 1
red_strike_count: 0
best_qualified_score: 0.88
last_transition_at: 2026-03-21T14:22:00+08:00
---
# Global State
```

### 10.2 `families/<family_id>/FAMILY.md`

Must contain:

- `family_id`
- `state`
- `strike_count`
- `red_strike_count`
- `best_qualified_score`
- `hypothesis`
- `mechanism`
- `editable_files`
- `forbidden_files`
- `allowed_mutations`
- `current_iteration`

### 10.3 `families/<family_id>/HISTORY.md`

Append-only transition log and audit trail.

### 10.4 `families/<family_id>/CURRENT_ITERATION.md`

Must contain:

- `family_id`
- `iteration_id`
- `stage`
- `mutation_category` optional
- `active_constraints`
- `pending_review_items`
- `changed_files`
- `expected_tests`

### 10.5 Judge verdict files

Must contain:

- `family_id`
- `iteration_id`
- `stage`
- `judge_type`
- `verdict`
- `risks`
- `must_fix`
- `required_tests`
- `taxonomy_tags`

### 10.6 Ledger entries

Each iteration writes a detailed ledger file:

- what was proposed,
- what judges said,
- what changed,
- what tests ran,
- what score resulted,
- how strikes changed.

---

## 11. Seed intake system

## 11.1 Why seed intake exists

The system should not depend on unrestricted idea generation.  
It should begin from a seed provided by:

- a research paper,
- a tweet or thread,
- a blog article,
- a forum discussion,
- a personal market observation,
- or a mutation from an existing family.

## 11.2 Seed directories

- `seeds/inbox/`
- `seeds/distilled/`
- `seeds/accepted/`
- `seeds/rejected/`

## 11.3 Seed lifecycle

```text
SEED_CAPTURE
  -> SEED_DISTILLED
  -> SEED_SCREENED
  -> FAMILY_CREATED
  -> QUEUED
```

## 11.4 Distilled seed schema

A raw seed is transformed into a structured research card.

Required fields:

- `seed_type`
- `source_title`
- `raw_claim`
- `market`
- `horizon`
- `mechanism`
- `required_data`
- `testable_hypothesis`
- `ambiguities`
- `risk_flags`

## 11.5 Seed judge

The Seed Judge determines:

- is the seed testable,
- is the mechanism coherent enough,
- is the required data available,
- is the scope narrow enough,
- is it duplicative,
- should it be narrowed before family creation.

Possible outputs:

- `accept`
- `accept_with_narrowing`
- `reject`
- `merge_with_existing_family`

---

## 12. Family lifecycle states

### 12.1 Family states

```text
NEW
QUEUED
PLAN_IN_REVIEW
PLAN_REVISION_REQUIRED
PLAN_APPROVED
CODING
CODE_IN_REVIEW
CODE_REVISION_REQUIRED
CODE_APPROVED
GUARDS_RUNNING
BACKTEST_RUNNING
RESULTS_IN_REVIEW
ITERATE
PROMOTE_TO_HOLDOUT
HOLDOUT_RUNNING
PROMOTE_TO_PAPER
PAPER_FORWARD_RUNNING
HUMAN_REVIEW
CANCELLED_3_STRIKES
ARCHIVED_REJECTED
DONE
```

### 12.2 High-level state transitions

```text
NEW -> QUEUED

QUEUED -> PLAN_IN_REVIEW

PLAN_IN_REVIEW
  -> PLAN_APPROVED
  -> PLAN_REVISION_REQUIRED
  -> CANCELLED_3_STRIKES

PLAN_APPROVED -> CODING
CODING -> CODE_IN_REVIEW

CODE_IN_REVIEW
  -> CODE_APPROVED
  -> CODE_REVISION_REQUIRED
  -> CANCELLED_3_STRIKES

CODE_APPROVED -> GUARDS_RUNNING

GUARDS_RUNNING
  -> BACKTEST_RUNNING
  -> QUEUED
  -> CANCELLED_3_STRIKES

BACKTEST_RUNNING
  -> RESULTS_IN_REVIEW
  -> QUEUED
  -> CANCELLED_3_STRIKES

RESULTS_IN_REVIEW
  -> ITERATE
  -> PROMOTE_TO_HOLDOUT
  -> ARCHIVED_REJECTED
  -> CANCELLED_3_STRIKES

ITERATE -> QUEUED

PROMOTE_TO_HOLDOUT -> HOLDOUT_RUNNING

HOLDOUT_RUNNING
  -> PROMOTE_TO_PAPER
  -> ARCHIVED_REJECTED

PROMOTE_TO_PAPER -> PAPER_FORWARD_RUNNING

PAPER_FORWARD_RUNNING
  -> HUMAN_REVIEW
  -> ARCHIVED_REJECTED

HUMAN_REVIEW
  -> DONE
  -> ARCHIVED_REJECTED
```

---

## 13. Iteration lifecycle

Each family can undergo repeated iterations.

### 13.1 Iteration stage flow

```text
DRAFT_PLAN
  -> PLAN_JUDGED
  -> CODE_WRITE
  -> CODE_JUDGED
  -> RUN_GUARDS
  -> RUN_BACKTEST
  -> RUN_ROBUSTNESS
  -> RESULT_JUDGED
  -> ITERATION_SUCCESS / ITERATION_FAILED
```

### 13.2 Key concept

- **Family state** decides long-term survival.
- **Iteration stage** decides step-by-step execution.

---

## 14. Tiered judge mechanism

The judge system is adversarial and multi-stage.

### 14.1 Judge roles

#### Seed Judge
Used before family creation.

#### Leakage Judge
Focus:
- time leakage,
- label leakage,
- split contamination,
- unsafe joins,
- normalization leakage.

#### Overfitting Judge
Focus:
- result-chasing,
- hidden parameter sweeps,
- excessive degrees of freedom,
- repeated local search.

#### Market Realism Judge
Focus:
- fee realism,
- turnover realism,
- liquidity assumptions,
- execution plausibility.

#### Code Smell Judge
Focus:
- suspicious implementation,
- hidden state,
- brittle logic,
- helper misuse,
- caching contamination.

#### Result Judge
Focus:
- suspicious concentration,
- instability,
- edge collapse under perturbation,
- fragile robustness.

#### Mutation Judge
Focus:
- whether a proposed mutation is disciplined refinement or search-space abuse.

---

## 15. Judge stages

### 15.1 Tier 0: Seed review
Before family creation.

### 15.2 Tier 1: Plan review
Before coding.

Required judges:
- Leakage Judge
- Overfitting Judge
- Market Realism Judge

### 15.3 Tier 2: Code review
After coding, before backtest.

Required judges:
- Leakage Judge
- Code Smell Judge
- optionally Market Realism Judge

### 15.4 Tier 3: Result review
After validation and robustness.

Required judges:
- Result Judge
- Overfitting Judge
- Market Realism Judge

### 15.5 Tier 4: Human review
Only after holdout and paper-forward success.

---

## 16. Judge output contract

All judge outputs must be structured.

Required fields:

- `verdict`: `approve`, `approve_with_constraints`, `revise`, `reject`, `fork_required`
- `risk_scores`
- `must_fix`
- `required_tests`
- `taxonomy_tags`
- `reasoning_summary`

Free text may exist, but only after the schema-valid output block.

---

## 17. Deterministic guard layer

LLM review is not sufficient.  
Hard guards must run before backtest and before promotion.

### 17.1 Edit surface guard

Before execution:

- inspect changed files using git diff or equivalent,
- ensure only allowed files changed.

Allowed:
- `research/features.py`
- `research/labels.py`
- `research/model_config.py`
- `research/signal_combiner.py`

Forbidden:
- `engine/*`
- `configs/splits.yaml`
- `configs/costs.yaml`
- `judges/prompts/*`
- `reports/*`
- holdout data paths

Violation:
- immediate red strike

### 17.2 Time integrity guard

Rules:
- `feature_timestamp <= decision_timestamp`
- `label_start > decision_timestamp`
- no future bars in rolling computations
- no future leakage in joins

### 17.3 Split isolation guard

Rules:
- holdout cannot be used before promotion
- train/validation partitions are immutable
- cached artifacts cannot cross split boundaries

### 17.4 Config guard

Rules:
- evaluator hash unchanged
- split config unchanged
- cost config unchanged unless explicitly approved

### 17.5 Reproducibility guard

Must log:
- dataset version
- run seed/version
- code commit hash
- run metadata

---

## 18. Evaluation pipeline

### 18.1 Validation backtest

Run only after:
- plan approval,
- code approval,
- guard pass.

Required metrics:
- net Sharpe or IR,
- max drawdown,
- turnover,
- cost sensitivity,
- symbol concentration,
- time stability,
- regime stability.

### 18.2 Robustness battery

Mandatory before holdout.

Suggested tests:
- walk-forward validation,
- rolling-window validation,
- cost x2 / x3,
- delay/slippage perturbation,
- leave-one-asset-out,
- horizon perturbation,
- placebo or shuffled sanity checks where appropriate.

### 18.3 Holdout stage

Only for shortlisted candidates.

Rule:
- holdout is untouched before this stage.

### 18.4 Paper-forward stage

Only after holdout success.

### 18.5 Human review

Only after paper-forward success.

---

## 19. Composite score

Selection must not rely on raw Sharpe alone.

Suggested composite score:

`score = alpha_quality + stability_bonus - turnover_penalty - drawdown_penalty - concentration_penalty - fragility_penalty`

Where:

- `alpha_quality`: post-cost validation quality
- `stability_bonus`: consistency across assets and periods
- `turnover_penalty`: excessive turnover
- `drawdown_penalty`: poor path behavior
- `concentration_penalty`: one-asset or one-regime dependence
- `fragility_penalty`: performance collapse under perturbation

### 19.1 Qualified improvement

An iteration counts as a **qualified improvement** only if:

- composite score improves beyond threshold,
- required robustness tests pass,
- and no major stability dimension materially worsens.

Approval alone does not count.

---

## 20. 3-strikes rule

### 20.1 Principle

Weak ideas should die quickly.  
Strike policy operates at the **family level**.

### 20.2 When to add a strike

A strike is added when:

- a plan repeats a previously flagged issue,
- code review finds blocking problems,
- guard checks fail,
- backtest does not materially beat the family baseline,
- robustness fails,
- mutation review finds search abuse.

### 20.3 Red strikes

Reserved for severe failures:

- leakage,
- split contamination,
- forbidden file modification,
- disguised structural mutation,
- obvious regime carving of bad history.

### 20.4 Cancellation rules

Cancel family if:

- `strike_count >= 3`
- or `red_strike_count >= 2`

### 20.5 Reset rules

Strikes reset only after a **qualified improvement**.  
Approval without real improvement does not reset strikes.

---

## 21. Mutation Review Policy

## 21.1 Purpose

Mutation review prevents family refinement from turning into local brute-force search.

The Mutation Judge does not prove alpha is real.  
It determines whether a proposed change is:

- disciplined continuation of a hypothesis,
or
- uncontrolled expansion of the search space.

## 21.2 Mutation categories

Every mutation must be assigned exactly one category:

- `horizon`
- `venue`
- `representation`
- `combination`
- `regime`
- `structural`

## 21.3 Definitions

### Horizon mutation
Example:
- 5m to 15m

### Venue mutation
Example:
- Binance to Bybit

### Representation mutation
Example:
- raw imbalance to bucketed imbalance

### Combination mutation
Example:
- imbalance + liquidation burst

### Regime mutation
Example:
- activate only in high-volatility periods

### Structural mutation
Example:
- directional strategy to relative-value strategy

Structural mutations usually require a fork.

---

## 22. Mutation budgets

Recommended defaults per family:

- horizon: max 2
- venue: max 2
- representation: max 1
- combination: max 1
- regime: max 1
- structural: 0 inside family

Budget is consumed when a mutation is approved for implementation.

---

## 23. Mutation review inputs

The Mutation Judge must always receive:

- family id,
- base hypothesis,
- original mechanism,
- prior mutation history,
- budget status,
- similar failed mutations,
- strike count,
- precise proposed change,
- reason for the change,
- minimum falsification test.

Without this history, the judge should not approve the mutation.

---

## 24. Mutation review questions

Every mutation review must answer:

1. Does this preserve the original mechanism?
2. How much additional search freedom does this add?
3. Does it look motivated before results, or after disappointing results?
4. Is there budget remaining?
5. Is this still the same family?

---

## 25. Mutation outcomes

Possible outputs:

- `approve`
- `approve_with_constraints`
- `reject`
- `fork_required`

---

## 26. Category-specific mutation policy

### 26.1 Horizon mutation

Usually allowed if:

- within a pre-approved candidate set,
- mechanism plausibly admits different decay speed,
- budget remains.

Suspicious if:

- many nearby horizons already tried,
- the new horizon is chosen only after failures,
- it acts as hidden hyperparameter search.

### 26.2 Venue mutation

Often encouraged as portability testing.

Allowed if:

- feature definition is unchanged,
- venue has similar market structure,
- the purpose is generalization rather than venue-shopping.

### 26.3 Representation mutation

Allowed only once unless forked.

Should remain simple and preserve the same information source.

### 26.4 Combination mutation

Allowed once, and only with simple fixed rules.

Suspicious if:
- many weak signals are being stacked,
- weights are heavily tuned,
- combination logic becomes complex.

### 26.5 Regime mutation

High-suspicion category.

Allowed only if:
- regime dependence fits the original mechanism,
- regime variable is simple and externally defined,
- it is not obviously being used to hide losing history.

### 26.6 Structural mutation

Usually not allowed inside the same family.  
Must fork.

---

## 27. Pre-registered mutation lanes

At family creation, allowed mutation lanes should be declared.

Example:

```json
{
  "family_id": "obi_continuation_v1",
  "allowed_mutations": {
    "horizon": ["5m", "15m"],
    "venue": ["binance", "bybit"],
    "representation": ["raw", "bucketed"],
    "regime_filter": ["high_vol_only"],
    "combination": []
  }
}
```

Proposals outside these lanes should normally be rejected or forked.

---

## 28. Fork policy

A child family must be created when a proposed change materially alters the hypothesis.

Typical fork triggers:

- directional to relative-value conversion,
- continuation to reversal switch,
- new data modality,
- major model complexity increase,
- weighted ensemble beyond simple rule,
- new target definition.

A fork inherits:

- ancestry,
- useful prior concerns,
- related notes,

but gets:

- new state,
- new strike count,
- new mutation budget.

---

## 29. Transition engine

The workflow engine should be plain Python.

Responsibilities:

- validate current state,
- validate event legality,
- update strikes,
- decide next state,
- persist state back to Markdown,
- append history logs,
- emit artifacts.

A transition function should conceptually take:

- current family state,
- event,
- transition context,

and return:

- next state,
- strike updates,
- side effects.

No LLM should write state files directly without schema validation.

---

## 30. Event model

Suggested family events:

- `SEED_ACCEPTED`
- `FAMILY_CREATED`
- `PLAN_SUBMITTED`
- `PLAN_APPROVED`
- `PLAN_REJECTED`
- `CODE_SUBMITTED`
- `CODE_APPROVED`
- `CODE_REJECTED`
- `GUARDS_PASSED`
- `GUARDS_FAILED`
- `BACKTEST_COMPLETED`
- `RESULT_APPROVED`
- `RESULT_REJECTED`
- `PROMOTE_HOLDOUT`
- `HOLDOUT_FAILED`
- `PROMOTE_PAPER`
- `PAPER_FAILED`
- `HUMAN_APPROVED`
- `HUMAN_REJECTED`
- `CANCELLED_3_STRIKES`

---

## 31. Orchestrator behavior

The top-level orchestrator should:

1. read global state
2. identify the active or queued family
3. determine the legal next step
4. invoke the correct agent or guard
5. validate structured output
6. apply transition logic
7. write updated files
8. continue until the family reaches a terminal or waiting state

The orchestrator must be deterministic.  
Agent outputs are inputs to transitions, not direct state mutations.

---

## 32. Suggested Python modules

### 32.1 `app/domain/states.py`

Defines:
- `FamilyState`
- `IterationStage`
- `MutationCategory`

### 32.2 `app/domain/events.py`

Defines legal workflow events.

### 32.3 `app/domain/models.py`

Pydantic models for:
- seed card,
- family model,
- iteration model,
- judge verdict,
- backtest result,
- robustness result,
- mutation proposal.

### 32.4 `app/domain/scoring.py`

Composite score computation and qualified-improvement logic.

### 32.5 `app/domain/strikes.py`

Strike accounting and cancellation checks.

### 32.6 `app/domain/mutation_policy.py`

Budget accounting, fork rules, and category policy.

### 32.7 `app/workflow/transitions.py`

Transition engine:
- state legality,
- strike updates,
- side effects.

### 32.8 `app/workflow/orchestrator.py`

Top-level family execution loop.

### 32.9 `app/workflow/seed_flow.py`

Seed intake, distillation, screening, family creation.

### 32.10 `app/workflow/family_flow.py`

Plan review, code review, guards, evaluation, promotion.

### 32.11 `app/storage/markdown_store.py`

Load/write Markdown with YAML frontmatter, append history logs.

### 32.12 `app/guards/*`

All deterministic checks:
- edit surface,
- time integrity,
- split isolation,
- config immutability,
- reproducibility.

### 32.13 `app/agents/*`

Thin wrappers around:
- Researcher
- Seed Judge
- Leakage Judge
- Overfitting Judge
- Realism Judge
- Code Judge
- Result Judge
- Mutation Judge

Each wrapper must return schema-validated structured output.

---

## 33. Coding agent requirements

This design doc is meant to be fed into a coding agent.

The coding agent should interpret this document as requiring:

### 33.1 Build order

1. domain enums and Pydantic schemas
2. Markdown storage layer
3. explicit transition engine
4. seed intake flow
5. family lifecycle flow
6. deterministic guards
7. judge wrappers
8. backtest and robustness runners
9. top-level orchestrator script

### 33.2 Hard constraints

The implementation must preserve:

- file-based state only,
- no database,
- no Docker,
- constrained edit surface,
- family-level 3-strikes rule,
- mutation budgets,
- fork rules,
- schema-validated judge outputs,
- immutable evaluator files.

### 33.3 Implementation style

Preferred style:

- modular Python package,
- explicit enums and models,
- low magic,
- readable file contracts,
- machine-parseable Markdown,
- side effects written through a storage layer.

---

## 34. Success criteria for v1

The implementation is successful if it can:

- accept a raw seed file,
- distill and screen it,
- create a family,
- run plan review,
- enforce mutation policy,
- constrain file edits,
- run code review and guard layer,
- execute validation and robustness evaluation,
- apply strike and cancellation policy,
- write all state and history to Markdown,
- produce a shortlist of surviving families.

---

## 35. Risks and cautions

### 35.1 False sense of rigor
This system improves research hygiene.  
It does not guarantee genuine alpha.

### 35.2 Judge over-textuality
Judges can still be fooled by elegant narratives if the structured history is poor.

### 35.3 Family boundary drift
If fork rules are weak, families can become garbage bags for unrelated rescue attempts.

### 35.4 Regime filters are dangerous
Regime filters should be treated with special suspicion.

### 35.5 File sprawl
Markdown state works only if naming, frontmatter, and logging are strict.

---

## 36. Final summary

This system is a **file-native, adversarially reviewed, mutation-bounded crypto alpha research loop**.

Its key design choices are:

- seeds instead of unconstrained brainstorming,
- idea families instead of isolated backtests,
- tiered judges instead of single-pass approval,
- deterministic guards instead of trusting LLM review,
- mutation budgets instead of open-ended tweaking,
- fork rules for structural changes,
- 3 strikes to kill weak families quickly,
- Markdown as the canonical state layer.

The design is intentionally optimized for a coding agent to turn into an implementation plan and repo scaffold without inventing core architecture on its own.
