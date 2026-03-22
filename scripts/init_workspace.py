#!/usr/bin/env python3
"""Initialize the alpha_research workspace directory tree."""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
import yaml

from alpha_forge.app.storage.markdown_store import MarkdownStore

logger = logging.getLogger(__name__)


def _auto_detect_splits(data_root: Path | None = None) -> dict | None:
    """Auto-detect data range and compute 60/20/20 splits."""
    try:
        from pegasus.config import BacktestConfig
        from pegasus.data.provider import DataProvider

        config = BacktestConfig()
        if data_root:
            config = config.model_copy(update={"data_root": data_root})

        with DataProvider(config) as provider:
            symbols = provider.get_symbols()
            if not symbols:
                return None

            ranges = []
            for sym in symbols:
                try:
                    start, end = provider.get_date_range(sym)
                    ranges.append((start, end))
                except Exception:
                    continue

            if not ranges:
                return None

            # Find common overlap
            common_start = max(r[0] for r in ranges)
            common_end = min(r[1] for r in ranges)

            total_days = (common_end - common_start).days
            train_days = int(total_days * 0.6)
            val_days = int(total_days * 0.2)

            train_end = common_start + timedelta(days=train_days)
            val_end = train_end + timedelta(days=val_days)

            return {
                "train": {
                    "start": common_start.strftime("%Y-%m-%d"),
                    "end": train_end.strftime("%Y-%m-%d"),
                },
                "validation": {
                    "start": train_end.strftime("%Y-%m-%d"),
                    "end": val_end.strftime("%Y-%m-%d"),
                },
                "holdout": {
                    "start": val_end.strftime("%Y-%m-%d"),
                    "end": common_end.strftime("%Y-%m-%d"),
                },
            }
    except Exception as e:
        logger.warning("Auto-detect splits failed: %s", e)
        return None


@click.command()
@click.option("--workspace", "-w", default="alpha_research", help="Workspace root directory")
@click.option("--configs", "-c", default="configs", help="Configs directory")
@click.option("--auto-splits", is_flag=True, help="Auto-detect data splits from available data")
def main(workspace: str, configs: str, auto_splits: bool) -> None:
    """Initialize the alpha_research workspace directory structure."""
    logging.basicConfig(level=logging.INFO)

    root = Path(workspace).resolve()

    # Create directory tree
    dirs = [
        root / "seeds" / "inbox",
        root / "seeds" / "distilled",
        root / "seeds" / "accepted",
        root / "seeds" / "rejected",
        root / "families",
        root / "ledger",
        root / "judges" / "verdicts",
        root / "reports",
    ]

    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        click.echo(f"  Created: {d}")

    # Create initial STATE.md
    store = MarkdownStore(workspace)
    if not (root / "STATE.md").exists():
        store.write_global_state({
            "active_family": None,
            "state": "IDLE",
            "current_iteration": 0,
            "strike_count": 0,
            "red_strike_count": 0,
            "best_qualified_score": 0.0,
            "last_transition_at": datetime.now(tz=timezone.utc).isoformat(),
        })
        click.echo("  Created: STATE.md")

    # Create IDEAS.md
    ideas_path = root / "IDEAS.md"
    if not ideas_path.exists():
        ideas_path.write_text("# Ideas Registry\n\nTrack all seed ideas and their status.\n")
        click.echo("  Created: IDEAS.md")

    # Create STRIKES.md
    strikes_path = root / "STRIKES.md"
    if not strikes_path.exists():
        strikes_path.write_text("# Strikes Log\n\nGlobal strike history across all families.\n")
        click.echo("  Created: STRIKES.md")

    # Auto-detect splits if requested
    if auto_splits:
        click.echo("\nAuto-detecting data splits...")
        splits = _auto_detect_splits()
        if splits:
            configs_path = Path(configs).resolve()
            splits_file = configs_path / "splits.yaml"
            with open(splits_file, "w") as f:
                yaml.dump(splits, f, default_flow_style=False)
            click.echo(f"  Updated splits.yaml:")
            click.echo(f"    Train: {splits['train']['start']} to {splits['train']['end']}")
            click.echo(f"    Validation: {splits['validation']['start']} to {splits['validation']['end']}")
            click.echo(f"    Holdout: {splits['holdout']['start']} to {splits['holdout']['end']}")
        else:
            click.echo("  Could not auto-detect splits. Using defaults from configs/splits.yaml")

    click.echo(f"\nWorkspace initialized at: {root}")


if __name__ == "__main__":
    main()
