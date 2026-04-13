"""Seed intake, distillation, screening, and family creation flow."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alpha_forge.app.agents.judge_seed import SeedJudge
from alpha_forge.app.agents.llm_client import LLMClient
from alpha_forge.app.domain.events import FamilyEvent
from alpha_forge.app.domain.models import (
    AllowedMutations,
    IdeaFamily,
    SeedCard,
    SeedJudgeOutput,
)
from alpha_forge.app.domain.states import FamilyState, SeedVerdict
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.transitions import TransitionEngine

logger = logging.getLogger(__name__)


def _generate_seed_id(text: str) -> str:
    """Generate a short deterministic seed ID from raw text."""
    h = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"seed_{h}"


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "_", text)
    return text[:50]


def _next_family_id(store: MarkdownStore, mechanism: str) -> str:
    """Allocate the next available family ID for a mechanism slug."""
    slug = _slugify(mechanism)
    pattern = re.compile(rf"^{re.escape(slug)}_v(\d+)$")
    highest = 0
    for family_id in store.list_families():
        match = pattern.match(family_id)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{slug}_v{highest + 1}"


def ingest_seed(
    raw_text: str,
    source: str,
    store: MarkdownStore,
) -> str:
    """Ingest a raw seed into seeds/inbox/. Returns seed_id."""
    seed_id = _generate_seed_id(raw_text)
    seed = SeedCard(
        seed_id=seed_id,
        seed_type="raw",
        source_title=source,
        raw_claim=raw_text,
        market="",
        horizon="",
        mechanism="",
        testable_hypothesis="",
    )
    store.write_seed(seed, stage="inbox")
    logger.info("Ingested seed %s from %s", seed_id, source)
    return seed_id


def distill_seed(
    seed_id: str,
    store: MarkdownStore,
    client: LLMClient | None = None,
) -> SeedCard:
    """Distill a raw seed into a structured research card via LLM."""
    if client is None:
        from alpha_forge.app.agents.llm_config import get_client_for_role
        client = get_client_for_role("seed_judge")
    raw_seed = store.read_seed(seed_id, stage="inbox")

    system = """You are a crypto alpha research assistant. Your job is to convert a raw research idea into a structured seed card.

Respond with valid JSON matching this schema:
{
  "seed_id": "string (use the provided seed_id)",
  "seed_type": "paper | tweet | blog | forum | observation | mutation",
  "source_title": "string",
  "raw_claim": "string (original claim)",
  "market": "crypto_spot",
  "horizon": "string (e.g., 5min, 1h, 4h, 1d)",
  "mechanism": "string (why this should work)",
  "required_data": ["OHLCV", "volume", etc.],
  "testable_hypothesis": "string (specific, falsifiable)",
  "ambiguities": ["string"],
  "risk_flags": ["string"]
}"""

    user_prompt = f"""Distill this raw seed into a structured research card.

## Raw Seed
Source: {raw_seed.source_title}
Text: {raw_seed.raw_claim}

