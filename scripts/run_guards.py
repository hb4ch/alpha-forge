#!/usr/bin/env python3
"""CLI for running guards manually on a family."""

import logging

import click

from alpha_forge.app.guards.runner import any_failed, has_red_strike, run_all_guards
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore


@click.command()
@click.option("--family", "-f", required=True, help="Family ID")
@click.option("--workspace", "-w", default="alpha_research", help="Workspace root")
@click.option("--configs", "-c", default="configs", help="Configs directory")
def main(family: str, workspace: str, configs: str) -> None:
    """Run all guards for a family."""
    logging.basicConfig(level=logging.INFO)

    store = MarkdownStore(workspace)
    artifact_store = ArtifactStore(workspace)
    family_obj = store.read_family(family)

    results = run_all_guards(family_obj, store, artifact_store, configs)

    click.echo(f"\nGuard results for {family}:")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        red = " [RED STRIKE]" if r.is_red_strike and not r.passed else ""
        click.echo(f"  {r.guard_name}: {status}{red}")
        for v in r.violations:
            click.echo(f"    - {v}")

    if any_failed(results):
        click.echo(f"\nOverall: FAILED")
        if has_red_strike(results):
            click.echo("  Red strike warranted!")
    else:
        click.echo(f"\nOverall: PASSED")


if __name__ == "__main__":
    main()
