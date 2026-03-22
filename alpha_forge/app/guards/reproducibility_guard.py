"""Reproducibility guard: logs and checks environment metadata."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from pathlib import Path

from alpha_forge.app.domain.models import GuardResult


def _get_git_hash(repo_dir: Path) -> str:
    """Get the current git commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except FileNotFoundError:
        return "git-not-found"


def check_reproducibility(
    workspace_root: Path,
    prior_metadata: dict[str, str] | None = None,
) -> GuardResult:
    """Log reproducibility metadata and compare against prior run."""
    metadata = {
        "python_version": platform.python_version(),
        "git_hash": _get_git_hash(workspace_root.parent if workspace_root.name == "alpha_research" else workspace_root),
        "platform": platform.platform(),
    }

    violations: list[str] = []

    if prior_metadata:
        for key in ["python_version", "git_hash"]:
            if key in prior_metadata and prior_metadata[key] != metadata[key]:
                violations.append(
                    f"Environment drift: {key} changed from {prior_metadata[key]} to {metadata[key]}"
                )

    return GuardResult(
        guard_name="reproducibility",
        passed=not violations,
        violations=violations,
        is_red_strike=False,
    )
