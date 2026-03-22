#!/usr/bin/env python3
"""CLI for creating a family from an accepted seed."""

import click

from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.seed_flow import create_family


@click.command()
@click.option("--seed-id", "-s", required=True, help="ID of the accepted seed")
@click.option("--workspace", "-w", default="alpha_research", help="Workspace root directory")
@click.option("--configs", "-c", default="configs", help="Configs directory")
def main(seed_id: str, workspace: str, configs: str) -> None:
    """Create a new idea family from an accepted seed."""
    store = MarkdownStore(workspace)
    artifact_store = ArtifactStore(workspace)

    family = create_family(seed_id, store, artifact_store, configs_dir=configs)
    click.echo(f"Family created: {family.family_id}")
    click.echo(f"  State: {family.state}")
    click.echo(f"  Hypothesis: {family.base_hypothesis}")
    click.echo(f"  Mechanism: {family.mechanism}")


if __name__ == "__main__":
    main()
