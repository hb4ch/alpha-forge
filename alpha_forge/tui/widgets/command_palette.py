"""Command palette for slash commands."""
from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Input, Label, Static

logger = logging.getLogger(__name__)


class CommandSubmitted(Message):
    """Message posted when a command is submitted."""

    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command

    # Parse helpers
    @property
    def parts(self) -> list[str]:
        return self.command.split()

    @property
    def name(self) -> str:
        return self.parts[0] if self.parts else ""

    @property
    def args(self) -> list[str]:
        return self.parts[1:] if len(self.parts) > 1 else []


class CommandPalette(Static):
    """Command input bar activated by '/' key."""

    def compose(self) -> ComposeResult:
        with Horizontal(id="command-bar"):
            yield Label("/", id="command-prefix")
            yield Input(placeholder="command...", id="command-input")

    def on_mount(self) -> None:
        self.display = False

    def show(self) -> None:
        self.display = True
        self.query_one("#command-input", Input).focus()

    def hide(self) -> None:
        self.display = False
        inp = self.query_one("#command-input", Input)
        inp.value = ""

    def on_input_submitted(self, event: Input.Submitted) -> None:
        command = event.value.strip()
        self.hide()
        if command:
            self.post_message(CommandSubmitted(command))
