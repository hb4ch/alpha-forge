"""Config immutability guard: verifies configs haven't changed since family creation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from alpha_forge.app.domain.models import GuardResult
from alpha_forge.app.storage.artifact_store import ArtifactStore

# Operator configs that do NOT affect backtest determinism and MUST NOT be hashed.
# llm.yaml selects which LLM runs the research loop — swapping providers/models
# is an operator choice, not a research violation.
EXCLUDED_CONFIGS: frozenset[str] = frozenset({"llm.yaml"})


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
        # Bootstrap: no baseline yet (reset or migration). Store current hashes and pass.
        hashes = {
            f.name: _hash_file(f)
            for f in sorted(configs_dir.glob("*.yaml"))
            if f.name not in EXCLUDED_CONFIGS
        }
        artifact_store.save_config_hashes(family_id, hashes)
        return GuardResult(
            guard_name="config_immutability",
            passed=True,
            violations=[],
        )

    for filename, original_hash in stored.items():
        if filename in EXCLUDED_CONFIGS:
            continue
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
