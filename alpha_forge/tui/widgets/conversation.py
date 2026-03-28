"""Streaming LLM conversation log widget."""
from __future__ import annotations

from rich.text import Text
from textual.widgets import RichLog

VERDICT_COLORS = {
    "approve": "green",
    "approve_with_constraints": "yellow",
    "revise": "#f0883e",
    "reject": "red",
    "fork_required": "magenta",
}


class ConversationStream(RichLog):
    """Auto-scrolling conversation log showing LLM and judge output."""

    def __init__(self, **kwargs) -> None:
        super().__init__(highlight=True, markup=True, auto_scroll=True, wrap=True, **kwargs)

    def add_iteration_header(self, iteration_id: str) -> None:
        self.write(Text(f"── {iteration_id} " + "─" * 40, style="dim"))

    def add_researcher_token(self, token: str) -> None:
        """Append a streaming token from the researcher."""
        self.write(token, shrink=False, scroll_end=True)

    def add_researcher_message(self, label: str, text: str) -> None:
        self.write(Text(f"\n🔬 {label}", style="bold cyan"))
        self.write(text)

    def add_verdict(self, judge_type: str, verdict: str, reasoning: str, must_fix: list[str]) -> None:
        color = VERDICT_COLORS.get(verdict, "white")
        self.write(
            Text(f"\n⚖ {judge_type.title()} Judge ", style="bold magenta")
            + Text(verdict.upper(), style=f"bold {color}")
        )
        if reasoning:
            self.write(reasoning)
        if must_fix:
            self.write(Text("  must_fix: " + str(must_fix), style="dim"))

    def add_override_prompt(self, verdict: str, judge_type: str) -> None:
        self.write("")
        self.write(
            Text(" SEMI-AUTO ", style="bold on #f0883e")
            + Text(f" Override {judge_type} verdict ({verdict})?", style="#f0883e")
        )
        self.write("  [a] Accept verdict")
        self.write("  [o] Override → APPROVE")
        self.write("  [r] Override → REJECT")
        self.write("  [v] Override → REVISE with custom feedback")
        self.write("  [s] Skip to autopilot")

    def add_system_message(self, text: str) -> None:
        self.write(Text(f"\n{text}", style="dim italic"))
