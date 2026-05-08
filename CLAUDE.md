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

## Engine Integration Patterns

The engine's `stop_loss_pct` / `trailing_stop_pct` fire **independently of the
signal**. If your strategy has its own bar-level exit logic (ATR-scaled stops,
range re-entry, propulsion-efficiency, slope-reversal), you must decide where
that logic lives. There are two valid patterns; pick one explicitly.

### Pattern A — signal-driven (engine stops as catastrophic safety net)

Use when exits are **structurally simple** and align with fixed-pct stops.

- `signal_combiner` emits the desired position; signal returns to 0 *only* on
  natural mechanism conditions (e.g. trend gate closes, regime flips)
- Engine's `stop_loss_pct` (~0.02–0.05) is a small safety net for catastrophic
  moves; rarely fires in practice
- Worked example: `eth_exhibits_institutional_momentum_over_weekly_ho_v1`
  (the only family in this workspace with a passing holdout). It uses RSI +
  20-day momentum + 20-day z-score with a binary trend-strength gate; the
  signal goes to 0 when the gate closes; engine `stop_loss_pct=0.025` is the
  safety net.

### Pattern B — event-driven (signal owns ALL exits, engine stops disabled)

Use when exits are **structurally rich** — ATR-scaled stops, range re-entry,
trailing stops, multiple exit conditions.

Why: the engine's pct stops fire concurrently with signal-driven exits. Your
2×ATR stop will be pre-empted by the engine's tighter `stop_loss_pct`. Result:
trade dynamics diverge from a custom event-driven backtest, often dramatically
(see `volatility_compression_atrclose_and_40-bar_high-lo_v1` for a 100×
trade-count discrepancy that this pattern resolves).

- `signal_combiner` keeps a stateful loop tracking entry, init stop, trailing
  extreme, and exit conditions. When the strategy *would* exit, return signal
  to 0 immediately
- **Magnitude must be constant** while in position (e.g. always +0.5). Any
  per-bar `vol_scale * raw` modulation generates per-bar position resizing
  that the engine counts as new trades
- Engine stops are **sentinels only**: `stop_loss_pct=0.30`, `trailing_stop_pct=0.30`
  (i.e. effectively disabled — a 30% adverse move triggers a kill switch that
  shouldn't fire under normal operation)
- Worked example skeleton:

  ```python
  def combine_signals(features, config):
      raw = pd.Series(0.0, index=features.index)
      in_long = False; entry_price = init_stop = trailing_extreme = None
      for i in range(len(features)):
          c = features['close'].iloc[i]
          atr = features['atr_20'].iloc[i]
          if not in_long:
              if features['long_entry'].iloc[i]:
                  in_long = True
                  entry_price = c
                  init_stop = c - 2 * atr
                  trailing_extreme = c
          else:
              trailing_extreme = max(trailing_extreme, c)
              trail = trailing_extreme - 2.5 * atr
              if c < init_stop or c < trail or c < features['range_high_at_entry'].iloc[i]:
                  in_long = False
          if in_long:
              raw.iloc[i] = 0.5  # CONSTANT magnitude
      return raw
  ```

### Choosing between the patterns

Quick decision rule:
- Strategy has **only entry conditions**, exits are "until conditions reverse"
  → Pattern A
- Strategy has **explicit exit conditions** distinct from entry conditions
  → Pattern B
- When in doubt, run `scripts/preflight_via_engine.py` against both versions
  and pick the one that produces consistent metrics across train/val/holdout.

### Pre-flight ALWAYS via engine, not custom backtest

`scripts/preflight_via_engine.py` runs your strategy through the actual engine
(same `run_backtest()` the loop uses) across train/val/holdout × cost levels
and emits a pass/fail decision. **Never finalize a seed based on a custom
event-driven pre-flight alone** — they routinely diverge from engine semantics
by 10×–100× on trade count and key metrics. Use custom pre-flights for early
sanity-check (does the IC have the expected sign?), but the *gating* pre-flight
must use the engine.

## Available Bar Columns
`open`, `high`, `low`, `close`, `volume`, `buy_volume`, `vwap`, `trade_count`

## Alternative Data Sources (opt-in)

`compute_features(bars)` is OHLCV-only by default. If a hypothesis requires
non-price signals, import `MultiSourceProvider` inside `features.py` and read
the relevant series. The data is local (solana-pegasus ETL parquet); no
network calls or DB access.

```python
from pegasus.data.multi_source import MultiSourceProvider

def compute_features(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy()
    with MultiSourceProvider() as p:
        fr = p.get_funding_rate("BTCUSDT", df.index.min(), df.index.max())
    # Resample/forward-fill to bar frequency, shift(1) to avoid lookahead, etc.
    ...
```

| Source | Method | Granularity | Coverage | Keys / notes |
|---|---|---|---|---|
| Funding rate | `get_funding_rate(symbol, start, end)` | 8h | 2022-04 → present | BTCUSDT, ETHUSDT only (raises on others) |
| Open interest | `get_open_interest(symbol, start, end, interval='1h')` | 1h | **forward-only** (~30d window) | BTCUSDT, ETHUSDT only; REST-paginated |
| Chain TVL | `get_chain_tvl(chain, start, end)` | daily | 2022-04 → present | Arbitrum, Base, Ethereum, Solana |
| Protocol TVL | `get_protocol_tvl(protocol, start, end, chain='Total')` | daily | 2022 → present | aave, lido, uniswap (chain is a column filter) |
| DEX volume | `get_dex_volume(chain, start, end, protocol='Total')` | daily | 2022 → present | per-chain partition; protocol is a column filter |
| Stablecoin supply | `get_stablecoin(chain, start, end, stablecoin='peggedUSD')` | daily | 2022 → present | per-chain partition; peg type is a column filter |

**Gotchas:**
- All returned DataFrames are indexed by UTC `datetime`. Bar timestamps are
  also UTC; align with `.reindex(bars.index, method='ffill')` for daily-into-bar
  alignment, then `shift(1)` to enforce causality.
- Open interest history is bounded by Binance's REST API window. Strategies
  that need long history will fail — restrict to recent windows or skip.
- `protocol_tvl` and `dex_volume` accept `"Total"` as the chain/protocol
  filter for the aggregate; named values are also valid.

## Rules
- NO forward-looking operations (no `.shift(-N)` with N > 0)
- NO external data sources (the alt-data above is local parquet, NOT external)
- NO database access (DuckDB inside `MultiSourceProvider` reads local parquet only)
- Only use pandas and numpy for computations
- Keep strategies simple and mechanistically motivated
