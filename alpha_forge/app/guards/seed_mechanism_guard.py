"""Seed-mechanism fidelity guard.

Detects when a seed/family explicitly names ``MultiSourceProvider`` or one of
its alt-data getter methods, but the submitted research code never imports
the provider or calls those methods. Catches two real failure modes from
prior runs:

1. **Silent fallback** (eth_momentum_v2): researcher writes
   ``if alt_data in bars.columns: <correct path> else: <neutral constants>``
   where the ``in bars.columns`` check is always False (bars are OHLCV-only),
   so the constant fallback always fires. All judges approve because each
   branch is internally clean.

2. **Proxy substitution** (funding_rate v1): researcher reads the seed's
   claim that mandates ``MultiSourceProvider.get_funding_rate(...)`` and
   substitutes a "proxy" (e.g. ``buy_volume / volume``) computed only from
   bars columns, never importing the provider. Judges approve because the
   proxy is internally valid; nobody verifies it matches the seed's
   declared mechanism.

The guard is intentionally narrow: it only fires when the seed/family text
contains an explicit reference to ``MultiSourceProvider`` (the class name)
or one of the six getter method names. Generic mentions of "funding rate"
or "TVL" do not trigger.
"""

from __future__ import annotations

import re
from pathlib import Path

from alpha_forge.app.domain.models import GuardResult, IdeaFamily, SeedCard


# Method names exposed by MultiSourceProvider. Adding a new alt-data method?
# Add it here too.
PROVIDER_METHODS: tuple[str, ...] = (
    "get_funding_rate",
    "get_open_interest",
    "get_chain_tvl",
    "get_protocol_tvl",
    "get_dex_volume",
    "get_stablecoin",
)

PROVIDER_CLASS_NAME = "MultiSourceProvider"
PROVIDER_IMPORT_MODULE = "pegasus.data.multi_source"

_METHOD_REGEX = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in PROVIDER_METHODS) + r")\b"
)

# Map structured ``SeedCard.required_data`` entries to the provider method
# they imply. The distiller emits these as snake-case identifiers; we match
# substrings to tolerate minor variants (e.g. "funding_rate_eth" → funding_rate).
DATA_KEY_TO_METHOD: dict[str, str] = {
    "funding_rate":  "get_funding_rate",
    "funding rate":  "get_funding_rate",
    "open_interest": "get_open_interest",
    "open interest": "get_open_interest",
    "chain_tvl":     "get_chain_tvl",
    "chain tvl":     "get_chain_tvl",
    "protocol_tvl":  "get_protocol_tvl",
    "protocol tvl":  "get_protocol_tvl",
    "dex_volume":    "get_dex_volume",
    "dex volume":    "get_dex_volume",
    "stablecoin":    "get_stablecoin",
}


def _collect_seed_text(seed_card: SeedCard | None, family: IdeaFamily) -> str:
    """Concatenate all the natural-language fields the researcher should be
    matching against. Returns an empty string when no seed is available."""
    parts: list[str] = [
        family.base_hypothesis or "",
        family.mechanism or "",
        family.fork_reason or "",
    ]
    if seed_card is not None:
        parts.extend([
            seed_card.raw_claim or "",
            seed_card.testable_hypothesis or "",
            seed_card.mechanism or "",
        ])
    return "\n".join(parts)


def _seed_declares_provider(
    seed_text: str,
    required_data: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """Return (is_declared, methods_named).

    A declaration is any of:
    - the literal class name ``MultiSourceProvider`` in seed text
    - a reference to one of the getter method names in seed text
    - a ``required_data`` entry naming an alt-data source (funding_rate,
      chain_tvl, etc.) — this is the structured signal the distiller emits

    methods_named lists the implied provider method calls; surfaced in the
    violation message so the researcher knows what was claimed.
    """
    declared = PROVIDER_CLASS_NAME in seed_text
    methods_named = list(_METHOD_REGEX.findall(seed_text))

    for entry in (required_data or []):
        norm = entry.lower()
        for key, method in DATA_KEY_TO_METHOD.items():
            if key in norm:
                methods_named.append(method)
                break

    methods_named = list(dict.fromkeys(methods_named))
    return (declared or bool(methods_named)), methods_named


def _code_uses_provider(research_dir: Path) -> tuple[bool, bool]:
    """Return (imports_provider, calls_any_method).

    Implementation note: regex over the source text is sufficient and avoids
    AST overhead. The import line and method calls have a fixed shape that
    doesn't get obscured by formatting.
    """
    if not research_dir.exists():
        return False, False

    imports = False
    calls = False
    for py in research_dir.glob("*.py"):
        text = py.read_text()
        # Match either:
        #   from pegasus.data.multi_source import ...
        #   import pegasus.data.multi_source ...
        if re.search(rf"(from\s+{re.escape(PROVIDER_IMPORT_MODULE)}\s+import"
                     rf"|import\s+{re.escape(PROVIDER_IMPORT_MODULE)}\b)", text):
            imports = True
        # Match a call to one of the getter methods
        if re.search(rf"\.(?:" + "|".join(PROVIDER_METHODS) + r")\s*\(", text):
            calls = True
    return imports, calls


def check_seed_mechanism(
    family_dir: Path,
    seed_card: SeedCard | None,
    family: IdeaFamily,
) -> GuardResult:
    """Verify research code uses MultiSourceProvider when the seed/family
    declares one of its alt-data getters as the mechanism.

    Skips silently when the seed makes no provider claim — generic seeds
    that don't involve alt-data are unaffected.
    """
    seed_text = _collect_seed_text(seed_card, family)
    required_data = seed_card.required_data if seed_card else None
    declared, methods_named = _seed_declares_provider(seed_text, required_data)

    if not declared:
        return GuardResult(
            guard_name="seed_mechanism",
            passed=True,
            violations=[],
        )

    research_dir = family_dir / "research"
    imports, calls = _code_uses_provider(research_dir)

    violations: list[str] = []
    if not imports:
        violations.append(
            f"Seed/family declares MultiSourceProvider usage "
            f"(methods: {methods_named or [PROVIDER_CLASS_NAME]}) but no "
            f"research/*.py imports `from {PROVIDER_IMPORT_MODULE} import "
            f"{PROVIDER_CLASS_NAME}`. Likely silent fallback or proxy "
            f"substitution — see eth_momentum_v2 / funding_rate_v1 lessons."
        )
    elif not calls:
        violations.append(
            f"Seed/family declares MultiSourceProvider usage but research "
            f"code imports the class without calling any getter method "
            f"({list(PROVIDER_METHODS)}). The provider is not actually "
            f"used; results will not test the seed mechanism."
        )

    return GuardResult(
        guard_name="seed_mechanism",
        passed=not violations,
        violations=violations,
        is_red_strike=bool(violations),
    )
