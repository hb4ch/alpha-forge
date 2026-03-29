"""JSON artifact storage for backtest results, robustness results, and config hashes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class ArtifactStore:
    """JSON artifact storage layer."""

    def __init__(self, workspace_root: str | Path = "alpha_research") -> None:
        self.root = Path(workspace_root).resolve()

    def _atomic_write_json(self, path: Path, data: Any) -> None:
        """Write JSON atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def load_json(self, path: Path) -> Any:
        """Load a JSON file."""
        with open(path) as f:
            return json.load(f)

    def save_json(self, path: Path, data: Any) -> None:
        """Save a JSON file atomically."""
        self._atomic_write_json(path, data)

    # ------------------------------------------------------------------
    # Backtest results
    # ------------------------------------------------------------------

    def _reports_dir(self, family_id: str) -> Path:
        return self.root / "reports" / family_id

    def save_backtest_result(
        self,
        family_id: str,
        iteration: int,
        result: dict[str, Any],
    ) -> Path:
        """Save backtest results for an iteration."""
        path = self._reports_dir(family_id) / f"iter_{iteration}_backtest.json"
        self._atomic_write_json(path, result)
        return path

    def load_backtest_result(
        self,
        family_id: str,
        iteration: int,
    ) -> dict[str, Any] | None:
        """Load backtest results for an iteration."""
        path = self._reports_dir(family_id) / f"iter_{iteration}_backtest.json"
        if not path.exists():
            return None
        return self.load_json(path)

    # ------------------------------------------------------------------
    # Robustness results
    # ------------------------------------------------------------------

    def save_robustness_result(
        self,
        family_id: str,
        iteration: int,
        result: dict[str, Any],
    ) -> Path:
        """Save robustness results for an iteration."""
        path = self._reports_dir(family_id) / f"iter_{iteration}_robustness.json"
        self._atomic_write_json(path, result)
        return path

    def load_robustness_result(
        self,
        family_id: str,
        iteration: int,
    ) -> dict[str, Any] | None:
        """Load robustness results for an iteration."""
        path = self._reports_dir(family_id) / f"iter_{iteration}_robustness.json"
        if not path.exists():
            return None
        return self.load_json(path)

    # ------------------------------------------------------------------
    # Holdout results
    # ------------------------------------------------------------------

    def save_holdout_result(
        self,
        family_id: str,
        iteration: int,
        result: dict[str, Any],
    ) -> Path:
        """Save holdout results."""
        path = self._reports_dir(family_id) / f"iter_{iteration}_holdout.json"
        self._atomic_write_json(path, result)
        return path

    # ------------------------------------------------------------------
    # Best result tracking
    # ------------------------------------------------------------------

    def save_best_result(self, family_id: str, result: dict[str, Any]) -> None:
        """Save the best result for a family."""
        path = self.root / "families" / family_id / "best_result.json"
        self._atomic_write_json(path, result)

    def load_best_result(self, family_id: str) -> dict[str, Any] | None:
        """Load the best result for a family."""
        path = self.root / "families" / family_id / "best_result.json"
        if not path.exists():
            return None
        return self.load_json(path)

    # ------------------------------------------------------------------
    # Config hashes (for config guard)
    # ------------------------------------------------------------------

    def save_config_hashes(self, family_id: str, hashes: dict[str, str]) -> None:
        """Save config file hashes at family creation time."""
        path = self.root / "families" / family_id / "config_hashes.json"
        self._atomic_write_json(path, hashes)

    def load_config_hashes(self, family_id: str) -> dict[str, str] | None:
        """Load stored config hashes for a family."""
        path = self.root / "families" / family_id / "config_hashes.json"
        if not path.exists():
            return None
        return self.load_json(path)

    def save_reproducibility_metadata(self, family_id: str, metadata: dict[str, str]) -> None:
        """Save reproducibility metadata for a family."""
        path = self.root / "families" / family_id / "reproducibility.json"
        self._atomic_write_json(path, metadata)

    def load_reproducibility_metadata(self, family_id: str) -> dict[str, str] | None:
        """Load reproducibility metadata for a family."""
        path = self.root / "families" / family_id / "reproducibility.json"
        if not path.exists():
            return None
        return self.load_json(path)

    # ------------------------------------------------------------------
    # Strike count (JSON artifact)
    # ------------------------------------------------------------------

    def save_strike_count(self, family_id: str, data: dict[str, Any]) -> None:
        """Save strike count JSON."""
        path = self.root / "families" / family_id / "strike_count.json"
        self._atomic_write_json(path, data)

    # ------------------------------------------------------------------
    # Code snapshots (for TUI diff view)
    # ------------------------------------------------------------------

    def save_code_snapshot(
        self,
        family_id: str,
        iteration: int,
        files: dict[str, str],
    ) -> Path:
        """Save research code snapshot for an iteration."""
        path = self._reports_dir(family_id) / f"iter_{iteration}_code.json"
        self._atomic_write_json(path, files)
        return path

    def load_code_snapshot(
        self,
        family_id: str,
        iteration: int,
    ) -> dict[str, str] | None:
        """Load research code snapshot for an iteration."""
        path = self._reports_dir(family_id) / f"iter_{iteration}_code.json"
        if not path.exists():
            return None
        return self.load_json(path)
