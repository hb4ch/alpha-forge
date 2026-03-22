"""Bridge between alpha-forge research/ files and crypto-pegasus Strategy interface.

Dynamically imports a family's research modules and composes them into
a Strategy that crypto-pegasus can backtest.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
from pegasus.strategy.base import Strategy


def _load_module(name: str, path: Path) -> ModuleType:
    """Dynamically load a Python module from a file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    # Don't pollute sys.modules with family-specific modules
    spec.loader.exec_module(module)
    return module


class ResearchStrategy(Strategy):
    """Strategy that composes a family's research/ modules.

    Loads features.py, model_config.py, and signal_combiner.py from
    the family's research directory and chains them into generate_signals().
    """

    def __init__(self, research_dir: str | Path, params: dict | None = None) -> None:
        super().__init__(params=params)
        self.research_dir = Path(research_dir).resolve()
        self._features_mod = _load_module("features", self.research_dir / "features.py")
        self._config_mod = _load_module("model_config", self.research_dir / "model_config.py")
        self._combiner_mod = _load_module("signal_combiner", self.research_dir / "signal_combiner.py")

    def generate_signals(self, bars: pd.DataFrame) -> pd.Series:
        """Generate signals by chaining features -> combiner."""
        features = self._features_mod.compute_features(bars)
        config = self._config_mod.MODEL_CONFIG
        return self._combiner_mod.combine_signals(features, config)

    @property
    def name(self) -> str:
        return f"ResearchStrategy({self.research_dir.parent.name})"
