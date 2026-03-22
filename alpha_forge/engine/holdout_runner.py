"""Holdout runner: runs the strategy on the holdout split.

Only callable after a family has been promoted to holdout.
"""

from __future__ import annotations

import logging

from alpha_forge.app.domain.models import BacktestResultSummary
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.engine.backtest_runner import run_backtest

logger = logging.getLogger(__name__)


def run_holdout(
    family_id: str,
    store: MarkdownStore,
    configs_dir: str = "configs",
) -> list[BacktestResultSummary]:
    """Run the holdout backtest for a family.

    This should only be called when the family is in HOLDOUT_RUNNING state.
    The holdout split is defined in configs/splits.yaml and has not been
    touched before this point.
    """
    logger.info("Running holdout backtest for family %s", family_id)
    return run_backtest(
        family_id,
        store,
        configs_dir=configs_dir,
        split_name="holdout",
    )
