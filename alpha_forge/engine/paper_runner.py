"""Paper-forward integration layer between alpha-forge and alpha-trader.

This module is the *only* code in alpha-forge that knows about the
paper-trading runtime. It does three things:

1. Pull bar data from pegasus.DataProvider for the paper-forward window
   (defaults to "everything after the holdout end") and write it to a
   parquet ReplayFeed can consume.
2. Spawn ``python -m alpha_trader.main`` as a subprocess, redirecting
   stdout/stderr to logs in the run's output directory.
3. Read the trader's ``result.json`` and translate it into a
   ``PaperResult`` for the orchestrator.

The wire surface between repos is:

- A *strategy bundle* directory (built by ``scripts/export_strategy.py``)
- A replay parquet at a known path
- A ``result.json`` file written by the trader

There are zero Python imports of ``alpha_trader.*`` here. Communication
is filesystem-only, matching the original "no shared imports between
repos" decision.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Trader location
# ----------------------------------------------------------------------


def resolve_alpha_trader_home(override: Optional[Path] = None) -> Path:
    """Resolve the alpha-trader checkout in this order: explicit override,
    ``$ALPHA_TRADER_HOME``, then ``~/alpha-trader``. Raises if none exist."""
    if override is not None:
        p = Path(override).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"alpha-trader override path missing: {p}")
        return p
    env = os.environ.get("ALPHA_TRADER_HOME")
    if env:
        p = Path(env).expanduser().resolve()
        if p.exists():
            return p
        raise FileNotFoundError(f"ALPHA_TRADER_HOME points to missing path: {p}")
    p = Path("~/alpha-trader").expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(
            "alpha-trader checkout not found. Set ALPHA_TRADER_HOME or "
            "place it at ~/alpha-trader"
        )
    return p


# ----------------------------------------------------------------------
# Result type
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class PaperResult:
    """Outcome of a single paper-forward run."""

    passed: bool
    reasons: list[str]
    metrics: dict[str, Any]
    raw: dict[str, Any]               # full result.json contents
    output_dir: Path

    @classmethod
    def from_result_json(cls, output_dir: Path, payload: dict) -> "PaperResult":
        verdict = payload.get("verdict", "FAIL")
        return cls(
            passed=(verdict == "PASS"),
            reasons=list(payload.get("reasons", [])),
            metrics=dict(payload.get("metrics", {})),
            raw=payload,
            output_dir=output_dir,
        )

    @classmethod
    def synthetic_fail(cls, output_dir: Path, reasons: list[str]) -> "PaperResult":
        """Build a PaperResult for an aborted/failed run that never produced
        a real result.json. Also writes a synthetic result.json so the
        artifact-presence idempotency check works on retry."""
        payload = {
            "schema_version": 1,
            "verdict": "FAIL",
            "reasons": reasons,
            "metrics": {},
            "synthetic": True,
            "as_of_utc": _utcnow().isoformat(),
        }
        result_path = output_dir / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, indent=2))
        return cls.from_result_json(output_dir, payload)


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def run_paper_forward(
    *,
    family_id: str,
    bundle_path: Path,
    output_dir: Path,
    paper_window: tuple[datetime, datetime],
    universe: list[str],
    timeframe: str,
    initial_capital: float = 100_000.0,
    fee_rate: float = 0.001,
    slippage_bps: float = 5.0,
    timeout_seconds: int = 1200,
    alpha_trader_home: Optional[Path] = None,
    bar_provider_factory=None,    # injectable for tests
) -> PaperResult:
    """Run a paper-forward sim end-to-end and return the trader's verdict.

    Idempotent: a pre-existing ``result.json`` in ``output_dir`` is
    returned without re-running the subprocess. Failure modes
    (subprocess crash, timeout, malformed result) collapse to a
    synthetic FAIL with a reason string.
    """
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Idempotency
    existing = _read_existing_result(output_dir)
    if existing is not None:
        logger.info(
            "paper_runner: reusing existing result.json (verdict=%s)",
            existing.get("verdict"),
        )
        return PaperResult.from_result_json(output_dir, existing)

    # Resolve trader home up-front so we fail fast if misconfigured.
    try:
        trader_home = resolve_alpha_trader_home(alpha_trader_home)
    except FileNotFoundError as e:
        logger.error("paper_runner: %s", e)
        return PaperResult.synthetic_fail(
            output_dir, [f"alpha_trader_not_found: {e}"]
        )

    # Build replay parquet
    parquet_path = output_dir / "replay_bars.parquet"
    try:
        _build_replay_parquet(
            parquet_path=parquet_path, universe=universe,
            timeframe=timeframe, paper_window=paper_window,
            provider_factory=bar_provider_factory,
        )
    except _NoBarsError as e:
        return PaperResult.synthetic_fail(output_dir, [f"no_bars_in_window: {e}"])
    except Exception as e:
        logger.exception("paper_runner: bar fetch failed")
        return PaperResult.synthetic_fail(output_dir, [f"bar_fetch_failed: {e!r}"])

    # Spawn the trader subprocess
    cmd = [
        sys.executable, "-m", "alpha_trader.main",
        "--bundle", str(bundle_path),
        "--output-dir", str(output_dir),
        "--mode", "paper",
        "--feed", "replay",
        "--replay-bars", str(parquet_path),
        "--initial-capital", str(initial_capital),
        "--fee-rate", str(fee_rate),
        "--slippage-bps", str(slippage_bps),
    ]
    stdout_log = output_dir / "trader.stdout.log"
    stderr_log = output_dir / "trader.stderr.log"
    logger.info("paper_runner: launching trader for family=%s, window=%s..%s",
                family_id, paper_window[0].isoformat(), paper_window[1].isoformat())

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(trader_home),
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        _write_logs(stdout_log, stderr_log, getattr(e, "stdout", None), getattr(e, "stderr", None))
        return PaperResult.synthetic_fail(output_dir, ["timeout"])
    except FileNotFoundError as e:
        return PaperResult.synthetic_fail(
            output_dir, [f"trader_executable_missing: {e}"]
        )

    _write_logs(stdout_log, stderr_log, proc.stdout, proc.stderr)

    if proc.returncode != 0:
        tail = (proc.stderr or "").splitlines()[-5:]
        return PaperResult.synthetic_fail(
            output_dir,
            [f"subprocess_exit_{proc.returncode}"] + [f"stderr: {line}" for line in tail],
        )

    # Parse the result.json the trader was supposed to write
    payload = _read_existing_result(output_dir)
    if payload is None:
        return PaperResult.synthetic_fail(output_dir, ["missing_result_json"])
    if "verdict" not in payload:
        return PaperResult.synthetic_fail(output_dir, ["malformed_result_json"])
    return PaperResult.from_result_json(output_dir, payload)


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


class _NoBarsError(RuntimeError):
    pass


def _build_replay_parquet(
    *,
    parquet_path: Path,
    universe: list[str],
    timeframe: str,
    paper_window: tuple[datetime, datetime],
    provider_factory=None,
) -> None:
    """Concat per-symbol bars into a single parquet ReplayFeed can read.

    Output schema: ``ts, symbol, open, high, low, close, volume, buy_volume,
    vwap, trade_count`` — matching alpha_trader.stages.feed.replay's expected
    columns. Bars come from pegasus.DataProvider.get_bars() unless a
    factory is injected (tests).
    """
    if provider_factory is None:
        provider_factory = _default_provider_factory
    start, end = paper_window
    frames: list[pd.DataFrame] = []
    with provider_factory() as provider:
        for sym in universe:
            df = provider.get_bars(
                sym, start.isoformat(), end.isoformat(), timeframe=timeframe,
            )
            if df is None or df.empty:
                continue
            df = df.copy()
            df = df.reset_index().rename(columns={"bar_time": "ts"})
            df["symbol"] = sym
            # Reorder columns for clarity
            cols = ["ts", "symbol"] + [c for c in df.columns if c not in ("ts", "symbol")]
            frames.append(df[cols])
    if not frames:
        raise _NoBarsError(
            f"no bars for universe={universe} window={paper_window} timeframe={timeframe}"
        )
    combined = pd.concat(frames, ignore_index=True)
    combined.to_parquet(parquet_path, index=False)


def _default_provider_factory():
    """Wrap the pegasus DataProvider in a context-manager-compatible holder."""
    return _ProviderHolder()


class _ProviderHolder:
    """Pegasus DataProvider doesn't itself support the context-manager
    protocol; wrap it so callers can ``with provider_factory() as provider``."""

    def __enter__(self):
        # Lazy import: pegasus is heavy + not always available in tests.
        from pegasus.config import BacktestConfig  # type: ignore
        from pegasus.data.provider import DataProvider  # type: ignore

        self._provider = DataProvider(BacktestConfig())
        return self._provider

    def __exit__(self, *_exc):
        # DataProvider.__exit__ would be ideal; otherwise just release.
        try:
            close = getattr(self._provider, "close", None)
            if callable(close):
                close()
        except Exception:
            pass


def _read_existing_result(output_dir: Path) -> Optional[dict]:
    path = output_dir / "result.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        logger.exception("paper_runner: result.json unreadable, ignoring")
        return None


def _write_logs(stdout_log: Path, stderr_log: Path,
                stdout: Optional[str], stderr: Optional[str]) -> None:
    if stdout:
        stdout_log.write_text(stdout)
    if stderr:
        stderr_log.write_text(stderr)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "PaperResult",
    "resolve_alpha_trader_home",
    "run_paper_forward",
]
