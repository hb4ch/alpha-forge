"""Guards tab: pass/fail status for all guard checks."""
from __future__ import annotations

from textual.widgets import Static


class GuardsPanel(Static):
    """Guard check results with pass/fail badges."""

    def update_guards(self, results: list[dict]) -> None:
        lines = []
        for g in results:
            name = g.get("guard_name", "unknown")
            passed = g.get("passed", False)
            violations = g.get("violations", [])
            is_red = g.get("is_red_strike", False)

            if passed:
                badge = "[green]✓ PASS[/]"
            elif is_red:
                badge = "[bold red]✗ RED STRIKE[/]"
            else:
                badge = "[red]✗ FAIL[/]"

            lines.append(f"  {badge}  {name}")
            for v in violations:
                lines.append(f"      [dim]{v}[/]")

        self.update("\n".join(lines) if lines else "  [dim]No guard results yet[/]")
