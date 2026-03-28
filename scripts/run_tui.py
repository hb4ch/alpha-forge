"""CLI entry point for the Alpha Forge TUI."""
import click

from alpha_forge.tui.app import AlphaForgeApp


@click.command()
@click.option("--workspace", default="alpha_research", help="Workspace root directory")
@click.option("--configs", default="configs", help="Configs directory")
@click.option("--family", default=None, help="Start with specific family")
@click.option("--max-iterations", default=10, help="Max iterations per run")
def main(workspace: str, configs: str, family: str | None, max_iterations: int) -> None:
    """Launch the Alpha Forge TUI dashboard."""
    app = AlphaForgeApp(
        workspace=workspace,
        configs_dir=configs,
        family_id=family,
        max_iterations=max_iterations,
    )
    app.run()


if __name__ == "__main__":
    main()
