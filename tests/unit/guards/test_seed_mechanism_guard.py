"""Tests for the seed-mechanism fidelity guard.

These tests cover the failure modes that motivated the guard:

- v2 silent fallback (claim names provider, code reads bars columns + falls
  through to constants when the column doesn't exist)
- funding_rate v1 proxy substitution (claim names provider, code computes a
  proxy from bars and never imports the provider)
"""
from __future__ import annotations

from pathlib import Path

from alpha_forge.app.domain.models import IdeaFamily, SeedCard
from alpha_forge.app.guards.seed_mechanism_guard import check_seed_mechanism
from tests.conftest import make_family


def _write_research_file(family_dir: Path, filename: str, content: str) -> None:
    research_dir = family_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    (research_dir / filename).write_text(content)


def _seed(
    claim: str = "",
    hypothesis: str = "",
    mechanism: str = "",
    required_data: list[str] | None = None,
) -> SeedCard:
    return SeedCard(
        seed_id="seed_test",
        seed_type="paper",
        source_title="src",
        raw_claim=claim,
        market="crypto_spot",
        horizon="1d",
        mechanism=mechanism,
        testable_hypothesis=hypothesis,
        required_data=required_data or [],
    )


class TestSeedMechanismGuard:
    def test_passes_when_seed_makes_no_provider_claim(self, tmp_path: Path) -> None:
        """A seed that doesn't mention MultiSourceProvider should pass even
        with no provider import in the code — most strategies are OHLCV-only."""
        family = make_family(family_id="fam_no_claim", base_hypothesis="RSI momentum on ETH")
        seed = _seed(claim="RSI > 70 → momentum continuation", mechanism="overbought continuation")
        _write_research_file(tmp_path / "fam_no_claim", "features.py",
                             "import pandas as pd\ndef compute_features(bars):\n    return bars\n")
        result = check_seed_mechanism(tmp_path / "fam_no_claim", seed, family)
        assert result.passed
        assert result.violations == []

    def test_passes_when_provider_used_as_declared(self, tmp_path: Path) -> None:
        """Happy path: seed names provider, code imports + calls it."""
        family = make_family(
            family_id="fam_ok",
            base_hypothesis="Use MultiSourceProvider.get_funding_rate to detect positioning regimes",
        )
        seed = _seed(claim="MultiSourceProvider.get_funding_rate('ETHUSDT', start, end)")
        _write_research_file(
            tmp_path / "fam_ok", "features.py",
            "from pegasus.data.multi_source import MultiSourceProvider\n"
            "def compute_features(bars):\n"
            "    with MultiSourceProvider() as p:\n"
            "        fr = p.get_funding_rate('ETHUSDT', bars.index.min(), bars.index.max())\n"
            "    return bars\n",
        )
        result = check_seed_mechanism(tmp_path / "fam_ok", seed, family)
        assert result.passed, result.violations

    def test_detects_proxy_substitution(self, tmp_path: Path) -> None:
        """funding_rate v1 pattern: seed mandates get_funding_rate, code computes
        a proxy from bars columns and never imports the provider."""
        family = make_family(
            family_id="fam_proxy",
            base_hypothesis=(
                "Use MultiSourceProvider.get_funding_rate('ETHUSDT', start, end) "
                "to compute a 90-day z-score of funding rate"
            ),
        )
        seed = _seed(mechanism="funding rate via get_funding_rate, lagged + z-scored")
        _write_research_file(
            tmp_path / "fam_proxy", "features.py",
            "import pandas as pd\n"
            "def compute_features(bars):\n"
            "    # 'proxy for funding rate sentiment'\n"
            "    buy_ratio = bars['buy_volume'] / bars['volume']\n"
            "    return bars.assign(buy_z=buy_ratio.rolling(90).mean())\n",
        )
        result = check_seed_mechanism(tmp_path / "fam_proxy", seed, family)
        assert not result.passed
        assert any("get_funding_rate" in v for v in result.violations)
        assert result.is_red_strike

    def test_detects_silent_fallback_no_import(self, tmp_path: Path) -> None:
        """v2 pattern: seed names provider, code uses bars columns inside an
        ``if has_alt_data: ... else: <fallback>`` where the if-branch is dead."""
        family = make_family(
            family_id="fam_fallback",
            base_hypothesis="MultiSourceProvider.get_chain_tvl + get_protocol_tvl as overlay",
        )
        seed = _seed(claim="MultiSourceProvider get_chain_tvl + get_protocol_tvl + get_funding_rate")
        _write_research_file(
            tmp_path / "fam_fallback", "features.py",
            "import pandas as pd\n"
            "def compute_features(bars):\n"
            "    if 'eth_tvl' in bars.columns:\n"
            "        # never fires — bars are OHLCV-only\n"
            "        return bars[['eth_tvl']]\n"
            "    return pd.DataFrame({'eth_tvl_healthy': 1.0}, index=bars.index)\n",
        )
        result = check_seed_mechanism(tmp_path / "fam_fallback", seed, family)
        assert not result.passed
        assert any("MultiSourceProvider" in v or "get_chain_tvl" in v for v in result.violations)

    def test_detects_import_without_call(self, tmp_path: Path) -> None:
        """Defensive: importing the provider but never calling a getter is also
        a violation — the import is dead, the seed mechanism isn't tested."""
        family = make_family(
            family_id="fam_import_only",
            base_hypothesis="MultiSourceProvider.get_dex_volume comparison signal",
        )
        seed = _seed(mechanism="get_dex_volume on Ethereum")
        _write_research_file(
            tmp_path / "fam_import_only", "features.py",
            "from pegasus.data.multi_source import MultiSourceProvider\n"
            "def compute_features(bars):\n"
            "    return bars[['close']]\n",
        )
        result = check_seed_mechanism(tmp_path / "fam_import_only", seed, family)
        assert not result.passed
        assert any("not actually used" in v.lower() or "without calling" in v.lower() for v in result.violations)

    def test_passes_when_method_named_in_family_only(self, tmp_path: Path) -> None:
        """Seed claim is generic but family.base_hypothesis names the method →
        the guard still fires because text aggregation includes family fields."""
        family = make_family(
            family_id="fam_family_only",
            base_hypothesis="Long when get_funding_rate z-score > 1.5",
            mechanism="positioning continuation via funding rate",
        )
        seed = _seed(claim="generic positioning study")
        _write_research_file(
            tmp_path / "fam_family_only", "features.py",
            "from pegasus.data.multi_source import MultiSourceProvider\n"
            "def compute_features(bars):\n"
            "    with MultiSourceProvider() as p:\n"
            "        fr = p.get_funding_rate('ETHUSDT', bars.index.min(), bars.index.max())\n"
            "    return bars\n",
        )
        result = check_seed_mechanism(tmp_path / "fam_family_only", seed, family)
        assert result.passed, result.violations

    def test_handles_no_seed_card(self, tmp_path: Path) -> None:
        """Seed file deleted/missing → guard still runs against family fields,
        passes if the family doesn't reference the provider either."""
        family = make_family(family_id="fam_no_seed", base_hypothesis="generic OHLCV momentum")
        _write_research_file(tmp_path / "fam_no_seed", "features.py",
                             "def compute_features(bars):\n    return bars\n")
        result = check_seed_mechanism(tmp_path / "fam_no_seed", None, family)
        assert result.passed

    def test_required_data_alone_triggers_guard(self, tmp_path: Path) -> None:
        """Real failure mode discovered live: the seed-judge distillation strips
        the literal MultiSourceProvider mandate from the claim text but preserves
        ``required_data: ['funding_rate', ...]``. The guard must trigger from
        the structured field even when prose-level mentions are absent."""
        family = make_family(
            family_id="fam_required_only",
            base_hypothesis="Long-short on 90d z-score of daily-mean funding rate",
            mechanism="Slow positioning regime indicator from funding history.",
        )
        # Notice: NO mention of MultiSourceProvider or get_funding_rate anywhere
        # in claim/mechanism/hypothesis prose — only the structured field.
        seed = _seed(
            claim="Extreme z-scores of 90d daily-mean funding rate predict ETH continuation.",
            mechanism="Sustained positioning regime continues, doesn't fade.",
            required_data=["OHLCV", "funding_rate", "realized_vol_20d"],
        )
        _write_research_file(
            tmp_path / "fam_required_only", "features.py",
            "import pandas as pd\n"
            "def compute_features(bars):\n"
            "    # 'proxy for funding rate' from bars columns — same trap as iter_1\n"
            "    buy_ratio = bars['buy_volume'] / bars['volume']\n"
            "    return bars.assign(z=buy_ratio.rolling(90).mean())\n",
        )
        result = check_seed_mechanism(tmp_path / "fam_required_only", seed, family)
        assert not result.passed, "guard must catch proxy substitution when required_data names funding_rate"
        assert any("get_funding_rate" in v for v in result.violations)

    def test_handles_missing_research_dir(self, tmp_path: Path) -> None:
        """Edge case: family dir exists but research/ doesn't yet (pre-iter_1).
        Guard should fail loudly when seed claims provider but no code exists."""
        family = make_family(
            family_id="fam_no_code",
            base_hypothesis="MultiSourceProvider.get_funding_rate is the core signal",
        )
        seed = _seed()
        (tmp_path / "fam_no_code").mkdir()
        result = check_seed_mechanism(tmp_path / "fam_no_code", seed, family)
        assert not result.passed
