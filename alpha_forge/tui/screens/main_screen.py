"""Main screen: IDE-style layout composing all panels."""
from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static, TabbedContent, TabPane

from alpha_forge.tui.widgets.code_panel import CodePanel
from alpha_forge.tui.widgets.command_palette import CommandPalette
from alpha_forge.tui.widgets.conversation import ConversationStream
from alpha_forge.tui.widgets.guards_panel import GuardsPanel
from alpha_forge.tui.widgets.log_panel import LogPanel
from alpha_forge.tui.widgets.metrics_panel import MetricsPanel
from alpha_forge.tui.widgets.state_sidebar import StateSidebar
from alpha_forge.tui.widgets.verdicts_panel import VerdictsPanel


class MainScreen(Vertical):
    """IDE-style layout: sidebar left, conversation top-right, tabs bottom-right."""

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield StateSidebar(id="sidebar")
            with Vertical(id="main-area"):
                yield ConversationStream(id="conversation")
                with TabbedContent(id="bottom-tabs"):
                    with TabPane("Metrics", id="tab-metrics"):
                        yield MetricsPanel(id="metrics-panel")
                    with TabPane("Code", id="tab-code"):
                        yield CodePanel(id="code-panel")
                    with TabPane("Verdicts", id="tab-verdicts"):
                        yield VerdictsPanel(id="verdicts-panel")
                    with TabPane("Guards", id="tab-guards"):
                        yield GuardsPanel(id="guards-panel")
                    with TabPane("Log", id="tab-log"):
                        yield LogPanel(id="log-panel")
        yield CommandPalette(id="command-palette")
        yield StatusBar(id="status-bar")


class StatusBar(Static):
    """Bottom status bar showing mode, family, iteration, elapsed time, and keybindings."""

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._mode = "autopilot"
        self._family_id: str = ""
        self._iteration: int = 0
        self._max_iterations: int = 0
        self._start_time: float = time.monotonic()
        self._refresh_content()

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        self._refresh_content()

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._refresh_content()

    def set_family(self, family_id: str) -> None:
        self._family_id = family_id
        self._refresh_content()

    def set_iteration(self, iteration: int, max_iterations: int = 0) -> None:
        self._iteration = iteration
        if max_iterations:
            self._max_iterations = max_iterations
        self._refresh_content()

    def _refresh_content(self) -> None:
        if self._mode == "autopilot":
            badge = "[bold white on #58a6ff] AUTOPILOT [/]"
        else:
            badge = "[bold black on #f0883e] SEMI-AUTO [/]"

        elapsed = int(time.monotonic() - self._start_time)
        mins, secs = divmod(elapsed, 60)
        time_str = f"{mins:02d}:{secs:02d}"

        family_str = self._family_id or "---"
        if self._max_iterations:
            iter_str = f"Iter {self._iteration}/{self._max_iterations}"
        elif self._iteration:
            iter_str = f"Iter {self._iteration}"
        else:
            iter_str = "Iter --"

        parts = [
            badge,
            f"[dim]\u2502[/] {family_str}",
            f"[dim]\u2502[/] {iter_str}",
            f"[dim]\u2502[/] {time_str}",
            f"[dim]\u2502 Shift+Tab \u2502 / \u2502 q[/]",
        ]
        self.update(" ".join(parts))
