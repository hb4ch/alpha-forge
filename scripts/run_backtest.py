#!/usr/bin/env python3
"""CLI for running a standalone backtest for a family."""

import logging

import click

from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.engine.backtest_runner import run_backtest


@click.command()
@click.option("--family", "-f", required=True, help="Family ID")
@click.option("--workspace", "-w", default="alpha_research", help="Workspace root")
@click.option("--configs", "-c", default="configs", help="Configs directory")
@click.option("--split", "-s", default="validation", help="Split to use (train/validation/holdout)")
def main(family: str, workspace: str, configs: str, split: str) -> None:
    """Run a backtest for a family on the specified split."""
    logging.basicConfig(level=logging.INFO)

    store = MarkdownStore(workspace)
    results = run_backtest(family, store, configs_dir=configs, split_name=split)

    click.echo(f"\nBacktest results for {family} ({split}):")
    for r in results:
        click.echo(f"\n  {r.symbol}:")
        click.echo(f"    Sharpe: {r.sharpe:.3f}")
        click.echo(f"    Sortino: {r.sortino:.3f}")
        click.echo(f"    Max DD: {r.max_drawdown:.3f}")
        click.echo(f"    Return: {r.total_return:.3f}")
        click.echo(f"    Win Rate: {r.win_rate:.3f}")
        click.echo(f"    Trades: {r.total_trades}")


if __name__ == "__main__":
    main()
