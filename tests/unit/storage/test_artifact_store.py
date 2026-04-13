"""Tests for ArtifactStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from alpha_forge.app.storage.artifact_store import ArtifactStore


class TestBacktestResultRoundtrip:
    def test_save_and_load(self, artifact_store: ArtifactStore) -> None:
        data = {"sharpe": 1.5}
        artifact_store.save_backtest_result("fam_001", 1, data)
        loaded = artifact_store.load_backtest_result("fam_001", 1)
        assert loaded == {"sharpe": 1.5}


class TestRobustnessResultRoundtrip:
    def test_save_and_load(self, artifact_store: ArtifactStore) -> None:
        data = {"cost_2x_passed": True, "degradation": 0.05}
        artifact_store.save_robustness_result("fam_001", 1, data)
        loaded = artifact_store.load_robustness_result("fam_001", 1)
        assert loaded == data


class TestLoadReturnsNone:
    def test_nonexistent_backtest(self, artifact_store: ArtifactStore) -> None:
        result = artifact_store.load_backtest_result("fam_999", 99)
        assert result is None


class TestConfigHashesRoundtrip:
    def test_save_and_load(self, artifact_store: ArtifactStore) -> None:
        hashes = {"costs.yaml": "abc123"}
        artifact_store.save_config_hashes("fam_001", hashes)
        loaded = artifact_store.load_config_hashes("fam_001")
        assert loaded == {"costs.yaml": "abc123"}


class TestBestResultRoundtrip:
    def test_save_and_load(self, artifact_store: ArtifactStore) -> None:
        data = {"sharpe": 2.0, "iteration": 3}
        artifact_store.save_best_result("fam_001", data)
        loaded = artifact_store.load_best_result("fam_001")
        assert loaded == data


class TestReproducibilityMetadataRoundtrip:
    def test_save_and_load(self, artifact_store: ArtifactStore) -> None:
        data = {"python_version": "3.13", "git_hash": "abc123", "platform": "linux"}
        artifact_store.save_reproducibility_metadata("fam_001", data)
        loaded = artifact_store.load_reproducibility_metadata("fam_001")
        assert loaded == data


class TestLoadConfigHashesReturnsNone:
    def test_nonexistent_family(self, artifact_store: ArtifactStore) -> None:
        result = artifact_store.load_config_hashes("fam_nonexistent")
        assert result is None


class TestCheckpointRoundtrip:
    def test_save_and_load(self, artifact_store: ArtifactStore) -> None:
        code = {"features.py": "def f(): pass", "model_config.py": "MC = {}"}
        artifact_store.save_checkpoint("fam_001", code)
        loaded = artifact_store.load_checkpoint("fam_001")
        assert loaded == code

    def test_load_nonexistent_returns_none(self, artifact_store: ArtifactStore) -> None:
        assert artifact_store.load_checkpoint("fam_999") is None

    def test_overwrite_existing(self, artifact_store: ArtifactStore) -> None:
        artifact_store.save_checkpoint("fam_001", {"a.py": "v1"})
        artifact_store.save_checkpoint("fam_001", {"a.py": "v2"})
        loaded = artifact_store.load_checkpoint("fam_001")
        assert loaded == {"a.py": "v2"}
