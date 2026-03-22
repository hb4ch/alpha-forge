#!/usr/bin/env python3
"""CLI for running holdout evaluation."""

import logging

import click

from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.engine.holdout_runner import run_holdout


@click.command()
@click.option("--family", "-f", required=True, help="Family ID")
@click.option("--workspace", "-w", default="alpha_research", help="Workspace root")
@click.option("--configs", "-c", default="configs", help="Configs directory")
def main(family: str, workspace: str, configs: str) -> None:
    """Run holdout evaluation for a family."""
    logging.basicConfig(level=logging.INFO)

    store = MarkdownStore(workspace)
    results = run_holdout(family, store, configs_dir=configs)

    click.echo(f"\nHoldout results for {family}:")
    for r in results:
        click.echo(f"\n  {r.symbol}:")
        click.echo(f"    Sharpe: {r.sharpe:.3f}")
        click.echo(f"    Return: {r.total_return:.3f}")
        click.echo(f"    Max DD: {r.max_drawdown:.3f}")


if __name__ == "__main__":
    main()
