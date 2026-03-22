#!/usr/bin/env python3
"""CLI for ingesting a raw seed idea."""

import click

from alpha_forge.app.agents.llm_client import LLMClient
from alpha_forge.app.agents.judge_seed import SeedJudge
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.seed_flow import (
    create_family,
    distill_seed,
    ingest_seed,
    screen_seed,
)


@click.command()
@click.option("--text", "-t", required=True, help="Raw seed text (idea, claim, observation)")
@click.option("--source", "-s", required=True, help="Source of the seed (paper, tweet, blog, etc.)")
@click.option("--workspace", "-w", default="alpha_research", help="Workspace root directory")
@click.option("--auto-create", is_flag=True, help="Automatically create family if seed is accepted")
def main(text: str, source: str, workspace: str, auto_create: bool) -> None:
    """Ingest a raw seed, distill it, screen it, and optionally create a family."""
    store = MarkdownStore(workspace)
    artifact_store = ArtifactStore(workspace)

    # Step 1: Ingest
    click.echo("Ingesting seed...")
    seed_id = ingest_seed(text, source, store)
    click.echo(f"  Seed ID: {seed_id}")

    # Step 2: Distill
    click.echo("Distilling seed via LLM...")
    card = distill_seed(seed_id, store)
    click.echo(f"  Hypothesis: {card.testable_hypothesis}")
    click.echo(f"  Mechanism: {card.mechanism}")
    click.echo(f"  Horizon: {card.horizon}")

    # Step 3: Screen
    click.echo("Screening seed via Seed Judge...")
    verdict = screen_seed(seed_id, store)
    click.echo(f"  Verdict: {verdict.verdict}")
    click.echo(f"  Reasoning: {verdict.reasoning_summary}")
    if verdict.risk_flags:
        click.echo(f"  Risk flags: {', '.join(verdict.risk_flags)}")

    # Step 4: Create family (optional)
    if auto_create and verdict.verdict.value.startswith("accept"):
        click.echo("Creating family...")
        family = create_family(seed_id, store, artifact_store)
        click.echo(f"  Family ID: {family.family_id}")
        click.echo(f"  State: {family.state}")
    elif verdict.verdict.value.startswith("accept"):
        click.echo(f"\nSeed accepted. Run `create_family.py --seed-id {seed_id}` to create a family.")
    else:
        click.echo("\nSeed rejected. See rejection details above.")


if __name__ == "__main__":
    main()
