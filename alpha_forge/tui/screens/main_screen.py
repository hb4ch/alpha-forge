"""Main screen: IDE-style layout composing all panels."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static, TabbedContent, TabPane

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
    """Bottom status bar showing mode and keybindings."""

    def __init__(self, **kwargs) -> None:
        super().__init__(
            "[bold white on blue] AUTOPILOT [/] [dim]Shift+Tab: Toggle mode │ Tab: Focus │ 1-5: Tabs │ /: Command │ q: Quit[/]",
            **kwargs,
        )
        self._mode = "autopilot"

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._refresh_content()

    def _refresh_content(self) -> None:
        if self._mode == "autopilot":
            badge = "[bold white on blue] AUTOPILOT [/]"
        else:
            badge = "[bold black on #f0883e] SEMI-AUTO [/]"
        self.update(
            f"{badge} [dim]Shift+Tab: Toggle mode │ Tab: Focus │ 1-5: Tabs │ /: Command │ q: Quit[/]"
        )
