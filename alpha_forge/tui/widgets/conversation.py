"""Streaming LLM conversation log widget."""
from __future__ import annotations

from rich.text import Text
from textual.widgets import RichLog

from alpha_forge.tui.rendering import coerce_plain_text, coerce_text_list

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
        self._streaming_buffer: str = ""
        self._streaming: bool = False

    def add_iteration_header(self, iteration_id: str) -> None:
        self.write(Text(f"── {iteration_id} " + "─" * 40, style="dim"))

    def begin_streaming(self, label: str, detail: str = "") -> None:
        """Start a streaming block — tokens accumulate until end_streaming."""
        self._streaming = True
        self._streaming_buffer = ""
        header = f"\n🔬 {coerce_plain_text(label)}"
        if detail:
            header += f"  {coerce_plain_text(detail)}"
        self.write(Text(header, style="bold cyan"))

    def add_researcher_token(self, token: str) -> None:
        """Append a streaming token — flushes complete lines as they form."""
        self._streaming_buffer += token
        # Flush complete lines for natural streaming feel
        while "\n" in self._streaming_buffer:
            line, self._streaming_buffer = self._streaming_buffer.split("\n", 1)
            if line.strip():
                self.write(Text(line))

    def end_streaming(self) -> None:
        """Flush remaining buffer and end streaming block."""
        if self._streaming_buffer.strip():
            self.write(Text(self._streaming_buffer))
        self._streaming_buffer = ""
        self._streaming = False

    def add_researcher_message(self, label: str, text: str) -> None:
        self.write(Text(f"\n🔬 {coerce_plain_text(label)}", style="bold cyan"))
        self.write(Text(coerce_plain_text(text)))

    def add_verdict(self, judge_type: str, verdict: str, reasoning: str, must_fix: list[str]) -> None:
        judge = coerce_plain_text(judge_type)
        verdict_text = coerce_plain_text(verdict)
        color = VERDICT_COLORS.get(verdict_text, "white")
        self.write(
            Text(f"\n⚖ {judge.title()} Judge ", style="bold magenta")
            + Text(verdict_text.upper(), style=f"bold {color}")
        )
        if reasoning:
            self.write(Text(coerce_plain_text(reasoning)))
        normalized_must_fix = coerce_text_list(must_fix)
        if normalized_must_fix:
            self.write(Text("  must_fix:", style="dim"))
            for item in normalized_must_fix:
                self.write(Text(f"    - {item}", style="dim"))

    def add_override_prompt(self, verdict: str, judge_type: str) -> None:
        verdict_text = coerce_plain_text(verdict)
        judge = coerce_plain_text(judge_type)
        self.write(Text(""))
        self.write(
            Text(" SEMI-AUTO ", style="bold on #f0883e")
            + Text(f" Override {judge} verdict ({verdict_text})?", style="#f0883e")
        )
        self.write(Text("  [a] Accept verdict"))
        self.write(Text("  [o] Override -> APPROVE"))
        self.write(Text("  [r] Override -> REJECT"))
        self.write(Text("  [v] Override -> REVISE with custom feedback"))
        self.write(Text("  [s] Skip to autopilot"))

    def add_system_message(self, text: str, style: str = "dim italic") -> None:
        self.write(Text(f"\n{coerce_plain_text(text)}", style=style))
