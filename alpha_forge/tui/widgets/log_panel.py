"""Log tab: raw Python logging output with filtering."""
from __future__ import annotations

import logging

from textual.widgets import RichLog


class TUILogHandler(logging.Handler):
    """Routes Python logging to a RichLog widget."""

    def __init__(self, log_widget: LogPanel) -> None:
        super().__init__()
        self._widget = log_widget

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._widget.write_log(msg, record.name)
        except Exception:
            self.handleError(record)


class LogPanel(RichLog):
    """Auto-scrolling log viewer with module filtering."""

    def __init__(self, **kwargs) -> None:
        super().__init__(highlight=True, auto_scroll=True, wrap=True, markup=True, **kwargs)
        self._filter_module: str | None = None

    def write_log(self, message: str, module: str = "") -> None:
        if self._filter_module and self._filter_module not in module:
            return
        self.write(message)

    def set_filter(self, module: str | None) -> None:
        self._filter_module = module

    def install_handler(self) -> TUILogHandler:
        handler = TUILogHandler(self)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
        logging.root.addHandler(handler)
        return handler
