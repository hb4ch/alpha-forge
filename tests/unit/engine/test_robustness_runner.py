"""Regression tests for robustness battery behavior."""
from __future__ import annotations

from pathlib import Path

from alpha_forge.engine.robustness_runner import run_robustness_battery
from tests.conftest import make_result


def test_sub_period_stability_uses_distinct_windows(
    markdown_store,
    tmp_path: Path,
    monkeypatch,
) -> None:
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "costs.yaml").write_text("fee_rate: 0.001\nslippage_bps: 5\n")
    (configs_dir / "guardrails.yaml").write_text("{}\n")
    (configs_dir / "splits.yaml").write_text(
        "validation:\n  start: 2024-01-01\n  end: 2024-01-10\n"
    )
    (configs_dir / "universe.yaml").write_text("symbols:\n  - BTCUSDT\n")

    calls: list[tuple[str | None, str | None]] = []

    def fake_run_backtest(*args, **kwargs):
        calls.append((kwargs.get("start_override"), kwargs.get("end_override")))
        return [make_result()]

    monkeypatch.setattr("alpha_forge.engine.robustness_runner.run_backtest", fake_run_backtest)

    run_robustness_battery(
        "fam_001",
        markdown_store,
        baseline_results=[make_result()],
        configs_dir=configs_dir,
    )

    window_calls = [call for call in calls if call[0] is not None]
    assert len(window_calls) == 3
    assert len(set(window_calls)) == 3
