"""Tests for seed ingestion, distillation, and family creation."""
from __future__ import annotations

from pathlib import Path

from alpha_forge.app.domain.models import SeedCard
from alpha_forge.app.domain.states import FamilyState
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.seed_flow import create_family, distill_seed, fork_family, ingest_seed
from tests.conftest import make_family


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


class TestForkFamily:
    def test_creates_child_with_correct_fields(
        self, tmp_workspace: Path,
    ) -> None:
        store = MarkdownStore(tmp_workspace)
        artifact_store = ArtifactStore(tmp_workspace)

        parent = make_family(
            family_id="test_mech_v1",
            seed_id="seed_001",
            mechanism="test_mech",
            state=FamilyState.QUEUED,
        )
        store.write_family(parent)

        child = fork_family(
            "test_mech_v1", "too complex", store, artifact_store,
            configs_dir="configs",
        )

        assert child.family_id == "test_mech_v2"
        assert child.parent_family_id == "test_mech_v1"
        assert child.fork_reason == "too complex"
        assert child.seed_id == "seed_001"
        assert child.state == FamilyState.QUEUED

    def test_copies_checkpoint_code(
        self, tmp_workspace: Path,
    ) -> None:
        store = MarkdownStore(tmp_workspace)
        artifact_store = ArtifactStore(tmp_workspace)

        parent = make_family(
            family_id="test_mech_v1",
            mechanism="test_mech",
            state=FamilyState.QUEUED,
        )
        store.write_family(parent)
        artifact_store.save_checkpoint("test_mech_v1", {"features.py": "# best code"})

        child = fork_family(
            "test_mech_v1", "fork reason", store, artifact_store,
            configs_dir="configs",
        )

        child_features = (
            store.root / "families" / child.family_id / "research" / "features.py"
        )
        assert child_features.exists()
        assert child_features.read_text() == "# best code"

    def test_falls_back_to_parent_research_files(
        self, tmp_workspace: Path,
    ) -> None:
        store = MarkdownStore(tmp_workspace)
        artifact_store = ArtifactStore(tmp_workspace)

        parent = make_family(
            family_id="test_mech_v1",
            mechanism="test_mech",
            state=FamilyState.QUEUED,
        )
        store.write_family(parent)
        # Write parent research files (no checkpoint)
        parent_research = store.root / "families" / "test_mech_v1" / "research"
        parent_research.mkdir(parents=True, exist_ok=True)
        (parent_research / "features.py").write_text("# parent code")

        child = fork_family(
            "test_mech_v1", "fork reason", store, artifact_store,
            configs_dir="configs",
        )

        child_features = (
            store.root / "families" / child.family_id / "research" / "features.py"
        )
        assert child_features.read_text() == "# parent code"

    def test_pushes_child_to_queue(
        self, tmp_workspace: Path,
    ) -> None:
        store = MarkdownStore(tmp_workspace)
        artifact_store = ArtifactStore(tmp_workspace)

        parent = make_family(
            family_id="test_mech_v1",
            mechanism="test_mech",
            state=FamilyState.QUEUED,
        )
        store.write_family(parent)

        fork_family(
            "test_mech_v1", "fork reason", store, artifact_store,
            configs_dir="configs",
        )

        state = store.read_global_state()
        assert "test_mech_v2" in (state.get("family_queue") or [])
