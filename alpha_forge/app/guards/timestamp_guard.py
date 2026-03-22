"""Time integrity guard: AST inspection for forward-looking patterns.

Checks research/ code for patterns that could leak future data.
Best-effort heuristic, not a proof.
"""

from __future__ import annotations

import ast
from pathlib import Path

from alpha_forge.app.domain.models import GuardResult


class _FutureLookDetector(ast.NodeVisitor):
    """AST visitor that detects potential forward-looking data access."""

    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        # Detect .shift(-N) where N > 0 (forward shift)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "shift":
            if node.args:
                arg = node.args[0]
                if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub):
                    if isinstance(arg.operand, ast.Constant) and isinstance(arg.operand.value, (int, float)):
                        if arg.operand.value > 0:
                            self.violations.append(
                                f"Line {node.lineno}: shift(-{arg.operand.value}) looks forward in time"
                            )
                elif isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                    if arg.value < 0:
                        self.violations.append(
                            f"Line {node.lineno}: shift({arg.value}) looks forward in time"
                        )

        # Detect .iloc with negative indexing that could access future bars
        if isinstance(node.func, ast.Attribute) and node.func.attr == "iloc":
            pass  # Too many false positives; skip for now

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # Detect future-looking slice patterns
        if isinstance(node.slice, ast.Slice):
            if node.slice.step and isinstance(node.slice.step, ast.UnaryOp):
                if isinstance(node.slice.step.op, ast.USub):
                    pass  # Reverse slicing is usually fine
        self.generic_visit(node)


def check_time_integrity(family_dir: Path) -> GuardResult:
    """Inspect research/ code for forward-looking patterns.

    Checks:
    - .shift(-N) calls (forward shift)
    - Other time-leakage patterns
    """
    research_dir = family_dir / "research"
    violations: list[str] = []

    if not research_dir.exists():
        return GuardResult(
            guard_name="time_integrity",
            passed=False,
            violations=["research/ directory does not exist"],
        )

    for py_file in research_dir.glob("*.py"):
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
            detector = _FutureLookDetector()
            detector.visit(tree)
            for v in detector.violations:
                violations.append(f"{py_file.name}: {v}")
        except SyntaxError as e:
            violations.append(f"{py_file.name}: syntax error at line {e.lineno}")

    return GuardResult(
        guard_name="time_integrity",
        passed=not violations,
        violations=violations,
        is_red_strike=len(violations) > 0,
    )
