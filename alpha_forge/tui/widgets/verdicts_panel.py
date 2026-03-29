"""Verdicts tab: collapsible judge output cards."""
from __future__ import annotations

from rich.console import Group
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Static

from alpha_forge.tui.rendering import normalize_verdict_output

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
        display = normalize_verdict_output(output)
        color = VERDICT_STYLES.get(display.verdict, "white")

        header = Text()
        header.append("⚖ ", style="bold magenta")
        header.append(display.judge.title(), style="bold magenta")
        header.append(" ")
        header.append(display.verdict.upper(), style=f"bold {color}")

        if display.primary_level and display.primary_label:
            risk_color = RISK_STYLES.get(display.primary_level, "white")
            header.append(" ")
            header.append(f"{display.primary_label}:", style="dim")
            header.append(display.primary_level, style=risk_color)

        lines: list[Text] = [header]
        if display.reasoning:
            lines.append(Text(f"  {display.reasoning[:200]}"))
        if display.must_fix:
            lines.append(Text("  must_fix:", style="dim"))
            lines.extend(Text(f"    - {item}", style="dim") for item in display.must_fix)
        self.update(Group(*lines))


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
