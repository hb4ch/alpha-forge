"""Verdicts tab: collapsible judge output cards."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Static

VERDICT_STYLES = {
    "approve": "green",
    "approve_with_constraints": "yellow",
    "revise": "#f0883e",
    "reject": "red",
}

RISK_STYLES = {
    "low": "green",
    "medium": "yellow",
    "high": "red",
}


class VerdictCard(Static):
    """Single judge verdict display."""

    def set_verdict(self, output: dict) -> None:
        judge = output.get("judge_type", "unknown")
        verdict = output.get("verdict", "unknown")
        reasoning = output.get("reasoning", "")
        must_fix = output.get("must_fix", [])
        risk = output.get("risk_level", "low")

        color = VERDICT_STYLES.get(verdict, "white")
        risk_color = RISK_STYLES.get(risk, "white")

        lines = [
            f"[bold magenta]⚖ {judge.title()}[/] [{color}]{verdict.upper()}[/] [dim]risk:[/][{risk_color}]{risk}[/]",
        ]
        if reasoning:
            lines.append(f"  {reasoning[:200]}")
        if must_fix:
            lines.append(f"  [dim]must_fix: {must_fix}[/]")
        self.update("\n".join(lines))


class VerdictsPanel(VerticalScroll):
    """All judge verdicts for current iteration."""

    def update_verdicts(self, outputs: list[dict]) -> None:
        self.remove_children()
        for output in outputs:
            judge = output.get("judge_type", "unknown")
            card = VerdictCard()
            collapsible = Collapsible(card, title=f"{judge.title()} Judge")
            self.mount(collapsible)
            card.set_verdict(output)
