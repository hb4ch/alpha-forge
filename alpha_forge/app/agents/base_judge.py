"""Base class for all LLM judges."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from alpha_forge.app.agents.llm_client import LLMClient

logger = logging.getLogger(__name__)

# Default location for judge prompt templates
PROMPTS_DIR = Path(__file__).resolve().parents[3] / "judges" / "prompts"


class BaseJudge:
    """Base class for LLM-based judges.

    Subclasses set `judge_type` and `prompt_file`, and can override
    `build_user_prompt` to customize what the LLM sees.
    """

    judge_type: str = "base"
    prompt_file: str = ""

    def __init__(self, client: LLMClient | None = None, role: str | None = None) -> None:
        if client:
            self.client = client
        else:
            from alpha_forge.app.agents.llm_config import get_client_for_role

            role_name = role
            if role_name is None:
                if self.judge_type and self.judge_type != "base":
                    role_name = f"{self.judge_type}_judge"
                else:
                    role_name = "result_judge"
            self.client = get_client_for_role(role_name)

    def _load_system_prompt(self) -> str:
        """Load the system prompt template from disk."""
        path = PROMPTS_DIR / self.prompt_file
        if not path.exists():
            raise FileNotFoundError(f"Judge prompt not found: {path}")
        return path.read_text()

    def build_user_prompt(self, **kwargs: Any) -> str:
        """Build the user prompt from kwargs. Override in subclasses."""
        raise NotImplementedError

    def evaluate_raw(self, **kwargs: Any) -> dict[str, Any]:
        """Run the judge and return raw parsed JSON."""
        system = self._load_system_prompt()
        user_prompt = self.build_user_prompt(**kwargs)
        return self.client.call_json(system, user_prompt)

    def evaluate(self, output_model: type[BaseModel], **kwargs: Any) -> BaseModel:
        """Run the judge and return a validated Pydantic model."""
        raw = self.evaluate_raw(**kwargs)
        raw.setdefault("judge_type", self.judge_type)
        return output_model.model_validate(raw)
