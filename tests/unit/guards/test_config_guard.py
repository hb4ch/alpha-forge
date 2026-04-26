"""Tests for the config immutability guard."""

from __future__ import annotations

import hashlib
from pathlib import Path

from alpha_forge.app.guards.config_guard import check_config_immutability
from alpha_forge.app.storage.artifact_store import ArtifactStore


def _hash_content(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestConfigGuard:
    """Tests for check_config_immutability."""

    def test_bootstraps_when_no_stored_hashes(self, tmp_path: Path) -> None:
        """Bootstraps hashes and passes when no stored hashes exist."""
        ws = tmp_path / "alpha_research"
        ws.mkdir()
        (ws / "families" / "fam_001").mkdir(parents=True)
        store = ArtifactStore(ws)
        configs_dir = tmp_path / "configs"
        configs_dir.mkdir()
        content = "universe:\n  - BTCUSDT\n"
        _write_yaml(configs_dir / "universe.yaml", content)

        result = check_config_immutability("fam_001", configs_dir, store)

        assert result.passed is True
        assert result.violations == []
        # Verify hashes were stored for future checks
        stored = store.load_config_hashes("fam_001")
        assert stored is not None
        assert "universe.yaml" in stored
        assert stored["universe.yaml"] == _hash_content(content)

    def test_passes_when_hashes_match(self, tmp_path: Path) -> None:
        """Passes when all current file hashes match stored hashes."""
        ws = tmp_path / "alpha_research"
        (ws / "families" / "fam_001").mkdir(parents=True)
        store = ArtifactStore(ws)

        configs_dir = tmp_path / "configs"
        configs_dir.mkdir()
        content = "universe:\n  - BTCUSDT\n"
        _write_yaml(configs_dir / "universe.yaml", content)

        hashes = {"universe.yaml": _hash_content(content)}
        store.save_config_hashes("fam_001", hashes)

        result = check_config_immutability("fam_001", configs_dir, store)

        assert result.passed is True
        assert result.violations == []

    def test_fails_when_config_content_differs(self, tmp_path: Path) -> None:
        """Fails when a config file's content differs from stored hash."""
        ws = tmp_path / "alpha_research"
        (ws / "families" / "fam_001").mkdir(parents=True)
        store = ArtifactStore(ws)

        configs_dir = tmp_path / "configs"
        configs_dir.mkdir()
        original = "universe:\n  - BTCUSDT\n"
        _write_yaml(configs_dir / "universe.yaml", original)

        hashes = {"universe.yaml": _hash_content(original)}
        store.save_config_hashes("fam_001", hashes)

        # Modify the config file
        (configs_dir / "universe.yaml").write_text("universe:\n  - ETHUSDT\n")

        result = check_config_immutability("fam_001", configs_dir, store)

        assert result.passed is False
        assert any("Config file modified" in v for v in result.violations)

    def test_excludes_llm_yaml_on_bootstrap(self, tmp_path: Path) -> None:
        """llm.yaml is an operator config — not hashed on bootstrap."""
        ws = tmp_path / "alpha_research"
        (ws / "families" / "fam_001").mkdir(parents=True)
        store = ArtifactStore(ws)

        configs_dir = tmp_path / "configs"
        configs_dir.mkdir()
        _write_yaml(configs_dir / "universe.yaml", "universe:\n  - BTCUSDT\n")
        _write_yaml(configs_dir / "llm.yaml", "tiers:\n  heavy:\n    model: foo\n")

        result = check_config_immutability("fam_001", configs_dir, store)

        assert result.passed is True
        stored = store.load_config_hashes("fam_001")
        assert "universe.yaml" in stored
        assert "llm.yaml" not in stored

    def test_passes_when_only_llm_yaml_changes(self, tmp_path: Path) -> None:
        """Modifying llm.yaml is an operator model swap, not a guard violation."""
        ws = tmp_path / "alpha_research"
        (ws / "families" / "fam_001").mkdir(parents=True)
        store = ArtifactStore(ws)

        configs_dir = tmp_path / "configs"
        configs_dir.mkdir()
        _write_yaml(configs_dir / "universe.yaml", "universe:\n  - BTCUSDT\n")
        _write_yaml(configs_dir / "llm.yaml", "tiers:\n  heavy:\n    model: old\n")

        # Simulate a pre-existing family baseline that (erroneously) included llm.yaml
        hashes = {
            "universe.yaml": _hash_content("universe:\n  - BTCUSDT\n"),
            "llm.yaml": _hash_content("tiers:\n  heavy:\n    model: old\n"),
        }
        store.save_config_hashes("fam_001", hashes)

        # Swap the model after family creation
        (configs_dir / "llm.yaml").write_text("tiers:\n  heavy:\n    model: new\n")

        result = check_config_immutability("fam_001", configs_dir, store)

        assert result.passed is True
        assert result.violations == []

    def test_fails_when_config_file_deleted(self, tmp_path: Path) -> None:
        """Fails when a config file is deleted."""
        ws = tmp_path / "alpha_research"
        (ws / "families" / "fam_001").mkdir(parents=True)
        store = ArtifactStore(ws)

        configs_dir = tmp_path / "configs"
        configs_dir.mkdir()
        content = "costs:\n  maker: 0.0002\n"
        _write_yaml(configs_dir / "costs.yaml", content)

        hashes = {"costs.yaml": _hash_content(content)}
        store.save_config_hashes("fam_001", hashes)

        # Delete the file
        (configs_dir / "costs.yaml").unlink()

        result = check_config_immutability("fam_001", configs_dir, store)

        assert result.passed is False
        assert any("Config file deleted" in v for v in result.violations)
