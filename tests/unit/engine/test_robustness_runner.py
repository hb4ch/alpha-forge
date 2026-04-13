"""Regression tests for robustness battery behavior."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from alpha_forge.engine.robustness_runner import run_robustness_battery
from tests.conftest import make_result


def test_sub_period_stability_uses_distinct_windows(
    markdown_store,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Sub-period stability splits the equity curve into 3 windows and
    computes a Sharpe for each.  Passes when >=2 windows are positive
    and the Sharpe std across windows is < 1.0."""
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "costs.yaml").write_text("fee_rate: 0.001\nslippage_bps: 5\n")
    (configs_dir / "guardrails.yaml").write_text("{}\n")
    (configs_dir / "splits.yaml").write_text(
        "train:\n  start: 2023-06-01\n  end: 2023-12-31\n"
        "validation:\n  start: 2024-01-01\n  end: 2024-01-31\n"
    )
    (configs_dir / "universe.yaml").write_text("symbols:\n  - BTCUSDT\n")

    # Create a family research dir with a minimal model_config
    family_dir = markdown_store.root / "families" / "fam_001" / "research"
    family_dir.mkdir(parents=True, exist_ok=True)
    (family_dir / "model_config.py").write_text(
        'MODEL_CONFIG = {"timeframe": "1d", "universe": ["BTCUSDT"]}\n'
    )
    (family_dir / "features.py").write_text(
        "import pandas as pd\ndef compute_features(bars): return bars\n"
    )
    (family_dir / "signal_combiner.py").write_text(
        "import pandas as pd\ndef combine_signals(features, config): return pd.Series(0.0, index=features.index)\n"
    )

    # Build a fake equity curve spanning the validation window (30 days, 10 per window)
    dates = pd.date_range("2024-01-01", "2024-01-30", freq="D", tz="UTC")
    # Constant positive returns → identical Sharpe across windows, std ≈ 0
    returns = np.full(len(dates), 0.005)
    equity_curve = pd.DataFrame({"returns": returns}, index=dates)

    # Mock BacktestEngine so the sub-period code gets our equity curve
    fake_bt_result = SimpleNamespace(equity_curve=equity_curve)
    mock_engine = MagicMock()
    mock_engine.run.return_value = fake_bt_result

    FakeBacktestEngine = lambda strategy, config, **kw: mock_engine  # noqa: E731
    monkeypatch.setattr(
        "pegasus.engine.backtest.BacktestEngine",
        FakeBacktestEngine,
    )

    # Mock _build_config to return a config with the right shape
    fake_config = MagicMock()
    fake_config.timeframe = "1d"
    fake_config.model_copy.return_value = fake_config
    monkeypatch.setattr(
        "alpha_forge.engine.backtest_runner._build_config",
        lambda configs_path: (fake_config, None, None, ["BTCUSDT"]),
    )

    # Stub run_backtest for cost/slippage tests (not sub-period)
    monkeypatch.setattr(
        "alpha_forge.engine.robustness_runner.run_backtest",
        lambda *a, **kw: [make_result()],
    )

    result = run_robustness_battery(
        "fam_001",
        markdown_store,
        baseline_results=[make_result()],
        configs_dir=configs_dir,
    )

    # Find the sub-period stability test result
    sub_period = [t for t in result.tests if t.test_name == "sub_period_stability"]
    assert len(sub_period) == 1
    details = sub_period[0].details
    assert "window_sharpes" in details
    assert len(details["window_sharpes"]) == 3
    # All returns are mostly positive → all 3 windows should have positive Sharpe
    assert all(s > 0 for s in details["window_sharpes"])
    assert sub_period[0].passed is True
