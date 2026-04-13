"""Tests for MarkdownStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from alpha_forge.app.domain.models import Iteration, SeedCard
from alpha_forge.app.domain.states import FamilyState, IterationStage
from alpha_forge.app.storage.markdown_store import MarkdownStore
from tests.conftest import make_family


class TestFamilyRoundtrip:
    def test_write_then_read_preserves_fields(self, markdown_store: MarkdownStore) -> None:
        family = make_family(
            family_id="fam_001",
            seed_id="seed_001",
            base_hypothesis="Test hypothesis",
            mechanism="Test mechanism",
            state=FamilyState.QUEUED,
        )
        markdown_store.write_family(family)
        loaded = markdown_store.read_family("fam_001")

        assert loaded.family_id == "fam_001"
        assert loaded.seed_id == "seed_001"
        assert loaded.base_hypothesis == "Test hypothesis"
        assert loaded.mechanism == "Test mechanism"
        assert loaded.state == FamilyState.QUEUED


class TestSeedRoundtrip:
    def test_write_then_read_preserves_fields(self, markdown_store: MarkdownStore) -> None:
        seed = SeedCard(
            seed_id="seed_001",
            seed_type="paper",
            source_title="Test",
            raw_claim="test claim",
            market="crypto_spot",
            horizon="5min",
            mechanism="test",
            testable_hypothesis="test hyp",
        )
        markdown_store.write_seed(seed, "inbox")
        loaded = markdown_store.read_seed("seed_001", "inbox")

        assert loaded.seed_id == "seed_001"
        assert loaded.seed_type == "paper"
        assert loaded.source_title == "Test"
        assert loaded.raw_claim == "test claim"
        assert loaded.market == "crypto_spot"
        assert loaded.horizon == "5min"
        assert loaded.mechanism == "test"
        assert loaded.testable_hypothesis == "test hyp"


class TestMoveSeed:
    def test_move_seed_between_stages(self, markdown_store: MarkdownStore, tmp_workspace: Path) -> None:
        seed = SeedCard(
            seed_id="seed_001",
            seed_type="paper",
            source_title="Test",
            raw_claim="test claim",
            market="crypto_spot",
            horizon="5min",
            mechanism="test",
            testable_hypothesis="test hyp",
        )
        markdown_store.write_seed(seed, "inbox")
        markdown_store.move_seed("seed_001", "inbox", "accepted")

        assert (tmp_workspace / "seeds" / "accepted" / "seed_001.md").exists()
        assert not (tmp_workspace / "seeds" / "inbox" / "seed_001.md").exists()


class TestListFamilies:
    def test_lists_both_families(self, markdown_store: MarkdownStore) -> None:
        fam1 = make_family(family_id="fam_001")
        fam2 = make_family(family_id="fam_002")
        markdown_store.write_family(fam1)
        markdown_store.write_family(fam2)

        result = markdown_store.list_families()
        assert sorted(result) == ["fam_001", "fam_002"]


class TestListSeeds:
    def test_lists_both_seeds(self, markdown_store: MarkdownStore) -> None:
        for sid in ["seed_001", "seed_002"]:
            seed = SeedCard(
                seed_id=sid,
                seed_type="paper",
                source_title="Test",
                raw_claim="test claim",
                market="crypto_spot",
                horizon="5min",
                mechanism="test",
                testable_hypothesis="test hyp",
            )
            markdown_store.write_seed(seed, "inbox")

        result = markdown_store.list_seeds("inbox")
        assert sorted(result) == ["seed_001", "seed_002"]


class TestGlobalStateRoundtrip:
    def test_write_then_read(self, markdown_store: MarkdownStore) -> None:
        state = {"active_family": "fam_001", "state": "QUEUED"}
        markdown_store.write_global_state(state)
        loaded = markdown_store.read_global_state()

        assert loaded["active_family"] == "fam_001"
        assert loaded["state"] == "QUEUED"


class TestIterationRoundtrip:
    def test_write_then_read_preserves_fields(self, markdown_store: MarkdownStore) -> None:
        iteration = Iteration(iteration_id="iter_1", family_id="fam_001")
        # Need family dir to exist
        markdown_store.write_family(make_family(family_id="fam_001"))
        markdown_store.write_iteration(iteration)
        loaded = markdown_store.read_iteration("fam_001")

        assert loaded is not None
        assert loaded.iteration_id == "iter_1"
        assert loaded.stage == IterationStage.DRAFT_PLAN


class TestAppendHistory:
    def test_two_appends_both_present(self, markdown_store: MarkdownStore, tmp_workspace: Path) -> None:
        family = make_family(family_id="fam_001")
        markdown_store.write_family(family)

        markdown_store.append_history("fam_001", "First entry")
        markdown_store.append_history("fam_001", "Second entry")

        history_path = tmp_workspace / "families" / "fam_001" / "HISTORY.md"
        content = history_path.read_text()
        assert "First entry" in content
        assert "Second entry" in content


class TestFamilyExists:
    def test_false_before_write_true_after(self, markdown_store: MarkdownStore) -> None:
        assert markdown_store.family_exists("fam_001") is False
        markdown_store.write_family(make_family(family_id="fam_001"))
        assert markdown_store.family_exists("fam_001") is True


class TestFamilyQueue:
    def test_push_and_pop_fifo(self, markdown_store: MarkdownStore) -> None:
        markdown_store.push_to_queue("fam_a")
        markdown_store.push_to_queue("fam_b")
        assert markdown_store.pop_from_queue() == "fam_a"
        assert markdown_store.pop_from_queue() == "fam_b"

    def test_pop_empty_returns_none(self, markdown_store: MarkdownStore) -> None:
        assert markdown_store.pop_from_queue() is None

    def test_push_deduplicates(self, markdown_store: MarkdownStore) -> None:
        markdown_store.push_to_queue("fam_a")
        markdown_store.push_to_queue("fam_a")
        assert markdown_store.pop_from_queue() == "fam_a"
        assert markdown_store.pop_from_queue() is None

    def test_pop_sets_active_family(self, markdown_store: MarkdownStore) -> None:
        markdown_store.push_to_queue("fam_a")
        markdown_store.pop_from_queue()
        state = markdown_store.read_global_state()
        assert state["active_family"] == "fam_a"
