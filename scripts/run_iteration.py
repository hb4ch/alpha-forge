#!/usr/bin/env python3
"""CLI for running a single iteration of a family."""

import logging

import click

from alpha_forge.app.agents.llm_client import LLMClient
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.family_flow import FamilyFlow


@click.command()
@click.option("--family", "-f", required=True, help="Family ID")
@click.option("--workspace", "-w", default="alpha_research", help="Workspace root directory")
@click.option("--configs", "-c", default="configs", help="Configs directory")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def main(family: str, workspace: str, configs: str, verbose: bool) -> None:
    """Run a single iteration for a family."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    store = MarkdownStore(workspace)
    artifact_store = ArtifactStore(workspace)
    flow = FamilyFlow(store, artifact_store, configs_dir=configs)

    iteration = flow.run_iteration(family)

    click.echo(f"\nIteration {iteration.iteration_id}:")
    click.echo(f"  Stage: {iteration.stage}")
    click.echo(f"  Verdict: {iteration.verdict}")
    click.echo(f"  Qualified improvement: {iteration.qualified_improvement}")
    if iteration.composite_score:
        click.echo(f"  Composite score: {iteration.composite_score.total:.3f}")


if __name__ == "__main__":
    main()
