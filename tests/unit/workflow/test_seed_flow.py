"""Tests for seed ingestion, distillation, and family creation."""
from __future__ import annotations

from pathlib import Path

from alpha_forge.app.domain.models import SeedCard
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.seed_flow import create_family, distill_seed, ingest_seed


class DummySeedClient:
    def call_json(self, system: str, user_prompt: str) -> dict[str, object]:
        return {
            "seed_type": "paper",
            "source_title": "Structured Source",
            "raw_claim": "Structured claim",
            "market": "crypto_spot",
            "horizon": "5min",
            "mechanism": "Structured mechanism",
            "required_data": ["OHLCV"],
            "testable_hypothesis": "Structured hypothesis",
            "ambiguities": [],
            "risk_flags": [],
        }


def test_distill_seed_persists_structured_card(tmp_workspace: Path) -> None:
    store = MarkdownStore(tmp_workspace)
    seed_id = ingest_seed("Raw claim", "Raw source", store)

    returned = distill_seed(seed_id, store, client=DummySeedClient())
    stored = store.read_seed(seed_id, stage="distilled")

    assert returned.model_dump() == stored.model_dump()
    assert store.list_seeds("inbox") == []


def test_create_family_auto_versions_duplicate_mechanisms(tmp_workspace: Path) -> None:
    store = MarkdownStore(tmp_workspace)
    artifact_store = ArtifactStore(tmp_workspace)

    for seed_id in ("seed_1", "seed_2"):
        store.write_seed(
            SeedCard(
                seed_id=seed_id,
                seed_type="paper",
                source_title="source",
                raw_claim="claim",
                market="crypto_spot",
                horizon="5min",
                mechanism="Same Mechanism",
                testable_hypothesis="hypothesis",
            ),
            stage="accepted",
        )

    family_1 = create_family("seed_1", store, artifact_store, configs_dir="configs")
    family_2 = create_family("seed_2", store, artifact_store, configs_dir="configs")

    assert family_1.family_id == "same_mechanism_v1"
    assert family_2.family_id == "same_mechanism_v2"
    assert store.read_family("same_mechanism_v1").seed_id == "seed_1"
    assert store.read_family("same_mechanism_v2").seed_id == "seed_2"
