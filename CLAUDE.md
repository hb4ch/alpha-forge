# Alpha Forge - Claude Code Instructions

## Project Overview
Alpha Forge is an adversarial, file-based, mutation-bounded crypto alpha research loop.
It integrates with crypto-pegasus (`../crypto-pegasus`) for backtesting.

## Architecture
- `alpha_forge/app/domain/` - Enums, Pydantic models, scoring, strikes, mutation policy
- `alpha_forge/app/workflow/` - Transition engine, seed flow, family flow, orchestrator
- `alpha_forge/app/guards/` - Deterministic guard checks (edit surface, time integrity, etc.)
- `alpha_forge/app/agents/` - LLM judge wrappers and researcher agent
- `alpha_forge/app/storage/` - Markdown+YAML and JSON artifact storage
- `alpha_forge/engine/` - Backtest runner, robustness runner, research strategy bridge
- `configs/` - Universe, costs, splits, guardrails YAML configs
- `judges/prompts/` - Judge prompt templates
- `scripts/` - CLI entry points

## Key Workflows
1. Seed intake: `scripts/intake_seed.py` → distill → screen → create family
2. Run loop: `scripts/run_loop.py` → orchestrator drives family through iterations
3. Single iteration: `scripts/run_iteration.py`
4. Init workspace: `scripts/init_workspace.py`

## Edit Surface Constraints
When editing research code for a family, you may ONLY modify:
- `research/features.py` - Feature computation
- `research/labels.py` - Label computation (optional)
- `research/model_config.py` - Configuration parameters
- `research/signal_combiner.py` - Signal generation logic

You must NOT modify:
- `engine/*` - Backtest infrastructure
- `configs/splits.yaml` - Data splits
- `configs/costs.yaml` - Cost assumptions
- `judges/prompts/*` - Judge prompts
- `reports/*` - Generated reports

## Research File Contracts
- `features.py`: must export `compute_features(bars: pd.DataFrame) -> pd.DataFrame`
- `model_config.py`: must export `MODEL_CONFIG: dict`
- `signal_combiner.py`: must export `combine_signals(features: pd.DataFrame, config: dict) -> pd.Series`
- `labels.py`: must export `compute_labels(bars: pd.DataFrame) -> pd.Series`

## Signal Contract
Signals must be a pd.Series with values between -1.0 and 1.0:
- `1.0` = fully long, `-1.0` = fully short
- Fractional values supported (e.g. `0.5` = 50% long exposure)
- `0.0` = flat
- `NaN` = no signal (warmup period)

## Risk Management (MODEL_CONFIG keys)
- `"timeframe"`: REQUIRED, must match seed horizon
- `"stop_loss_pct"`: engine-level stop-loss (e.g. 0.02 = 2%)
- `"take_profit_pct"`: engine-level take-profit (e.g. 0.05 = 5%)
- `"trailing_stop_pct"`: engine-level trailing stop (e.g. 0.03 = 3%)

## Available Bar Columns
`open`, `high`, `low`, `close`, `volume`, `buy_volume`, `vwap`, `trade_count`

## Rules
- NO forward-looking operations (no `.shift(-N)` with N > 0)
- NO external data sources
- NO database access
- Only use pandas and numpy for computations
- Keep strategies simple and mechanistically motivated
