"""Researcher agent: generates plans and research code via Claude API.

Produces the 4 research/ files (features.py, labels.py, model_config.py,
signal_combiner.py) based on family context and prior feedback.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from alpha_forge.app.agents.llm_client import LLMClient
from alpha_forge.app.domain.models import IdeaFamily, SeedCard

logger = logging.getLogger(__name__)

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
    ) -> str:
        """Draft an iteration plan for the family."""
        feedback_str = "\n".join(prior_feedback) if prior_feedback else "No prior feedback."

        system = """You are a crypto alpha researcher. Draft a concrete implementation plan
for the research hypothesis. The plan should specify:
1. What features to compute from OHLCV bars
2. What signal logic to use
3. What configuration parameters to set
4. Expected behavior and falsification criteria

Keep the plan specific and implementable. Do not be vague."""

        user_prompt = f"""## Family
- ID: {family.family_id}
- Hypothesis: {family.base_hypothesis}
- Mechanism: {family.mechanism}
- Iteration: {family.current_iteration}
- Strike Count: {family.strike_count}

## Seed
- Claim: {seed.raw_claim}
- Horizon: {seed.horizon}
- Market: {seed.market}

## Prior Feedback
{feedback_str}

## Constraints
- You may only modify: features.py, labels.py, model_config.py, signal_combiner.py
- Available bar columns: open, high, low, close, volume, buy_volume, vwap, trade_count
- Signals must be: 1.0 (long), -1.0 (short), 0.0 (flat), NaN (warmup)
- No forward-looking operations (no shift(-N), no future data)

Draft the implementation plan.
"""
        return self.client.call(system, user_prompt)

    def write_code(
        self,
        family: IdeaFamily,
        plan: str,
        prior_feedback: list[str] | None = None,
    ) -> dict[str, str]:
        """Generate the 4 research files based on the plan.

        Returns a dict mapping filename -> code content.
        """
        feedback_str = "\n".join(prior_feedback) if prior_feedback else "No prior feedback."

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
- Signals: 1.0 = long, -1.0 = short, 0.0 = flat, NaN = warmup
- Available columns: open, high, low, close, volume, buy_volume, vwap, trade_count
- NO forward-looking operations (shift must use positive values only)
- NO external data sources
- Use only: pandas, numpy (already available)
"""

        user_prompt = f"""## Family
- Hypothesis: {family.base_hypothesis}
- Mechanism: {family.mechanism}
- Iteration: {family.current_iteration}

## Plan
{plan}

## Prior Feedback
{feedback_str}

Generate the 4 research files as JSON.
"""

        result = self.client.call_json(system, user_prompt)

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

        Returns list of files written.
        """
        research_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []

        for filename, content in code_files.items():
            filepath = research_dir / filename
            filepath.write_text(content)
            written.append(str(filepath))
            logger.info("Wrote %s", filepath)

        return written
