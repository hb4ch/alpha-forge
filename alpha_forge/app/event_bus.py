"""Thread-safe event bus for TUI <-> orchestrator communication."""
from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from typing import Any, Callable


class EventBus:
    """Pub/sub event bus with thread-safe bridging and gate mechanism.

    The EventBus bridges the synchronous worker thread (orchestrator) to
    the async Textual event loop. Key design:
    - emit(): for async callers (TUI side)
    - emit_sync(): for worker thread, uses Textual's call_from_thread
    - gate_for_override(): blocks worker thread until TUI user decides
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, app=None) -> None:
        self._loop = loop
        self._app = app  # Textual App instance for call_from_thread
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._gate: threading.Event | None = None
        self._gate_decision: dict[str, Any] | None = None
        self.semi_auto: bool = False

    def subscribe(self, event: str, callback: Callable) -> None:
        """Register a callback for an event type."""
        self._subscribers[event].append(callback)

    async def emit(self, event: str, data: dict[str, Any]) -> None:
        """Emit an event from async context."""
        for cb in self._subscribers.get(event, []):
            cb(data)

    def emit_sync(self, event: str, data: dict[str, Any]) -> None:
        """Emit an event from a worker thread.

        Always notifies local subscribers immediately. If a Textual app is
        attached, also posts a BusEvent for the TUI message pump.
        """
        for cb in list(self._subscribers.get(event, [])):
            cb(data)

        if self._app is not None:
            from alpha_forge.tui.app import BusEvent
            try:
                self._app.post_message(BusEvent(event, data))
            except Exception:
                pass  # App shutting down

    def gate_for_override(self, context: dict[str, Any]) -> dict[str, Any] | None:
        """Block worker thread until TUI user makes an override decision.

        Called by the worker thread at verdict points when semi_auto is True.
        Returns None if not in semi-auto mode (autopilot skips gating).
        Returns the override decision dict when the gate is released.
        """
        if not self.semi_auto:
            return None

        self._gate = threading.Event()
        self._gate_decision = None

        # Notify TUI that we're waiting for an override decision
        self.emit_sync("verdict_awaiting_override", context)

        # Block until TUI releases the gate
        self._gate.wait()
        decision = self._gate_decision
        self._gate = None
        self._gate_decision = None
        return decision

    def release_gate(self, decision: dict[str, Any]) -> None:
        """Release the gate from the TUI side.

        Called when the user picks an override action.
        """
        self._gate_decision = decision
        if self._gate is not None:
            self._gate.set()
