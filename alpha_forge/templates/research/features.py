"""Feature computation module.

This file is part of the editable research surface.
Implement compute_features() to derive indicators from OHLCV bars.
"""

from __future__ import annotations

import pandas as pd


def compute_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute features from OHLCV bars.

    Args:
        bars: DataFrame with columns [open, high, low, close, volume, buy_volume, vwap, trade_count]
              indexed by datetime.

    Returns:
        DataFrame with original columns plus any derived feature columns.
    """
    features = bars.copy()
    # TODO: Implement feature computation
    # Example: features["sma_20"] = bars["close"].rolling(20).mean()
    return features
