"""Researcher agent: generates plans and research code via Claude API.

Produces the 4 research/ files (features.py, labels.py, model_config.py,
signal_combiner.py) based on family context and prior feedback.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alpha_forge.app.agents.llm_client import LLMClient
from alpha_forge.app.domain.models import IdeaFamily, SeedCard

logger = logging.getLogger(__name__)


def _format_code_bundle(code_files: dict[str, str] | None) -> str:
    """Render existing code for prompt context."""
    if not code_files:
        return "No existing research code."

    parts: list[str] = []
    for filename in sorted(code_files):
        parts.append(f"# === {filename} ===\n{code_files[filename]}")
    return "\n\n".join(parts)


class ResearcherAgent:
    """Agent that drafts plans and writes research code."""

    def __init__(self, client: LLMClient | None = None) -> None:
        if client:
            self.client = client
        else:
            from alpha_forge.app.agents.llm_config import get_client_for_role

            self.client = get_client_for_role("researcher")

    def draft_plan(
        self,
        family: IdeaFamily,
        seed: SeedCard,
        prior_feedback: list[str] | None = None,
        stream_callback=None,
    ) -> str:
        """Draft an iteration plan for the family."""
        feedback_str = "\n".join(prior_feedback) if prior_feedback else "No prior feedback."

        system = """You are a crypto alpha researcher. Draft a concrete implementation plan
for the research hypothesis. The plan should specify:
1. What features to compute from OHLCV bars
2. What signal logic to use
3. What configuration parameters to set
4. Expected behavior and falsification criteria

Keep the plan specific and implementable. Do not be vague.

## Critical requirements (judges will reject plans that violate these)

### Data integrity (Leakage Judge)
- All rolling/expanding statistics must use ONLY past data (no future leakage)
- Explicitly state the lookback window for every rolling computation
- No normalization fit on full sample — must be rolling or expanding with lookback only
- No label information may flow into features

### Signal direction discipline
- If the seed specifies signal directions (e.g. "high RSI → long", "positive z-score → long"),
  you MUST follow those directions exactly. Do NOT invert signals based on textbook conventions.
  Crypto markets often have inverted behavior compared to equities (e.g. RSI acts as momentum, not mean-reversion).
- If the seed specifies .shift(1) on features, preserve that shift. It is part of the signal design.
- If the seed specifies the trading universe (e.g. "BTC, ETH, SOL only"), add a "universe" key
  to MODEL_CONFIG listing exactly those symbols.

### Mechanism preservation
- Vol targeting, regime-adaptive weights, and position sizing rules specified in the seed
  are STRUCTURAL parts of the mechanism, not optional complexity to remove.
- If the seed specifies vol targeting (e.g. "scale by target_vol / realized_vol"), you MUST
  implement it. It controls risk across volatility regimes and is essential to the strategy.
- If the seed specifies regime-adaptive weights, implement them — do not simplify to equal weights.
- Only simplify components that the seed does NOT specify.

### Overfit discipline (Overfit Judge)
- Keep degrees of freedom minimal — prefer 1-2 parameters over many
- Do not stack multiple weak signals with tuned weights
- The mechanism must be preserved from the seed hypothesis — do not invent new mechanisms

### Market realism (Realism Judge)
- Account for realistic execution: fees, slippage, and fill assumptions
- Avoid strategies that only work with zero-cost or instantaneous fills
- Position sizing must be fractional (conviction-weighted), not binary all-in (±1.0)
- Include stop-loss in MODEL_CONFIG for downside protection

### Code quality (Code Judge)
- Keep logic simple and auditable — no unnecessary abstractions
- signal_combiner.py signals must be fractional (-1.0 to 1.0), not just binary ±1
- MODEL_CONFIG must include "timeframe" matching the seed horizon
- MODEL_CONFIG should include "stop_loss_pct" at minimum"""

        user_prompt = f"""## Family
- ID: {family.family_id}
- Hypothesis: {family.base_hypothesis}
- Mechanism: {family.mechanism}
- Iteration: {family.current_iteration}
- Best score so far: {family.best_score:.3f}

## Seed
- Claim: {seed.raw_claim}
- Horizon: {seed.horizon}
- Market: {seed.market}

## Prior Feedback
{feedback_str}

## Constraints
- You may only modify: features.py, labels.py, model_config.py, signal_combiner.py
- Available bar columns: open, high, low, close, volume, buy_volume, vwap, trade_count
- Signals: values between -1.0 and 1.0 (fractional sizing allowed, e.g. 0.5 = 50% long exposure)
  - 1.0 = fully long, -1.0 = fully short, 0.0 = flat, NaN = warmup
  - Use fractional signals for conviction-weighted position sizing (e.g. Kelly fraction, z-score scaled)
