"""paper_runner.run_paper_forward — unit tests with mocked subprocess +
DataProvider. Covers idempotency, timeout, non-zero exit, malformed
result.json, no-bars, and the happy path.
"""
from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from alpha_forge.engine import paper_runner
from alpha_forge.engine.paper_runner import (
    PaperResult,
    resolve_alpha_trader_home,
    run_paper_forward,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def trader_home(tmp_path: Path, monkeypatch) -> Path:
    """Synthetic alpha-trader checkout dir + ALPHA_TRADER_HOME env var."""
    home = tmp_path / "alpha-trader"
    home.mkdir()
    (home / "alpha_trader").mkdir()
    (home / "alpha_trader" / "main.py").write_text("# stub\n")
    monkeypatch.setenv("ALPHA_TRADER_HOME", str(home))
    return home


def _bars(n: int) -> pd.DataFrame:
    """Sample bar DataFrame mimicking pegasus.DataProvider.get_bars output."""
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    idx = [base.replace(hour=i % 24, day=1 + i // 24) for i in range(n)]
    return pd.DataFrame({
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.0 + i for i in range(n)],
        "volume": [1000.0] * n,
        "buy_volume": [500.0] * n,
        "vwap": [100.0 + i for i in range(n)],
        "trade_count": [10] * n,
    }, index=pd.DatetimeIndex(idx, name="bar_time"))


class _MockProvider:
    """Stand-in for pegasus.DataProvider used in tests."""

    def __init__(self, bars_by_symbol: dict[str, pd.DataFrame] | None = None):
        self._bars = bars_by_symbol or {}

    def get_bars(self, symbol, start, end, timeframe="5min"):
        return self._bars.get(symbol, pd.DataFrame())


@pytest.fixture
def provider_factory():
    """Provide a context-manager factory yielding a controllable mock."""
    holder = {"bars_by_symbol": {"ETHUSDT": _bars(20), "BTCUSDT": _bars(20)}}

    @contextmanager
    def factory():
        yield _MockProvider(holder["bars_by_symbol"])

    factory.holder = holder  # type: ignore[attr-defined]
    return factory


# ----------------------------------------------------------------------
# Idempotency
# ----------------------------------------------------------------------


def test_idempotency_short_circuits_on_existing_result(
    tmp_path: Path, trader_home: Path, provider_factory
):
    """If result.json already exists, the runner returns it without running
    a subprocess."""
    output = tmp_path / "run"
    output.mkdir()
    (output / "result.json").write_text(json.dumps({
        "verdict": "PASS", "reasons": [],
        "metrics": {"sharpe": 1.5}, "schema_version": 1,
    }))
    # subprocess.run NOT monkey-patched — if called, it would try to
    # actually execute the stub trader which doesn't exist, so any call
    # would fail. The fact that we get a clean PASS verdict means it wasn't.
    result = run_paper_forward(
        family_id="x", bundle_path=tmp_path / "bundle",
        output_dir=output,
        paper_window=(datetime(2026, 4, 1, tzinfo=timezone.utc),
                      datetime(2026, 4, 2, tzinfo=timezone.utc)),
        universe=["ETHUSDT"], timeframe="1h",
        bar_provider_factory=provider_factory,
    )
    assert result.passed
    assert result.metrics == {"sharpe": 1.5}


# ----------------------------------------------------------------------
# Timeout / subprocess failure
# ----------------------------------------------------------------------


def test_timeout_returns_synthetic_fail(
    tmp_path: Path, trader_home: Path, provider_factory, monkeypatch
):
    output = tmp_path / "run"

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="alpha_trader", timeout=1)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_paper_forward(
        family_id="x", bundle_path=tmp_path / "bundle",
        output_dir=output,
        paper_window=(datetime(2026, 4, 1, tzinfo=timezone.utc),
                      datetime(2026, 4, 2, tzinfo=timezone.utc)),
        universe=["ETHUSDT"], timeframe="1h",
        timeout_seconds=1,
        bar_provider_factory=provider_factory,
    )
    assert not result.passed
    assert result.reasons == ["timeout"]
    # Synthetic result.json should be persisted for idempotency on retry
    assert (output / "result.json").exists()


def test_nonzero_exit_returns_synthetic_fail(
    tmp_path: Path, trader_home: Path, provider_factory, monkeypatch
):
    output = tmp_path / "run"

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0], returncode=2,
            stdout="", stderr="ERROR: bundle missing\nTraceback\n",
        )
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_paper_forward(
        family_id="x", bundle_path=tmp_path / "bundle",
        output_dir=output,
        paper_window=(datetime(2026, 4, 1, tzinfo=timezone.utc),
                      datetime(2026, 4, 2, tzinfo=timezone.utc)),
        universe=["ETHUSDT"], timeframe="1h",
        bar_provider_factory=provider_factory,
    )
    assert not result.passed
    assert any("subprocess_exit_2" in r for r in result.reasons)
    # stderr is logged
    assert (output / "trader.stderr.log").read_text().startswith("ERROR")


