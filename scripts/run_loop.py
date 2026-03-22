#!/usr/bin/env python3
"""CLI for running the orchestrator loop."""

import logging

import click

from alpha_forge.app.agents.llm_client import LLMClient
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.orchestrator import Orchestrator


@click.command()
@click.option("--family", "-f", default=None, help="Family ID (default: active family from STATE.md)")
@click.option("--workspace", "-w", default="alpha_research", help="Workspace root directory")
@click.option("--configs", "-c", default="configs", help="Configs directory")
@click.option("--max-iterations", "-n", default=10, help="Max iterations per run")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def main(
    family: str | None,
    workspace: str,
    configs: str,
    max_iterations: int,
    verbose: bool,
) -> None:
    """Run the orchestrator loop for a family."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    store = MarkdownStore(workspace)
    artifact_store = ArtifactStore(workspace)

    orchestrator = Orchestrator(
        store=store,
        artifact_store=artifact_store,
        configs_dir=configs,
        max_iterations=max_iterations,
    )

    result = orchestrator.run(family_id=family)

    click.echo(f"\nOrchestrator finished:")
    click.echo(f"  Family: {result.get('family_id', 'N/A')}")
    click.echo(f"  Final state: {result.get('final_state', 'N/A')}")
    click.echo(f"  Iterations run: {result.get('iterations_run', 0)}")
    click.echo(f"  Best score: {result.get('best_score', 0.0):.3f}")
    click.echo(f"  Strike count: {result.get('strike_count', 0)}")


if __name__ == "__main__":
    main()
