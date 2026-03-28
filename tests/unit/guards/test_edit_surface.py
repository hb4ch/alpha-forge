"""Tests for the edit surface guard."""

from __future__ import annotations

import hashlib
from pathlib import Path

from alpha_forge.app.guards.check_edit_surface import check_edit_surface


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_file(path: Path, content: str = "# placeholder\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestEditSurface:
    """Tests for check_edit_surface."""

    def test_passes_when_research_exists_and_no_baseline(self, tmp_path: Path) -> None:
        """Passes when research/ exists and baseline_hashes is None."""
        family_dir = tmp_path / "workspace" / "families" / "fam_001"
        research_dir = family_dir / "research"
        research_dir.mkdir(parents=True)
        _write_file(research_dir / "features.py", "x = 1\n")

        result = check_edit_surface(
            family_dir=family_dir,
            allowed_files=["research/features.py"],
            forbidden_patterns=["engine/*"],
            baseline_hashes=None,
        )

        assert result.passed is True
        assert result.is_red_strike is False
        assert result.violations == []

    def test_fails_red_strike_when_research_dir_missing(self, tmp_path: Path) -> None:
        """Fails with is_red_strike=True when research/ directory doesn't exist."""
        family_dir = tmp_path / "workspace" / "families" / "fam_001"
        family_dir.mkdir(parents=True)

        result = check_edit_surface(
            family_dir=family_dir,
            allowed_files=["research/features.py"],
            forbidden_patterns=[],
            baseline_hashes=None,
        )

        assert result.passed is False
        assert result.is_red_strike is True
        assert "research/ directory does not exist" in result.violations[0]

    def test_fails_when_file_not_in_allowed_and_hash_differs(self, tmp_path: Path) -> None:
        """Fails when a file in research/ is not in allowed_files and its hash differs."""
        family_dir = tmp_path / "workspace" / "families" / "fam_001"
        research_dir = family_dir / "research"
        research_dir.mkdir(parents=True)

        # Create a file that is NOT in allowed_files
        unauthorized = research_dir / "secret.py"
        _write_file(unauthorized, "original content\n")
        original_hash = _hash_file(unauthorized)

        # Build baseline with the original hash
        baseline = {"research/secret.py": original_hash}

        # Now modify the file so the hash differs
        unauthorized.write_text("modified content\n")

        result = check_edit_surface(
            family_dir=family_dir,
            allowed_files=["research/features.py"],
            forbidden_patterns=[],
            baseline_hashes=baseline,
        )

        assert result.passed is False
        assert result.is_red_strike is True
        assert any("Unauthorized file modified" in v for v in result.violations)

    def test_fails_when_unauthorized_new_py_file_appears(self, tmp_path: Path) -> None:
        """Fails when unauthorized new .py file appears in research/ (not in baseline)."""
        family_dir = tmp_path / "workspace" / "families" / "fam_001"
        research_dir = family_dir / "research"
        research_dir.mkdir(parents=True)

        # Create baseline with only allowed files
        allowed = research_dir / "features.py"
        _write_file(allowed, "x = 1\n")
        baseline = {"research/features.py": _hash_file(allowed)}

        # Now add an unauthorized new file
        _write_file(research_dir / "hack.py", "import os\n")

        result = check_edit_surface(
            family_dir=family_dir,
            allowed_files=["research/features.py"],
            forbidden_patterns=[],
            baseline_hashes=baseline,
        )

        assert result.passed is False
        assert result.is_red_strike is True
        assert any("Unauthorized new file" in v for v in result.violations)

    def test_passes_when_only_allowed_files_and_hashes_match(self, tmp_path: Path) -> None:
        """Passes when only allowed files exist and hashes match baseline."""
        family_dir = tmp_path / "workspace" / "families" / "fam_001"
        research_dir = family_dir / "research"
        research_dir.mkdir(parents=True)

        features = research_dir / "features.py"
        _write_file(features, "def compute_features(bars): pass\n")

        baseline = {"research/features.py": _hash_file(features)}

        result = check_edit_surface(
            family_dir=family_dir,
            allowed_files=["research/features.py"],
            forbidden_patterns=[],
            baseline_hashes=baseline,
        )

        assert result.passed is True
        assert result.is_red_strike is False
        assert result.violations == []
