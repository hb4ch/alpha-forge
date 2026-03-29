"""Guards tab: pass/fail status for all guard checks."""
from __future__ import annotations

from rich.console import Group
from rich.text import Text
from textual.widgets import Static


class GuardsPanel(Static):
    """Guard check results with pass/fail badges."""

    def update_guards(self, results: list[dict]) -> None:
        lines: list[Text] = []
        for g in results:
            name = str(g.get("guard_name", "unknown"))
            passed = g.get("passed", False)
            violations = g.get("violations", [])
            is_red = g.get("is_red_strike", False)

            line = Text("  ")
            if passed:
                line.append("✓ PASS", style="green")
            elif is_red:
                line.append("✗ RED STRIKE", style="bold red")
            else:
                line.append("✗ FAIL", style="red")

            line.append(f"  {name}")
            lines.append(line)
            for v in violations:
                lines.append(Text(f"      {v}", style="dim"))

        if lines:
            self.update(Group(*lines))
        else:
            self.update(Text("  No guard results yet", style="dim"))