- No forward-looking operations — .shift(-N) is FORBIDDEN and will fail the time integrity guard
- For forward returns in labels.py, use numpy array slicing: `ret[:n-k] = (close[k:] - close[:n-k]) / close[:n-k]`
- If specifying a universe in MODEL_CONFIG, use exact trading pair names: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT
  Do NOT use bare asset names like "BTC" or "ETH" — these are Binance USDT trading pairs.

## Risk Management (engine-level, set via MODEL_CONFIG)
The backtest engine supports automatic risk management. Set these optional keys in MODEL_CONFIG:
- "timeframe": str — REQUIRED, must match the seed horizon (e.g. "1h")
- "stop_loss_pct": float — exit when loss exceeds this % from entry (e.g. 0.02 = 2% SL)
- "take_profit_pct": float — exit when gain exceeds this % from entry (e.g. 0.05 = 5% TP)
- "trailing_stop_pct": float — exit when price retraces this % from peak since entry (e.g. 0.03 = 3%)
These are enforced by the engine using intra-bar high/low checks. The strategy code does NOT need to implement stop logic.

Draft the implementation plan.
"""
        return self.client.call(system, user_prompt, stream_callback=stream_callback)

    def choose_iteration_mode(
        self,
        feedback: list[str],
        family: IdeaFamily,
        stream_callback=None,
    ) -> dict:
        """Ask the LLM to decide how to proceed after judge feedback.

        Returns {"mode": "replan"|"revise_code"|"adjust_config", "reasoning": "..."}.
        """
        feedback_str = "\n".join(feedback) if feedback else "No feedback."

        system = """You are deciding how to proceed after reviewing judge feedback on a crypto alpha strategy.

Choose one of:
- "replan": The strategy needs a fundamentally different approach. The current plan/mechanism is flawed.
- "revise_code": The approach is sound but the implementation needs changes. Fix specific code issues.
- "adjust_config": Only model_config.py parameters need tweaking (e.g. thresholds, stop-loss, timeframe).

Return JSON: {"mode": "replan" or "revise_code" or "adjust_config", "reasoning": "one sentence why"}"""

        user_prompt = f"""## Family
- Hypothesis: {family.base_hypothesis}
- Mechanism: {family.mechanism}
- Iteration: {family.current_iteration}
- Best score so far: {family.best_score:.3f}

## Judge Feedback
{feedback_str}

Choose the iteration mode."""

        result = self.client.call_json(system, user_prompt, stream_callback=stream_callback)
        mode = result.get("mode", "replan")
        if mode not in ("replan", "revise_code", "adjust_config"):
            logger.warning("Invalid iteration mode '%s', defaulting to replan", mode)
            mode = "replan"
        return {"mode": mode, "reasoning": result.get("reasoning", "")}

    def write_code(
        self,
        family: IdeaFamily,
        plan: str,
        prior_feedback: list[str] | None = None,
        existing_code: dict[str, str] | None = None,
        mode: str = "replan",
        stream_callback=None,
    ) -> dict[str, str]:
        """Generate the 4 research files based on the plan.

        Args:
            mode: "replan" (full generation), "revise_code" (fix specific issues),
                  or "adjust_config" (only model_config.py changes).

        Returns a dict mapping filename -> code content.
        """
        feedback_str = "\n".join(prior_feedback) if prior_feedback else "No prior feedback."
        existing_code_str = _format_code_bundle(existing_code)

        if mode == "adjust_config" and existing_code:
            revision_guidance = (
                "ONLY modify model_config.py parameters. Return ALL 4 files but keep"
                " features.py, labels.py, and signal_combiner.py EXACTLY as they are."
                " Focus on tuning configuration: thresholds, stop-loss, timeframe, etc."
            )
        elif mode == "revise_code" and existing_code:
            revision_guidance = (
                "Fix the specific issues identified in the judge feedback below."
                " Address at most 3 issues — do NOT attempt to fix everything at once."
                " Preserve the overall approach and plan — only change what's broken."
                " Return all 4 files with targeted fixes."
            )
        elif existing_code:
            revision_guidance = (
                "Revise the existing implementation in place. Preserve the current file contracts and"
                " update only the allowed research files."
            )
        else:
            revision_guidance = "Generate the initial implementation for the allowed research files."

        system = """You are a crypto alpha researcher implementing a trading strategy.
Generate Python code for the 4 research files. You MUST respond with valid JSON
where keys are filenames and values are the complete Python file contents.

