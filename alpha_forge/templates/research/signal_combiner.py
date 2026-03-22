"""Signal combination module.

This file is part of the editable research surface.
Implement combine_signals() to produce trading signals from features.
"""

from __future__ import annotations

import pandas as pd


def combine_signals(features: pd.DataFrame, config: dict) -> pd.Series:
    """Combine features into trading signals.

    Args:
        features: DataFrame with OHLCV + derived feature columns.
        config: Configuration dict from model_config.py.

    Returns:
        Series with values:
            1.0 = fully long
           -1.0 = fully short
            0.0 = flat
            NaN = no signal (warmup period)
    """
    # TODO: Implement signal generation logic
    return pd.Series(0.0, index=features.index)
