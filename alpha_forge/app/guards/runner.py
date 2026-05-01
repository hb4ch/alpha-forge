"""Guard runner: executes all applicable guards and aggregates results."""

from __future__ import annotations

import logging
from pathlib import Path

from alpha_forge.app.domain.models import GuardResult, IdeaFamily
from alpha_forge.app.guards.check_edit_surface import check_edit_surface
from alpha_forge.app.guards.config_guard import check_config_immutability
from alpha_forge.app.guards.reproducibility_guard import (
    check_reproducibility,
    collect_reproducibility_metadata,
)
from alpha_forge.app.guards.seed_mechanism_guard import check_seed_mechanism
from alpha_forge.app.guards.split_guard import check_split_isolation
from alpha_forge.app.guards.timestamp_guard import check_time_integrity
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore

logger = logging.getLogger(__name__)


def run_all_guards(
    family: IdeaFamily,
    store: MarkdownStore,
    artifact_store: ArtifactStore,
    configs_dir: str | Path = "configs",
    split_name: str = "validation",
) -> list[GuardResult]:
    """Run all deterministic guards for a family.

    Returns a list of GuardResult. Any failure is a blocking failure.
    """
    family_dir = store.root / "families" / family.family_id
    configs_path = Path(configs_dir).resolve()
    results: list[GuardResult] = []

    # 1. Edit surface guard
    result = check_edit_surface(
        family_dir=family_dir,
        allowed_files=family.editable_files,
        forbidden_patterns=family.forbidden_files,
    )
    results.append(result)
    if not result.passed:
        logger.warning("Edit surface guard failed: %s", result.violations)

    # 2. Time integrity guard
    result = check_time_integrity(family_dir)
    results.append(result)
    if not result.passed:
        logger.warning("Time integrity guard failed: %s", result.violations)

    # 3. Split isolation guard
    result = check_split_isolation(family, requested_split=split_name)
    results.append(result)
    if not result.passed:
        logger.warning("Split isolation guard failed: %s", result.violations)

    # 4. Config immutability guard
    result = check_config_immutability(family.family_id, configs_path, artifact_store)
    results.append(result)
    if not result.passed:
        logger.warning("Config immutability guard failed: %s", result.violations)

    # 5. Reproducibility guard
    prior_metadata = artifact_store.load_reproducibility_metadata(family.family_id)
    result = check_reproducibility(store.root, prior_metadata=prior_metadata)
    results.append(result)
    if not result.passed:
        logger.warning("Reproducibility guard failed: %s", result.violations)
    artifact_store.save_reproducibility_metadata(
        family.family_id,
        collect_reproducibility_metadata(store.root),
    )

    # 6. Seed-mechanism fidelity guard
    # Catches researcher substituting a proxy when the seed mandates a
    # specific MultiSourceProvider call. Skips silently for seeds that
    # don't reference the provider.
    seed_card = None
    try:
        seed_card = store.read_seed(family.seed_id, stage="accepted")
    except Exception:
        pass  # No seed on disk → guard runs against family fields only
    result = check_seed_mechanism(family_dir, seed_card, family)
    results.append(result)
    if not result.passed:
        logger.warning("Seed mechanism guard failed: %s", result.violations)

    return results


def any_failed(results: list[GuardResult]) -> bool:
    """Check if any guard failed."""
    return any(not r.passed for r in results)


def has_red_strike(results: list[GuardResult]) -> bool:
    """Check if any guard failure warrants a red strike."""
    return any(r.is_red_strike and not r.passed for r in results)
