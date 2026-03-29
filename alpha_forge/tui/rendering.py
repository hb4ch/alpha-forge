"""Helpers for safely rendering dynamic TUI content."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PRIMARY_LEVEL_FIELDS = {
    "leakage": "leakage_risk",
    "overfit": "overfit_risk",
    "realism": "realism_risk",
    "code": "code_risk",
    "result": "result_quality",
}

PRIMARY_LEVEL_LABELS = {
    "result": "quality",
}


@dataclass(slots=True)
class VerdictDisplayData:
    """Normalized verdict data for TUI widgets."""

    judge: str
    verdict: str
    reasoning: str
    must_fix: list[str]
    primary_label: str | None
    primary_level: str | None


def coerce_plain_text(value: Any) -> str:
    """Convert dynamic values to plain text for safe rendering."""
    if value is None:
        return ""
    return str(value)


def coerce_text_list(value: Any) -> list[str]:
    """Normalize list-like dynamic values to a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [coerce_plain_text(item) for item in value]
    if isinstance(value, tuple):
        return [coerce_plain_text(item) for item in value]
    text = coerce_plain_text(value)
    return [text] if text else []


def normalize_verdict_output(output: Mapping[str, Any]) -> VerdictDisplayData:
    """Map a judge payload to the fields the TUI actually renders."""
    judge = coerce_plain_text(output.get("judge_type", "unknown")) or "unknown"
    verdict = coerce_plain_text(output.get("verdict", "unknown")) or "unknown"
    reasoning = coerce_plain_text(
        output.get("reasoning_summary") or output.get("reasoning") or ""
    )
    must_fix = coerce_text_list(output.get("must_fix", []))

    primary_level: str | None = None
    primary_label: str | None = None
    primary_field = PRIMARY_LEVEL_FIELDS.get(judge)

    if primary_field:
        value = output.get(primary_field)
        if value is not None:
            primary_level = coerce_plain_text(value) or None
            primary_label = PRIMARY_LEVEL_LABELS.get(judge, "risk")

    if primary_level is None:
        value = output.get("risk_level")
        if value is not None:
            primary_level = coerce_plain_text(value) or None
            primary_label = "risk"

    return VerdictDisplayData(
        judge=judge,
        verdict=verdict,
        reasoning=reasoning,
        must_fix=must_fix,
        primary_label=primary_label,
        primary_level=primary_level,
    )
