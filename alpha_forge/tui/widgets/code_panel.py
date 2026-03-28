"""Code tab: syntax-highlighted view with diff toggle."""
from __future__ import annotations

import difflib

from rich.syntax import Syntax
from rich.text import Text
from textual.widgets import RichLog


class CodePanel(RichLog):
    """Code viewer with diff toggle (press 'd')."""

    def __init__(self, **kwargs) -> None:
        super().__init__(highlight=True, wrap=True, **kwargs)
        self._current_code: dict[str, str] = {}
        self._previous_code: dict[str, str] = {}
        self._show_diff: bool = False

    def update_code(self, current: dict[str, str], previous: dict[str, str] | None = None) -> None:
        self._current_code = current
        self._previous_code = previous or {}
        self._render()

    def toggle_diff(self) -> None:
        self._show_diff = not self._show_diff
        self._render()

    def _render(self) -> None:
        self.clear()
        if self._show_diff and self._previous_code:
            self._render_diff()
        else:
            self._render_full()

    def _render_full(self) -> None:
        for filename, content in sorted(self._current_code.items()):
            self.write(Text(f"─── {filename} ───", style="bold cyan"))
            self.write(Syntax(content, "python", theme="monokai", line_numbers=True))

    def _render_diff(self) -> None:
        for filename in sorted(set(self._current_code) | set(self._previous_code)):
            old = self._previous_code.get(filename, "").splitlines(keepends=True)
            new = self._current_code.get(filename, "").splitlines(keepends=True)
            diff = difflib.unified_diff(old, new, fromfile=f"prev/{filename}", tofile=f"curr/{filename}")
            diff_text = "".join(diff)
            if diff_text:
                self.write(Text(f"─── {filename} (diff) ───", style="bold yellow"))
                self.write(Syntax(diff_text, "diff", theme="monokai"))
            else:
                self.write(Text(f"─── {filename} (unchanged) ───", style="dim"))
