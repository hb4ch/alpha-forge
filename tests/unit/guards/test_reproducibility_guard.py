"""Tests for the reproducibility guard."""

from __future__ import annotations

import platform
from pathlib import Path
from unittest.mock import patch

from alpha_forge.app.guards.reproducibility_guard import check_reproducibility


class TestReproducibilityGuard:
    """Tests for check_reproducibility."""

    def test_passes_when_prior_metadata_is_none(self, tmp_path: Path) -> None:
        """Passes on first run when prior_metadata is None."""
        result = check_reproducibility(tmp_path, prior_metadata=None)

        assert result.passed is True
        assert result.violations == []

    def test_passes_when_python_version_and_git_hash_match(self, tmp_path: Path) -> None:
        """Passes when python_version and git_hash match current values."""
        with patch(
            "alpha_forge.app.guards.reproducibility_guard._get_git_hash",
            return_value="abc123",
        ):
            prior = {
                "python_version": platform.python_version(),
                "git_hash": "abc123",
            }
            result = check_reproducibility(tmp_path, prior_metadata=prior)

        assert result.passed is True
        assert result.violations == []

    def test_fails_when_python_version_differs(self, tmp_path: Path) -> None:
        """Fails when python_version differs from current."""
        with patch(
            "alpha_forge.app.guards.reproducibility_guard._get_git_hash",
            return_value="abc123",
        ):
            prior = {
                "python_version": "2.7.0",  # definitely not current
                "git_hash": "abc123",
            }
            result = check_reproducibility(tmp_path, prior_metadata=prior)

        assert result.passed is False
        assert any("python_version" in v for v in result.violations)

    def test_fails_when_git_hash_differs(self, tmp_path: Path) -> None:
        """Fails when git_hash differs from current."""
        with patch(
            "alpha_forge.app.guards.reproducibility_guard._get_git_hash",
            return_value="current_hash_abc",
        ):
            prior = {
                "python_version": platform.python_version(),
                "git_hash": "old_hash_xyz",
            }
            result = check_reproducibility(tmp_path, prior_metadata=prior)

        assert result.passed is False
        assert any("git_hash" in v for v in result.violations)
