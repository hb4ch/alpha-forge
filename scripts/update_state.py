#!/usr/bin/env python3
"""CLI for manual state transitions (admin tool)."""

import logging

import click

from alpha_forge.app.domain.events import FamilyEvent
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.transitions import TransitionEngine


@click.command()
@click.option("--family", "-f", required=True, help="Family ID")
@click.option("--event", "-e", required=True, type=click.Choice([e.value for e in FamilyEvent]),
              help="Event to apply")
@click.option("--workspace", "-w", default="alpha_research", help="Workspace root")
def main(family: str, event: str, workspace: str) -> None:
    """Manually apply a state transition to a family."""
    logging.basicConfig(level=logging.INFO)

    store = MarkdownStore(workspace)
    family_obj = store.read_family(family)

    click.echo(f"Current state: {family_obj.state}")
    click.echo(f"Applying event: {event}")

    engine = TransitionEngine()
    event_enum = FamilyEvent(event)

    try:
        result = engine.apply(family_obj, event_enum)
        store.write_family(result.family)
        store.append_history(family, f"Manual transition: {event} -> {result.new_state}")

        click.echo(f"New state: {result.new_state}")
        if result.cancelled:
            click.echo("Family CANCELLED due to 3-strikes!")
    except Exception as e:
        click.echo(f"Error: {e}")


if __name__ == "__main__":
    main()
