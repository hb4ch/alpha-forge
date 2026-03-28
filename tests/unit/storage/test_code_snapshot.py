"""Tests for code snapshot save/load in ArtifactStore."""
from __future__ import annotations

from alpha_forge.app.storage.artifact_store import ArtifactStore


class TestCodeSnapshot:
    def test_save_and_load(self, tmp_path) -> None:
        store = ArtifactStore(tmp_path / "alpha_research")
        files = {"features.py": "def compute_features(): pass", "signal_combiner.py": "def combine(): pass"}
        store.save_code_snapshot("fam_001", 1, files)

        loaded = store.load_code_snapshot("fam_001", 1)
        assert loaded == files

    def test_load_missing_returns_none(self, tmp_path) -> None:
        store = ArtifactStore(tmp_path / "alpha_research")
        assert store.load_code_snapshot("fam_001", 99) is None
