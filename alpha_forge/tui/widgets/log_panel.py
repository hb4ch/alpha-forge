"""Log tab: raw Python logging output with filtering."""
from __future__ import annotations

import logging

from rich.text import Text
from textual.message import Message
from textual.widgets import RichLog


class LogPanel(RichLog):
    """Auto-scrolling log viewer with module filtering."""

    class LogMessage(Message):
        """Thread-safe log message delivery."""

        def __init__(self, text: str, module: str) -> None:
            super().__init__()
            self.text = text
            self.module = module

    def __init__(self, **kwargs) -> None:
        super().__init__(highlight=True, auto_scroll=True, wrap=True, markup=True, **kwargs)
        self._filter_module: str | None = None

    def on_log_panel_log_message(self, event: LogMessage) -> None:
        """Handle log messages posted from any thread."""
        if self._filter_module and self._filter_module not in event.module:
            return
        self.write(Text(event.text))

    def write_log(self, message: str, module: str = "") -> None:
        """Thread-safe: posts a message instead of writing directly."""
        try:
            self.post_message(self.LogMessage(message, module))
        except Exception:
            pass  # Widget not mounted yet or app shutting down

    def set_filter(self, module: str | None) -> None:
        self._filter_module = module

    def install_handler(self) -> "TUILogHandler":
        handler = TUILogHandler(self)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
        handler.setLevel(logging.INFO)
        logging.root.addHandler(handler)
        return handler


class TUILogHandler(logging.Handler):
    """Routes Python logging to a LogPanel widget via thread-safe post_message."""

    def __init__(self, log_widget: LogPanel) -> None:
        super().__init__()
        self._widget = log_widget

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._widget.write_log(msg, record.name)
        except Exception:
            self.handleError(record)
