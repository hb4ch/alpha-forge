"""Tests for the guard runner utility functions."""

from __future__ import annotations

from pathlib import Path

from alpha_forge.app.domain.models import GuardResult
from alpha_forge.app.guards.runner import any_failed, has_red_strike, run_all_guards
from tests.conftest import make_family


class TestAnyFailed:
    """Tests for any_failed."""

    def test_returns_false_when_all_pass(self) -> None:
        """Returns False when all GuardResults pass."""
        results = [
            GuardResult(guard_name="a", passed=True, is_red_strike=False),
            GuardResult(guard_name="b", passed=True, is_red_strike=False),
            GuardResult(guard_name="c", passed=True, is_red_strike=False),
        ]
        assert any_failed(results) is False

    def test_returns_true_when_one_fails(self) -> None:
        """Returns True when at least one GuardResult fails."""
        results = [
            GuardResult(guard_name="a", passed=True, is_red_strike=False),
            GuardResult(guard_name="b", passed=False, is_red_strike=False),
            GuardResult(guard_name="c", passed=True, is_red_strike=False),
        ]
        assert any_failed(results) is True


class TestHasRedStrike:
    """Tests for has_red_strike."""

    def test_returns_false_when_no_red_strikes(self) -> None:
        """Returns False when no results have red strikes."""
        results = [
            GuardResult(guard_name="a", passed=True, is_red_strike=False),
            GuardResult(guard_name="b", passed=False, is_red_strike=False),
        ]
        assert has_red_strike(results) is False

    def test_returns_true_when_failed_with_red_strike(self) -> None:
        """Returns True when a failed guard has is_red_strike=True."""
        results = [
            GuardResult(guard_name="a", passed=True, is_red_strike=False),
            GuardResult(guard_name="b", passed=False, is_red_strike=True),
        ]
        assert has_red_strike(results) is True

    def test_returns_false_when_passed_with_red_strike(self) -> None:
        """Returns False when a passed guard has is_red_strike=True (passed guards don't count)."""
        results = [
            GuardResult(guard_name="a", passed=True, is_red_strike=True),
            GuardResult(guard_name="b", passed=True, is_red_strike=False),
        ]
        assert has_red_strike(results) is False


def test_run_all_guards_persists_and_checks_reproducibility(
    markdown_store,
    artifact_store,
    tmp_path: Path,
    monkeypatch,
) -> None:
    family = make_family(family_id="fam_repro")
    markdown_store.write_family(family)
    artifact_store.save_config_hashes("fam_repro", {})

    pass_result = GuardResult(guard_name="ok", passed=True)
    monkeypatch.setattr("alpha_forge.app.guards.runner.check_edit_surface", lambda **kwargs: pass_result)
    monkeypatch.setattr("alpha_forge.app.guards.runner.check_time_integrity", lambda *_args, **_kwargs: pass_result)
    monkeypatch.setattr("alpha_forge.app.guards.runner.check_split_isolation", lambda *_args, **_kwargs: pass_result)
    monkeypatch.setattr("alpha_forge.app.guards.runner.check_config_immutability", lambda *_args, **_kwargs: pass_result)

    metadata_sequence = iter([
        {"python_version": "3.13.0", "git_hash": "abc", "platform": "linux"},
        {"python_version": "3.13.0", "git_hash": "abc", "platform": "linux"},
        {"python_version": "3.13.0", "git_hash": "def", "platform": "linux"},
        {"python_version": "3.13.0", "git_hash": "def", "platform": "linux"},
    ])
    monkeypatch.setattr(
        "alpha_forge.app.guards.reproducibility_guard.collect_reproducibility_metadata",
        lambda _root: next(metadata_sequence),
    )
    monkeypatch.setattr(
        "alpha_forge.app.guards.runner.collect_reproducibility_metadata",
        lambda _root: next(metadata_sequence),
    )

    first = run_all_guards(family, markdown_store, artifact_store, configs_dir=tmp_path / "configs")
    second = run_all_guards(family, markdown_store, artifact_store, configs_dir=tmp_path / "configs")

    assert next(result for result in first if result.guard_name == "reproducibility").passed is True
    second_repro = next(result for result in second if result.guard_name == "reproducibility")
    assert second_repro.passed is False
    assert any("git_hash" in violation for violation in second_repro.violations)
