"""Tests for the time integrity (timestamp) guard."""

from __future__ import annotations

from pathlib import Path

from alpha_forge.app.guards.timestamp_guard import check_time_integrity


def _write_research_file(family_dir: Path, filename: str, content: str) -> None:
    research_dir = family_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    (research_dir / filename).write_text(content)


class TestTimestampGuard:
    """Tests for check_time_integrity."""

    def test_detects_shift_minus_one(self, tmp_path: Path) -> None:
        """Detects df.shift(-1) as a violation with line number."""
        family_dir = tmp_path / "family"
        _write_research_file(family_dir, "features.py", "import pandas as pd\ndf.shift(-1)\n")

        result = check_time_integrity(family_dir)

        assert result.passed is False
        assert result.is_red_strike is True
        assert len(result.violations) == 1
        assert "Line 2" in result.violations[0]
        assert "shift(-1)" in result.violations[0]

    def test_detects_shift_minus_five(self, tmp_path: Path) -> None:
        """Detects df.shift(-5) as a violation."""
        family_dir = tmp_path / "family"
        _write_research_file(family_dir, "features.py", "x = df.shift(-5)\n")

        result = check_time_integrity(family_dir)

        assert result.passed is False
        assert result.is_red_strike is True
        assert any("shift(-5)" in v for v in result.violations)

    def test_allows_positive_shift(self, tmp_path: Path) -> None:
        """Allows df.shift(1) — backward/positive shift is fine."""
        family_dir = tmp_path / "family"
        _write_research_file(family_dir, "features.py", "x = df.shift(1)\n")

        result = check_time_integrity(family_dir)

        assert result.passed is True
        assert result.violations == []

    def test_allows_shift_zero(self, tmp_path: Path) -> None:
        """Allows df.shift(0)."""
        family_dir = tmp_path / "family"
        _write_research_file(family_dir, "features.py", "x = df.shift(0)\n")

        result = check_time_integrity(family_dir)

        assert result.passed is True
        assert result.violations == []

    def test_handles_syntax_errors_gracefully(self, tmp_path: Path) -> None:
        """Reports syntax errors as a violation without crashing."""
        family_dir = tmp_path / "family"
        _write_research_file(family_dir, "features.py", "def broken(\n")

        result = check_time_integrity(family_dir)

        assert result.passed is False
        assert any("syntax error" in v for v in result.violations)

    def test_passes_clean_code(self, tmp_path: Path) -> None:
        """Passes clean code with no shifts."""
        family_dir = tmp_path / "family"
        _write_research_file(
            family_dir,
            "features.py",
            "import pandas as pd\n\ndef compute_features(bars):\n    return bars\n",
        )

        result = check_time_integrity(family_dir)

        assert result.passed is True
        assert result.violations == []

    def test_passes_code_with_only_positive_shifts(self, tmp_path: Path) -> None:
        """Passes code that only uses positive (backward-looking) shifts."""
        family_dir = tmp_path / "family"
        _write_research_file(
            family_dir,
            "features.py",
            "x = df.shift(1)\ny = df.shift(5)\nz = df.shift(10)\n",
        )

        result = check_time_integrity(family_dir)

        assert result.passed is True
        assert result.violations == []