def test_no_bars_in_window_returns_synthetic_fail(
    tmp_path: Path, trader_home: Path
):
    """Empty universe-bars → no_bars_in_window reason."""
    output = tmp_path / "run"

    @contextmanager
    def empty_factory():
        yield _MockProvider({})

    result = run_paper_forward(
        family_id="x", bundle_path=tmp_path / "bundle",
        output_dir=output,
        paper_window=(datetime(2026, 4, 1, tzinfo=timezone.utc),
                      datetime(2026, 4, 2, tzinfo=timezone.utc)),
        universe=["ETHUSDT"], timeframe="1h",
        bar_provider_factory=empty_factory,
    )
    assert not result.passed
    assert any("no_bars_in_window" in r for r in result.reasons)


def test_malformed_result_json_returns_synthetic_fail(
    tmp_path: Path, trader_home: Path, provider_factory, monkeypatch
):
    output = tmp_path / "run"

    def fake_run(*args, **kwargs):
        # Subprocess "succeeds" but writes a result.json missing 'verdict'
        out_dir = Path(args[0][args[0].index("--output-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.json").write_text(json.dumps({"oops": "no verdict"}))
        return subprocess.CompletedProcess(args=args[0], returncode=0,
                                            stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_paper_forward(
        family_id="x", bundle_path=tmp_path / "bundle",
        output_dir=output,
        paper_window=(datetime(2026, 4, 1, tzinfo=timezone.utc),
                      datetime(2026, 4, 2, tzinfo=timezone.utc)),
        universe=["ETHUSDT"], timeframe="1h",
        bar_provider_factory=provider_factory,
    )
    assert not result.passed
    assert any("malformed_result_json" in r for r in result.reasons)


def test_missing_result_json_returns_synthetic_fail(
    tmp_path: Path, trader_home: Path, provider_factory, monkeypatch
):
    output = tmp_path / "run"

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0,
                                            stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_paper_forward(
        family_id="x", bundle_path=tmp_path / "bundle",
        output_dir=output,
        paper_window=(datetime(2026, 4, 1, tzinfo=timezone.utc),
                      datetime(2026, 4, 2, tzinfo=timezone.utc)),
        universe=["ETHUSDT"], timeframe="1h",
        bar_provider_factory=provider_factory,
    )
    assert not result.passed
    assert any("missing_result_json" in r for r in result.reasons)


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------


def test_happy_path_returns_pass_verdict(
    tmp_path: Path, trader_home: Path, provider_factory, monkeypatch
):
    """Subprocess runs cleanly, writes a passing result.json → PaperResult.passed=True."""
    output = tmp_path / "run"
    captured: dict[str, Any] = {}

    def fake_run(*args, **kwargs):
        captured["cmd"] = args[0]
        captured["cwd"] = kwargs.get("cwd")
        captured["timeout"] = kwargs.get("timeout")
        out_dir = Path(args[0][args[0].index("--output-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.json").write_text(json.dumps({
            "schema_version": 1, "verdict": "PASS", "reasons": [],
            "metrics": {"sharpe": 1.7, "max_drawdown": 0.04,
                        "trade_count": 5, "net_return": 0.08},
            "bars_processed": 40, "expected_bars": 40,
        }))
        return subprocess.CompletedProcess(args=args[0], returncode=0,
                                            stdout="ok\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_paper_forward(
        family_id="eth_v1", bundle_path=tmp_path / "bundle",
        output_dir=output,
        paper_window=(datetime(2026, 4, 1, tzinfo=timezone.utc),
                      datetime(2026, 4, 2, tzinfo=timezone.utc)),
        universe=["ETHUSDT"], timeframe="1h",
        timeout_seconds=999,
        bar_provider_factory=provider_factory,
    )
    assert result.passed
    assert result.metrics["sharpe"] == 1.7
    assert result.reasons == []
    # Subprocess invocation includes the right flags
    assert "--mode" in captured["cmd"] and "paper" in captured["cmd"]
    assert "--feed" in captured["cmd"] and "replay" in captured["cmd"]
    assert captured["cwd"] == str(trader_home)
    assert captured["timeout"] == 999


def test_replay_parquet_has_expected_schema(
    tmp_path: Path, trader_home: Path, provider_factory, monkeypatch
):
    """Verify the parquet handed to the trader has the columns ReplayFeed
    expects (ts, symbol, open, high, low, close, volume, ...)."""
    output = tmp_path / "run"
    seen_parquet: dict[str, Path] = {}

    def fake_run(*args, **kwargs):
        idx = args[0].index("--replay-bars")
        seen_parquet["path"] = Path(args[0][idx + 1])
        out_dir = Path(args[0][args[0].index("--output-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.json").write_text(json.dumps({"verdict": "PASS"}))
        return subprocess.CompletedProcess(args=args[0], returncode=0,
                                            stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    run_paper_forward(
        family_id="x", bundle_path=tmp_path / "bundle",
        output_dir=output,
        paper_window=(datetime(2026, 4, 1, tzinfo=timezone.utc),
                      datetime(2026, 4, 2, tzinfo=timezone.utc)),
        universe=["ETHUSDT", "BTCUSDT"], timeframe="1h",
        bar_provider_factory=provider_factory,
    )
    df = pd.read_parquet(seen_parquet["path"])
    required = {"ts", "symbol", "open", "high", "low", "close",
                "volume", "buy_volume", "vwap", "trade_count"}
    assert required.issubset(df.columns)
    assert set(df["symbol"].unique()) == {"ETHUSDT", "BTCUSDT"}


# ----------------------------------------------------------------------
# resolve_alpha_trader_home
# ----------------------------------------------------------------------


def test_resolve_explicit_override(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALPHA_TRADER_HOME", raising=False)
    home = tmp_path / "elsewhere"
    home.mkdir()
    assert resolve_alpha_trader_home(home) == home


def test_resolve_env_var(tmp_path: Path, monkeypatch):
    home = tmp_path / "via-env"
    home.mkdir()
    monkeypatch.setenv("ALPHA_TRADER_HOME", str(home))
    assert resolve_alpha_trader_home(None) == home


def test_resolve_env_var_missing_path_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALPHA_TRADER_HOME", str(tmp_path / "nope"))
    with pytest.raises(FileNotFoundError):
        resolve_alpha_trader_home(None)


def test_resolve_default_path_missing_raises(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALPHA_TRADER_HOME", raising=False)
    monkeypatch.setattr(
        Path, "expanduser",
        lambda self: tmp_path / "nope" if str(self) == "~/alpha-trader" else self,
    )
    with pytest.raises(FileNotFoundError):
        resolve_alpha_trader_home(None)
