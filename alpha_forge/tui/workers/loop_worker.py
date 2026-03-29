"""Background worker that runs the orchestrator loop in a thread."""
from __future__ import annotations

import logging
from pathlib import Path

from alpha_forge.app.agents.llm_client import LLMClient
from alpha_forge.app.event_bus import EventBus
from alpha_forge.app.storage.artifact_store import ArtifactStore
from alpha_forge.app.storage.markdown_store import MarkdownStore
from alpha_forge.app.workflow.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class LoopWorker:
    """Drives the orchestrator in a background thread.

    Used by the Textual app's run_worker() to keep the TUI responsive
    while the orchestrator runs.
    """

    def __init__(
        self,
        workspace: str,
        configs_dir: str,
        bus: EventBus,
        family_id: str | None = None,
        max_iterations: int = 10,
    ) -> None:
        self.workspace = workspace
        self.configs_dir = configs_dir
        self.bus = bus
        self.family_id = family_id
        self.max_iterations = max_iterations
        self.orchestrator: Orchestrator | None = None

    def run(self) -> dict:
        """Run the orchestrator loop. Called from a worker thread."""
        store = MarkdownStore(self.workspace)
        artifact_store = ArtifactStore(self.workspace)
        from alpha_forge.app.agents.llm_config import get_client_for_role
        client = get_client_for_role("researcher")

        self.orchestrator = Orchestrator(
            store=store,
            artifact_store=artifact_store,
            configs_dir=self.configs_dir,
            client=client,
            max_iterations=self.max_iterations,
            bus=self.bus,
        )

        # Resolve family ID from STATE.md if not provided
        family_id = self.family_id
        if family_id is None:
            global_state = store.read_global_state()
            family_id = global_state.get("active_family")

        self.bus.emit_sync("loop_started", {"family_id": family_id})

        try:
            result = self.orchestrator.run(family_id)
            self.bus.emit_sync("loop_finished", result)
            return result
        except Exception as e:
            logger.exception("Loop worker failed: %s", e)
            self.bus.emit_sync("loop_error", {"error": str(e)})
            return {"error": str(e)}

    def pause(self) -> None:
        if self.orchestrator:
            self.orchestrator.pause()

    def resume(self) -> None:
        if self.orchestrator:
            self.orchestrator.resume()
