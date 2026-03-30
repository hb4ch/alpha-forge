"""Metrics tab: per-symbol backtest results table."""
from __future__ import annotations

from textual.widgets import DataTable, Static
from textual.containers import Vertical
from textual.app import ComposeResult


class MetricsPanel(Vertical):
    """Backtest metrics table + composite score."""

    def compose(self) -> ComposeResult:
        yield DataTable(id="metrics-table", cursor_type="row")
        yield Static("", id="composite-score")

    def on_mount(self) -> None:
        table = self.query_one("#metrics-table", DataTable)
        table.add_columns("Symbol", "Sharpe", "Sortino", "MaxDD", "Return", "Trades")

    def update_metrics(self, results: list[dict]) -> None:
        table = self.query_one("#metrics-table", DataTable)
        table.clear()
        if not results:
            return
        for r in results:
            table.add_row(
                r.get("symbol", ""),
                f"{r.get('sharpe', 0):.2f}",
                f"{r.get('sortino', 0):.2f}",
                f"{r.get('max_drawdown', 0):.0%}",
                f"{r.get('total_return', 0):.0%}",
                str(r.get("trade_count", 0)),
            )

    def update_composite(self, score: float, robustness_pass: str) -> None:
        self.query_one("#composite-score", Static).update(
            f"  Composite: {score:.2f}  Robustness: {robustness_pass}"
        )
