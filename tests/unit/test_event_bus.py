"""Tests for EventBus thread-safe pub/sub."""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

from alpha_forge.app.event_bus import EventBus


class DummyApp:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def post_message(self, message: object) -> None:
        self.messages.append(message)


class TestEventBusSubscribe:
    def test_subscribe_and_emit(self) -> None:
        loop = asyncio.new_event_loop()
        bus = EventBus(loop)
        callback = MagicMock()
        bus.subscribe("test_event", callback)

        loop.run_until_complete(bus.emit("test_event", {"key": "value"}))
        callback.assert_called_once_with({"key": "value"})
        loop.close()

    def test_multiple_subscribers(self) -> None:
        loop = asyncio.new_event_loop()
        bus = EventBus(loop)
        cb1 = MagicMock()
        cb2 = MagicMock()
        bus.subscribe("evt", cb1)
        bus.subscribe("evt", cb2)

        loop.run_until_complete(bus.emit("evt", {"x": 1}))
        cb1.assert_called_once()
        cb2.assert_called_once()
        loop.close()

    def test_unrelated_event_not_called(self) -> None:
        loop = asyncio.new_event_loop()
        bus = EventBus(loop)
        callback = MagicMock()
        bus.subscribe("event_a", callback)

        loop.run_until_complete(bus.emit("event_b", {}))
        callback.assert_not_called()
        loop.close()


class TestEventBusSyncEmit:
    def test_emit_sync_notifies_local_subscribers_without_running_loop(self) -> None:
        loop = asyncio.new_event_loop()
        bus = EventBus(loop)
        received = []
        bus.subscribe("sync_test", lambda data: received.append(data))

        bus.emit_sync("sync_test", {"from": "worker"})

        assert len(received) == 1
        assert received[0]["from"] == "worker"
        loop.close()

    def test_emit_sync_posts_to_app_and_notifies_subscribers(self) -> None:
        loop = asyncio.new_event_loop()
        app = DummyApp()
        bus = EventBus(loop, app=app)
        received = []
        bus.subscribe("sync_test", lambda data: received.append(data))

        bus.emit_sync("sync_test", {"from": "worker"})

        assert len(received) == 1
        assert received[0]["from"] == "worker"
        assert len(app.messages) == 1
        loop.close()


class TestEventBusGate:
    def test_gate_blocks_and_releases(self) -> None:
        loop = asyncio.new_event_loop()
        bus = EventBus(loop)
        bus.semi_auto = True
        gate_results = []
        override_emitted = []
        worker_started = threading.Event()

        bus.subscribe("verdict_awaiting_override", lambda d: override_emitted.append(d))

        def worker():
            worker_started.set()
            result = bus.gate_for_override({"verdict": "revise", "judge": "overfit"})
            gate_results.append(result)

        worker_thread = threading.Thread(target=worker)
        worker_thread.start()

        assert worker_started.wait(timeout=1) is True
        time.sleep(0.05)
        assert override_emitted == [{"verdict": "revise", "judge": "overfit"}]

        bus.release_gate({"action": "override", "verdict": "approve"})
        worker_thread.join(timeout=1)

        assert len(gate_results) == 1
        assert gate_results[0]["action"] == "override"
        assert gate_results[0]["verdict"] == "approve"
        loop.close()

    def test_gate_skipped_in_autopilot(self) -> None:
        loop = asyncio.new_event_loop()
        bus = EventBus(loop)
        bus.semi_auto = False

        result = bus.gate_for_override({"verdict": "revise"})
        assert result is None  # No blocking in autopilot
        loop.close()
