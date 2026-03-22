"""Config immutability guard: verifies configs haven't changed since family creation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from alpha_forge.app.domain.models import GuardResult
from alpha_forge.app.storage.artifact_store import ArtifactStore


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_config_immutability(
    family_id: str,
    configs_dir: Path,
    artifact_store: ArtifactStore,
) -> GuardResult:
    """Compare current config hashes against stored hashes from family creation."""
    stored = artifact_store.load_config_hashes(family_id)
    violations: list[str] = []

    if stored is None:
        violations.append("No stored config hashes found for family")
        return GuardResult(
            guard_name="config_immutability",
            passed=False,
            violations=violations,
        )

    for filename, original_hash in stored.items():
        config_file = configs_dir / filename
        if not config_file.exists():
            violations.append(f"Config file deleted: {filename}")
            continue
        current_hash = _hash_file(config_file)
        if current_hash != original_hash:
            violations.append(f"Config file modified: {filename}")

    return GuardResult(
        guard_name="config_immutability",
        passed=not violations,
        violations=violations,
        is_red_strike=False,
    )
