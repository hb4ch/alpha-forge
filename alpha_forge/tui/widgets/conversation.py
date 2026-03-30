"""Streaming LLM conversation log widget."""
from __future__ import annotations

from rich.markdown import Markdown
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

VERDICT_BARS = {
    "approve": "\u258e",
    "approve_with_constraints": "\u258e",
    "revise": "\u258e",
    "reject": "\u258e",
    "fork_required": "\u258e",
}


class ConversationStream(RichLog):
    """Auto-scrolling conversation log showing LLM and judge output."""

    def __init__(self, **kwargs) -> None:
        super().__init__(highlight=True, markup=True, auto_scroll=True, wrap=True, **kwargs)
        self._streaming_buffer: str = ""
        self._full_stream: str = ""
        self._streaming: bool = False
        self._stream_lines_written: int = 0

    def add_iteration_header(self, iteration_id: str) -> None:
        rule = f"\u2501\u2501\u2501 {iteration_id} " + "\u2501" * 40
        self.write(Text(rule, style="bold dim"))

    def begin_streaming(self, label: str, detail: str = "") -> None:
        """Start a streaming block -- tokens accumulate until end_streaming."""
        self._streaming = True
        self._streaming_buffer = ""
        self._full_stream = ""
        self._stream_lines_written = 0
        header = f"\n\U0001f52c {coerce_plain_text(label)}"
        if detail:
            header += f"  {coerce_plain_text(detail)}"
        self.write(Text(header, style="bold cyan on #161b22"))

    def add_researcher_token(self, token: str) -> None:
        """Append a streaming token -- flushes complete lines as they form."""
        self._streaming_buffer += token
        self._full_stream += token
        # Flush complete lines for natural streaming feel
        while "\n" in self._streaming_buffer:
            line, self._streaming_buffer = self._streaming_buffer.split("\n", 1)
            if line.strip():
                self.write(Text(line))
                self._stream_lines_written += 1

    def end_streaming(self) -> None:
        """Replace streamed plain-text lines with a single Markdown render."""
        full_text = (self._full_stream + self._streaming_buffer).strip()

        # Remove the plain-text lines that were written incrementally
        if self._stream_lines_written > 0 and len(self.lines) >= self._stream_lines_written:
            del self.lines[-self._stream_lines_written:]
            self._line_cache.clear()

        # Render the full response as Markdown
        if full_text:
            try:
                self.write(Markdown(full_text))
            except Exception:
                self.write(Text(full_text))

        self._streaming_buffer = ""
        self._full_stream = ""
        self._streaming = False
        self._stream_lines_written = 0

    def add_researcher_message(self, label: str, text: str) -> None:
        self.write(Text(f"\n\U0001f52c {coerce_plain_text(label)}", style="bold cyan"))
        self.write(Text(coerce_plain_text(text)))

    def add_verdict(self, judge_type: str, verdict: str, reasoning: str, must_fix: list[str]) -> None:
        judge = coerce_plain_text(judge_type)
        verdict_text = coerce_plain_text(verdict)
        color = VERDICT_COLORS.get(verdict_text, "white")
        bar = VERDICT_BARS.get(verdict_text, "\u258e")

        header = Text()
        header.append(f"{bar} ", style=f"bold {color}")
        header.append(f"\u2696 {judge.title()} Judge ", style="bold magenta")
        header.append(verdict_text.upper(), style=f"bold {color}")

        self.write(Text(""))
        self.write(header)
        if reasoning:
            self.write(Text(f"  {bar} {coerce_plain_text(reasoning)}", style="dim"))
        normalized_must_fix = coerce_text_list(must_fix)
        if normalized_must_fix:
            self.write(Text(f"  {bar} must_fix:", style="dim bold"))
            for item in normalized_must_fix:
                self.write(Text(f"  {bar}   - {item}", style="dim"))

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
        self.write(Text(f"\n\u203a {coerce_plain_text(text)}", style=style))
