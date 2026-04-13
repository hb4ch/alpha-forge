"""Markdown + YAML frontmatter storage layer.

All state mutations flow through this store. Writes are atomic
(write to temp file, then rename) to prevent corruption on crash.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from alpha_forge.app.domain.models import (
    IdeaFamily,
    Iteration,
    SeedCard,
)
from alpha_forge.app.domain.states import FamilyState, IterationStage


class MarkdownStore:
    """File-based state store using Markdown with YAML frontmatter."""

    def __init__(self, workspace_root: str | Path = "alpha_research") -> None:
        self.root = Path(workspace_root).resolve()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _atomic_write(self, path: Path, content: str) -> None:
        """Write content atomically via temp file + rename."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def _read_frontmatter(self, path: Path) -> tuple[dict[str, Any], str]:
        """Read a Markdown file and return (metadata, content)."""
        post = frontmatter.load(str(path))
        return dict(post.metadata), post.content

    def _write_frontmatter(self, path: Path, metadata: dict[str, Any], content: str = "") -> None:
        """Write a Markdown file with YAML frontmatter."""
        post = frontmatter.Post(content, **metadata)
        self._atomic_write(path, frontmatter.dumps(post))

    # ------------------------------------------------------------------
    # Global state
    # ------------------------------------------------------------------

    def read_global_state(self) -> dict[str, Any]:
        """Read STATE.md global state."""
        path = self.root / "STATE.md"
        if not path.exists():
            return {}
        meta, _ = self._read_frontmatter(path)
        return meta

    def write_global_state(self, state: dict[str, Any]) -> None:
        """Write STATE.md global state."""
        self._write_frontmatter(
            self.root / "STATE.md",
            state,
            "# Global State\n",
        )

    def push_to_queue(self, family_id: str) -> None:
        """Append a family to the global processing queue."""
        state = self.read_global_state()
        queue = state.get("family_queue") or []
        if family_id not in queue:
            queue.append(family_id)
        state["family_queue"] = queue
        self.write_global_state(state)

    def pop_from_queue(self) -> str | None:
        """Pop the next family from the processing queue."""
        state = self.read_global_state()
        queue = state.get("family_queue") or []
        if not queue:
            return None
        family_id = queue.pop(0)
        state["family_queue"] = queue
        state["active_family"] = family_id
        self.write_global_state(state)
        return family_id

    # ------------------------------------------------------------------
    # Family operations
    # ------------------------------------------------------------------

    def _family_dir(self, family_id: str) -> Path:
        return self.root / "families" / family_id

    def family_exists(self, family_id: str) -> bool:
        return (self._family_dir(family_id) / "FAMILY.md").exists()

    def read_family(self, family_id: str) -> IdeaFamily:
        """Read a family from its FAMILY.md file."""
        path = self._family_dir(family_id) / "FAMILY.md"
        meta, _ = self._read_frontmatter(path)
        return IdeaFamily.model_validate(meta)

    def write_family(self, family: IdeaFamily) -> None:
        """Write a family to its FAMILY.md file."""
        family_dir = self._family_dir(family.family_id)
        family_dir.mkdir(parents=True, exist_ok=True)

        data = family.model_dump(mode="json")
        content = f"# Family: {family.family_id}\n\n## Hypothesis\n{family.base_hypothesis}\n\n## Mechanism\n{family.mechanism}\n"
        self._write_frontmatter(family_dir / "FAMILY.md", data, content)

    def list_families(self) -> list[str]:
        """List all family IDs."""
        families_dir = self.root / "families"
        if not families_dir.exists():
            return []
        return [
            d.name
            for d in families_dir.iterdir()
            if d.is_dir() and (d / "FAMILY.md").exists()
        ]

    # ------------------------------------------------------------------
    # Iteration operations
    # ------------------------------------------------------------------

    def read_iteration(self, family_id: str) -> Iteration | None:
        """Read the current iteration from CURRENT_ITERATION.md."""
        path = self._family_dir(family_id) / "CURRENT_ITERATION.md"
        if not path.exists():
            return None
        meta, _ = self._read_frontmatter(path)
        return Iteration.model_validate(meta)

    def write_iteration(self, iteration: Iteration) -> None:
        """Write the current iteration to CURRENT_ITERATION.md."""
        family_dir = self._family_dir(iteration.family_id)
        data = iteration.model_dump(mode="json")
        content = f"# Iteration: {iteration.iteration_id}\n\nStage: {iteration.stage}\n"
        self._write_frontmatter(family_dir / "CURRENT_ITERATION.md", data, content)

    # ------------------------------------------------------------------
    # History operations (append-only)
    # ------------------------------------------------------------------

    def append_history(self, family_id: str, entry: str) -> None:
        """Append an entry to the family's HISTORY.md."""
        path = self._family_dir(family_id) / "HISTORY.md"
        path.parent.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(tz=timezone.utc).isoformat()
        line = f"\n## [{timestamp}]\n{entry}\n"

        if path.exists():
            with open(path, "a") as f:
                f.write(line)
        else:
            self._atomic_write(path, f"# History: {family_id}\n{line}")

    # ------------------------------------------------------------------
    # Seed operations
    # ------------------------------------------------------------------

    def _seed_dir(self, stage: str) -> Path:
        return self.root / "seeds" / stage

    def write_seed(self, seed: SeedCard, stage: str = "inbox") -> None:
        """Write a seed card to the appropriate directory."""
        directory = self._seed_dir(stage)
        data = seed.model_dump(mode="json")
        content = f"# Seed: {seed.seed_id}\n\n## Claim\n{seed.raw_claim}\n\n## Hypothesis\n{seed.testable_hypothesis}\n"
        self._write_frontmatter(directory / f"{seed.seed_id}.md", data, content)

    def read_seed(self, seed_id: str, stage: str = "inbox") -> SeedCard:
        """Read a seed card from a directory."""
        path = self._seed_dir(stage) / f"{seed_id}.md"
        meta, _ = self._read_frontmatter(path)
        return SeedCard.model_validate(meta)

    def move_seed(self, seed_id: str, from_stage: str, to_stage: str) -> None:
        """Move a seed between directories."""
        src = self._seed_dir(from_stage) / f"{seed_id}.md"
        dst_dir = self._seed_dir(to_stage)
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{seed_id}.md"
        src.rename(dst)

    def delete_seed(self, seed_id: str, stage: str = "inbox") -> None:
        """Delete a seed card from a stage directory if it exists."""
        path = self._seed_dir(stage) / f"{seed_id}.md"
        if path.exists():
            path.unlink()

    def list_seeds(self, stage: str = "inbox") -> list[str]:
        """List seed IDs in a directory."""
        directory = self._seed_dir(stage)
        if not directory.exists():
            return []
        return [p.stem for p in directory.glob("*.md")]

    # ------------------------------------------------------------------
    # Ledger operations
    # ------------------------------------------------------------------

    def write_ledger_entry(
        self,
        family_id: str,
        iteration_id: str,
        entry_data: dict[str, Any],
    ) -> None:
        """Write a ledger entry for an iteration."""
        ledger_dir = self.root / "ledger"
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{family_id}_{iteration_id}.md"
        content = f"# Ledger: {family_id} / {iteration_id}\n"
        self._write_frontmatter(ledger_dir / filename, entry_data, content)

    # ------------------------------------------------------------------
    # Judge verdict operations
    # ------------------------------------------------------------------

    def write_verdict(
        self,
        family_id: str,
        iteration_id: str,
        stage: str,
        verdict_data: dict[str, Any],
    ) -> None:
        """Write a judge verdict file."""
        verdicts_dir = self.root / "judges" / "verdicts"
        filename = f"{family_id}_{iteration_id}_{stage}.md"
        content = f"# Verdict: {stage}\n"
        self._write_frontmatter(verdicts_dir / filename, verdict_data, content)

    # ------------------------------------------------------------------
    # Mutation policy file
    # ------------------------------------------------------------------

    def write_mutation_policy(self, family_id: str, policy_data: dict[str, Any]) -> None:
        """Write the mutation policy file for a family."""
        path = self._family_dir(family_id) / "MUTATION_POLICY.md"
        content = f"# Mutation Policy: {family_id}\n"
        self._write_frontmatter(path, policy_data, content)

    def read_mutation_policy(self, family_id: str) -> dict[str, Any]:
        """Read the mutation policy for a family."""
        path = self._family_dir(family_id) / "MUTATION_POLICY.md"
        if not path.exists():
            return {}
        meta, _ = self._read_frontmatter(path)
        return meta
