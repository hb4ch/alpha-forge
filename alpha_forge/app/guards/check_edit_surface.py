"""Edit surface guard: ensures only allowed files were modified.

Violation results in an immediate red strike.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import Path

from alpha_forge.app.domain.models import GuardResult


def _hash_file(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _matches_pattern(filepath: str, patterns: list[str]) -> bool:
    """Check if a filepath matches any of the given glob patterns."""
    for pattern in patterns:
        if fnmatch.fnmatch(filepath, pattern):
            return True
    return False


def check_edit_surface(
    family_dir: Path,
    allowed_files: list[str],
    forbidden_patterns: list[str],
    baseline_hashes: dict[str, str] | None = None,
) -> GuardResult:
    """Check that only allowed files were modified in the family directory.

    Args:
        family_dir: Path to the family directory.
        allowed_files: List of relative paths that may be modified (e.g., "research/features.py").
        forbidden_patterns: Glob patterns for files that must NOT be modified.
        baseline_hashes: Previous file hashes to detect changes. If None,
                        only checks for presence of forbidden files.

    Returns:
        GuardResult with violations list.
    """
    violations: list[str] = []
    research_dir = family_dir / "research"

    if not research_dir.exists():
        return GuardResult(
            guard_name="edit_surface",
            passed=False,
            violations=["research/ directory does not exist"],
            is_red_strike=True,
        )

    # Check if any forbidden files exist or were modified
    for root_dir in [family_dir.parent.parent]:  # workspace root
        for pattern in forbidden_patterns:
            for match in root_dir.glob(pattern):
                if match.is_file() and baseline_hashes:
                    rel = str(match.relative_to(root_dir))
                    if rel in baseline_hashes:
                        current_hash = _hash_file(match)
                        if current_hash != baseline_hashes[rel]:
                            violations.append(f"Forbidden file modified: {rel}")

    # Check that only allowed files changed in the family's research dir
    if baseline_hashes:
        for filepath in research_dir.rglob("*.py"):
            rel = str(filepath.relative_to(family_dir))
            if rel not in allowed_files:
                if rel in baseline_hashes:
                    current_hash = _hash_file(filepath)
                    if current_hash != baseline_hashes[rel]:
                        violations.append(f"Unauthorized file modified: {rel}")
                else:
                    violations.append(f"Unauthorized new file: {rel}")

    is_red = len(violations) > 0
    return GuardResult(
        guard_name="edit_surface",
        passed=not violations,
        violations=violations,
        is_red_strike=is_red,
    )