Seed ID: {seed_id}
"""

    data = client.call_json(system, user_prompt)
    data["seed_id"] = seed_id
    card = SeedCard.model_validate(data)

    store.write_seed(card, stage="distilled")
    store.delete_seed(seed_id, stage="inbox")
    logger.info("Distilled seed %s", seed_id)
    return card


def screen_seed(
    seed_id: str,
    store: MarkdownStore,
    judge: SeedJudge | None = None,
) -> SeedJudgeOutput:
    """Screen a distilled seed via the Seed Judge."""
    judge = judge or SeedJudge()
    card = store.read_seed(seed_id, stage="distilled")
    existing = store.list_families()

    verdict = judge.evaluate_seed(card, existing_families=existing)

    if verdict.verdict in (SeedVerdict.ACCEPT, SeedVerdict.ACCEPT_WITH_NARROWING):
        store.move_seed(seed_id, from_stage="distilled", to_stage="accepted")
    else:
        store.move_seed(seed_id, from_stage="distilled", to_stage="rejected")

    logger.info("Screened seed %s: %s", seed_id, verdict.verdict)
    return verdict


def create_family(
    seed_id: str,
    store: MarkdownStore,
    artifact_store: ArtifactStore,
    configs_dir: str | Path = "configs",
) -> IdeaFamily:
    """Create a new idea family from an accepted seed."""
    card = store.read_seed(seed_id, stage="accepted")

    family_id = _next_family_id(store, card.mechanism)

    # Build allowed mutations from the seed card
    allowed = AllowedMutations(
        horizon=[card.horizon] if card.horizon else [],
        venue=["binance"],
        representation=["raw"],
    )

    family = IdeaFamily(
        family_id=family_id,
        seed_id=seed_id,
        base_hypothesis=card.testable_hypothesis,
        mechanism=card.mechanism,
        allowed_mutations=allowed,
        state=FamilyState.NEW,
    )

    # Write family files
    store.write_family(family)
    store.append_history(family_id, f"Family created from seed {seed_id}")
    store.write_mutation_policy(family_id, {
        "budget": family.mutation_budget.model_dump(),
        "allowed_mutations": allowed.model_dump(),
    })

    # Create research directory with stubs
    _create_research_stubs(store, family_id)

    # Store config hashes for config guard
    _store_config_hashes(artifact_store, family_id, configs_dir)

    # Transition to QUEUED
    engine = TransitionEngine()
    result = engine.apply(family, FamilyEvent.FAMILY_CREATED)
    store.write_family(result.family)

    # Update global state
    store.write_global_state({
        "active_family": family_id,
        "state": str(result.new_state),
        "current_iteration": 0,
        "best_score": 0.0,
        "best_qualified_score": 0.0,
        "last_transition_at": datetime.now(tz=timezone.utc).isoformat(),
    })

    logger.info("Created family %s from seed %s", family_id, seed_id)
    return result.family


def fork_family(
    parent_id: str,
    fork_reason: str,
    store: MarkdownStore,
    artifact_store: ArtifactStore,
    configs_dir: str | Path = "configs",
) -> IdeaFamily:
    """Create a child family from a parent with a simplified hypothesis.

    Inherits the parent's mechanism and best code checkpoint, but starts
    with fresh strikes, budget, and iteration count.
    """
    parent = store.read_family(parent_id)

    # Allocate child family_id (next version of same slug)
    child_id = _next_family_id(store, parent.mechanism)

    child = IdeaFamily(
        family_id=child_id,
        parent_family_id=parent_id,
        fork_reason=fork_reason,
        seed_id=parent.seed_id,
        base_hypothesis=parent.base_hypothesis,
        mechanism=parent.mechanism,
        allowed_mutations=parent.allowed_mutations.model_copy(),
        state=FamilyState.NEW,
    )

    # Write family files
    store.write_family(child)
    store.append_history(
        child_id,
        f"Forked from {parent_id}. Reason: {fork_reason}",
    )
    store.write_mutation_policy(child_id, {
        "budget": child.mutation_budget.model_dump(),
        "allowed_mutations": child.allowed_mutations.model_dump(),
    })

    # Copy best code from parent checkpoint, or current research code
    checkpoint = artifact_store.load_checkpoint(parent_id)
    research_dir = store.root / "families" / child_id / "research"
    research_dir.mkdir(parents=True, exist_ok=True)

    if checkpoint:
        for filename, content in checkpoint.items():
            (research_dir / filename).write_text(content)
    else:
        # Fall back to copying parent's current research files
        parent_research = store.root / "families" / parent_id / "research"
        if parent_research.exists():
            for py_file in parent_research.glob("*.py"):
                dest = research_dir / py_file.name
                dest.write_text(py_file.read_text())
        else:
            _create_research_stubs(store, child_id)

    # Store config hashes for config guard
    _store_config_hashes(artifact_store, child_id, configs_dir)

    # Transition to QUEUED
    engine = TransitionEngine()
    result = engine.apply(child, FamilyEvent.FAMILY_CREATED)
    store.write_family(result.family)

    # Push to queue so orchestrator picks it up
    store.push_to_queue(child_id)

    logger.info(
        "Forked family %s from %s (reason: %s)",
        child_id, parent_id, fork_reason,
    )
    return result.family


def _create_research_stubs(store: MarkdownStore, family_id: str) -> None:
    """Copy research stub templates into the family directory."""
    templates_dir = Path(__file__).resolve().parents[2] / "templates" / "research"
    family_research = store.root / "families" / family_id / "research"
    family_research.mkdir(parents=True, exist_ok=True)

    for template_file in templates_dir.glob("*.py"):
        dest = family_research / template_file.name
        if not dest.exists():
            dest.write_text(template_file.read_text())


def _store_config_hashes(
    artifact_store: ArtifactStore,
    family_id: str,
    configs_dir: str | Path,
) -> None:
    """Compute and store SHA-256 hashes of config files."""
    configs_path = Path(configs_dir).resolve()
    hashes = {}
    for config_file in configs_path.glob("*.yaml"):
        content = config_file.read_bytes()
        hashes[config_file.name] = hashlib.sha256(content).hexdigest()
    artifact_store.save_config_hashes(family_id, hashes)
