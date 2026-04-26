#!/usr/bin/env python3
"""Resume an iteration after a manual patch of research files.

The all-coaching loop currently lacks an escape hatch when the code judge
loops on the same complaint. This helper bypasses the researcher for ONE
iteration: it freezes the on-disk research/*.py as the "submission", lets
tier-2 judges review them, and continues guards → backtest → result.

Usage:
    uv run python scripts/resume_with_manual_patch.py --family <family_id>
"""

from __future__ import annotations

import logging
from pathlib import Path

import click

from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.family_flow import FamilyFlow


RESEARCH_FILES = ["features.py", "labels.py", "model_config.py", "signal_combiner.py"]


@click.command()
@click.option("--family", "-f", required=True, help="Family ID")
@click.option("--workspace", "-w", default="alpha_research", help="Workspace root directory")
@click.option("--configs", "-c", default="configs", help="Configs directory")
def main(family: str, workspace: str, configs: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    store = MarkdownStore(workspace)
    artifact_store = ArtifactStore(workspace)
    flow = FamilyFlow(store, artifact_store, configs_dir=configs)

    research_dir = Path(workspace) / "families" / family / "research"
    on_disk = {fn: (research_dir / fn).read_text() for fn in RESEARCH_FILES if (research_dir / fn).exists()}
    click.echo(f"Freezing {len(on_disk)} on-disk files as researcher submission: {list(on_disk)}")

    def frozen_write_code(*args, **kwargs):
        return dict(on_disk)

    flow.researcher.write_code = frozen_write_code  # type: ignore[assignment]

    iteration = flow.run_iteration(family)

    click.echo(f"\nIteration {iteration.iteration_id}:")
    click.echo(f"  Stage:                {iteration.stage}")
    click.echo(f"  Verdict:              {iteration.verdict}")
    click.echo(f"  Qualified improvement: {iteration.qualified_improvement}")
    if iteration.composite_score:
        click.echo(f"  Composite score:      {iteration.composite_score.total:.3f}")


if __name__ == "__main__":
    main()