Response format:
{
  "features.py": "...",
  "model_config.py": "...",
  "signal_combiner.py": "...",
  "labels.py": "..."
}

Rules:
- features.py must export: compute_features(bars: pd.DataFrame) -> pd.DataFrame
- model_config.py must export: MODEL_CONFIG: dict
- signal_combiner.py must export: combine_signals(features: pd.DataFrame, config: dict) -> pd.Series
- labels.py must export: compute_labels(bars: pd.DataFrame) -> pd.Series
- Signals: values between -1.0 and 1.0. Fractional sizing is supported (e.g. 0.3 = 30% long exposure)
  - Use conviction-weighted sizing (e.g. scale by z-score magnitude or Kelly fraction) instead of binary all-in
- Available columns: open, high, low, close, volume, buy_volume, vwap, trade_count
- NO forward-looking operations — .shift(-N) is FORBIDDEN and will fail the time integrity guard
- For labels.py (forward returns): use numpy array slicing instead of shift(-N).
  Example: `ret = np.full(n, np.nan); ret[:n-k] = (close[k:] - close[:n-k]) / close[:n-k]`
  This computes forward returns without using shift(-N).
- NO external data sources
- Use only: pandas, numpy (already available)

## Critical requirements (judges will reject code that violates these)

### Signal direction discipline
- If the seed or plan specifies signal directions (e.g. "high RSI → positive signal"),
  implement those directions EXACTLY. Do NOT invert based on textbook conventions.
  In crypto, RSI and z-score often act as MOMENTUM indicators (high → continue up),
  not mean-reversion indicators.
- If features are specified with .shift(1), KEEP the .shift(1). It is intentional.
- Use sqrt(252) for annualizing daily volatility, NOT sqrt(365).

### Mechanism preservation
- If the plan/seed specifies vol targeting, regime-adaptive weights, or z-score normalization
  in the signal combiner, you MUST implement them. These are structural mechanism components.
- Do NOT simplify regime-adaptive weights to equal weights unless the plan explicitly says to.
- Do NOT remove vol targeting unless the plan explicitly says to.
- If the plan specifies a trading universe, add "universe": [...] to MODEL_CONFIG.
  Use exact Binance pair names: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT. NOT bare "BTC", "ETH", etc.

### Data integrity (Leakage Judge)
- All rolling/expanding window operations must use ONLY past data
- No .shift(-N) with N > 0 (forward-looking)
- Rolling stats must use explicit lookback windows, not full-sample fits

### Code quality (Code Judge)
- Logic must be simple and auditable — no unnecessary abstractions
- Signals must be fractional (-1.0 to 1.0), not binary ±1.0 only
- No hidden mutable state, globals, or caches

### Market realism (Realism Judge checked at results phase)
- Fractional position sizing is critical — binary all-in is rejected
- Strategies need stop-loss for downside protection

MODEL_CONFIG required/optional keys:
- "timeframe": str — REQUIRED, must match the seed horizon (e.g. "1h"). DO NOT omit this.
- "stop_loss_pct": float — optional, engine-level stop-loss (e.g. 0.02 = 2%)
- "take_profit_pct": float — optional, engine-level take-profit (e.g. 0.05 = 5%)
- "trailing_stop_pct": float — optional, engine-level trailing stop (e.g. 0.03 = 3%)
These risk params are handled by the engine automatically — do NOT implement stop logic in signal_combiner.py.
"""

        user_prompt = f"""## Family
- Hypothesis: {family.base_hypothesis}
- Mechanism: {family.mechanism}
- Iteration: {family.current_iteration}

## Plan
{plan}

## Prior Feedback
{feedback_str}

## Allowed Files
- features.py
- labels.py
- model_config.py
- signal_combiner.py

## Existing Research Files
```python
{existing_code_str}
```

## Revision Mode
{revision_guidance}

Generate the 4 research files as JSON.
"""

        result = self.client.call_json(system, user_prompt, stream_callback=stream_callback)

        # Validate expected keys
        expected = {"features.py", "model_config.py", "signal_combiner.py", "labels.py"}
        missing = expected - set(result.keys())
        if missing:
            raise ValueError(f"Missing files in researcher output: {missing}")

        return result

    def apply_code(
        self,
        family_id: str,
        code_files: dict[str, str],
        research_dir: Path,
    ) -> list[str]:
        """Write generated code to the family's research directory.

        Returns list of files written.  Always overwrites — the
        checkpoint/rollback mechanism in family_flow handles safety.
        """
        research_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []

        for filename, content in code_files.items():
            filepath = research_dir / filename
            filepath.write_text(content)
            written.append(str(filepath))
            logger.info("Wrote %s", filepath)

        return written
