"""Label computation module (optional for v1).

This file is part of the editable research surface.
For ML-based approaches, implement compute_labels() to define targets.
"""

from __future__ import annotations

import pandas as pd


def compute_labels(bars: pd.DataFrame) -> pd.Series:
    """Compute labels/targets from OHLCV bars.

    Args:
        bars: DataFrame with OHLCV columns indexed by datetime.

    Returns:
        Series of labels aligned to the bars index.
    """
    # TODO: Implement label computation if using ML-based approach
    return pd.Series(0.0, index=bars.index)
