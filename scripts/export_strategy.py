"""Export an alpha-forge family as a strategy bundle for alpha-trader.

The bundle is the **only** wire format between alpha-forge (research) and
alpha-trader (execution). This script copies a family's four research .py
files into a directory layout that ``alpha_trader.bundle.StrategyBundle.load``
can consume, plus a ``manifest.json`` carrying the bare minimum metadata
the trader needs.

Intentionally has zero imports of ``alpha_trader.*`` and only uses standard
alpha-forge dependencies (frontmatter, pyyaml). The trader has zero imports
of this script. The contract between the two is the bundle's on-disk shape,
nothing else.

Usage::

    uv run python scripts/export_strategy.py FAMILY_ID --output-dir bundles/
    # or:
    uv run python scripts/export_strategy.py FAMILY_ID --archive bundles/foo.tar.gz
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter

# ----------------------------------------------------------------------
# Shared rule (matches alpha_trader.bundle.infer_min_history_bars exactly).
# Keep in sync if the trader's heuristic changes.
# ----------------------------------------------------------------------

_LOOKBACK_SUBSTRINGS = (
    "lookback", "window", "period", "_n", "ema", "sma", "atr", "rolling",
)


def infer_min_history_bars(config: dict[str, Any]) -> int:
    """Floor of 200; otherwise max int value at any lookback-named key."""
    candidates = [200]
    for key, val in config.items():
        if not isinstance(val, int):
            continue
        if any(sub in key.lower() for sub in _LOOKBACK_SUBSTRINGS):
            candidates.append(val)
    return max(candidates)


# ----------------------------------------------------------------------
# Family loader (filesystem-only)
# ----------------------------------------------------------------------


def load_family(workspace_root: Path, family_id: str) -> tuple[Path, dict[str, Any]]:
    """Locate the family directory and return (path, frontmatter dict)."""
    family_dir = workspace_root / "families" / family_id
    if not family_dir.exists():
        raise FileNotFoundError(f"family not found: {family_dir}")
    family_md = family_dir / "FAMILY.md"
    if not family_md.exists():
        raise FileNotFoundError(f"missing FAMILY.md in {family_dir}")
    fm = frontmatter.load(family_md)
    return family_dir, dict(fm.metadata)


def load_model_config(family_dir: Path) -> dict[str, Any]:
    """Dynamically import research/model_config.py and return MODEL_CONFIG."""
    cfg_path = family_dir / "research" / "model_config.py"
    if not cfg_path.exists():
        raise FileNotFoundError(f"missing model_config.py in {family_dir / 'research'}")
    spec = importlib.util.spec_from_file_location("_export_model_config", cfg_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {cfg_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_export_model_config"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop("_export_model_config", None)
    if not hasattr(mod, "MODEL_CONFIG"):
        raise AttributeError(f"{cfg_path} does not define MODEL_CONFIG")
    return dict(mod.MODEL_CONFIG)


# ----------------------------------------------------------------------
# Bundle assembly
# ----------------------------------------------------------------------


REQUIRED_FILES = ("features.py", "model_config.py", "signal_combiner.py")
OPTIONAL_FILES = ("labels.py",)


def _compute_code_hash(stage_dir: Path) -> str:
    """SHA-256 of the four code files in fixed order. Must match
    alpha_trader.bundle._compute_code_hash byte-for-byte."""
    h = hashlib.sha256()
    for fn in sorted(REQUIRED_FILES + OPTIONAL_FILES):
        f = stage_dir / fn
        if f.exists():
            h.update(fn.encode())
            h.update(b"\0")
            h.update(f.read_bytes())
            h.update(b"\0\0")
    return h.hexdigest()


def build_bundle(
    *,
    family_id: str,
    family_dir: Path,
    family_meta: dict[str, Any],
    output_dir: Path,
    alpha_forge_commit: str | None = None,
) -> Path:
    """Assemble bundle layout in ``output_dir / family_id`` and return path."""
    research = family_dir / "research"
    for fn in REQUIRED_FILES:
        if not (research / fn).exists():
            raise FileNotFoundError(f"family {family_id} missing research/{fn}")

    stage_dir = output_dir / family_id
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)

    for fn in REQUIRED_FILES + OPTIONAL_FILES:
        src = research / fn
        if src.exists():
            shutil.copy2(src, stage_dir / fn)

    # ── manifest.json ──
    config = load_model_config(family_dir)
    timeframe = config.get("timeframe")
    if not timeframe:
        raise ValueError(f"MODEL_CONFIG.timeframe is required (family {family_id})")
    universe = config.get("universe") or family_meta.get("universe") or []
    if not isinstance(universe, list) or not universe:
        raise ValueError(
            f"family {family_id}: universe must be a non-empty list "
            "(set in MODEL_CONFIG['universe'] or family frontmatter)"
        )

    manifest = {
        "schema_version": 1,
        "family_id": family_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "alpha_forge_commit": alpha_forge_commit or "unknown",
        "universe": list(universe),
        "timeframe": timeframe,
        "min_history_bars": infer_min_history_bars(config),
        "fee_rate": float(family_meta.get("fee_rate", 0.001)),
        "slippage_bps": float(family_meta.get("slippage_bps", 5.0)),
        "expected_metrics": _extract_expected_metrics(family_meta),
        "code_hash_sha256": _compute_code_hash(stage_dir),
    }
    (stage_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return stage_dir


def _extract_expected_metrics(meta: dict[str, Any]) -> dict[str, float]:
    """Pull a few well-known metric keys from FAMILY.md frontmatter if present.

    The bundle only carries these for downstream comparison (the trader's
    auto-promotion checker uses them). Missing keys are simply omitted.
    """
    keys = (
        "validation_sharpe", "holdout_sharpe", "max_drawdown",
        "avg_trades_per_month", "best_score",
    )
    return {k: float(meta[k]) for k in keys if isinstance(meta.get(k), (int, float))}


def archive_bundle(stage_dir: Path, archive_path: Path) -> Path:
    """Tar.gz a staged bundle directory."""
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(stage_dir, arcname=stage_dir.name)
    return archive_path


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="export_strategy",
        description="Export an alpha-forge family as an alpha-trader bundle",
    )
    ap.add_argument("family_id", help="family directory name under <workspace>/families/")
    ap.add_argument("--workspace", default="alpha_research",
                    help="workspace root (default: alpha_research)")
    ap.add_argument("--output-dir", default="bundles",
                    help="staging directory for the bundle")
    ap.add_argument("--archive", help="also write a .tar.gz archive at this path")
    ap.add_argument("--alpha-forge-commit", help="override the embedded git sha")
    args = ap.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    family_dir, family_meta = load_family(workspace, args.family_id)

    commit = args.alpha_forge_commit
    if not commit:
        # Best-effort git sha; ignore failures (e.g. when running outside a checkout).
        try:
            import subprocess
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=str(workspace.parent),
                stderr=subprocess.DEVNULL,
            ).decode().strip()
        except Exception:
            commit = None

    stage = build_bundle(
        family_id=args.family_id,
        family_dir=family_dir,
        family_meta=family_meta,
        output_dir=output_dir,
        alpha_forge_commit=commit,
    )
    print(f"bundle staged: {stage}")

    if args.archive:
        archive_path = Path(args.archive).expanduser().resolve()
        archive_bundle(stage, archive_path)
        print(f"archive written: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
