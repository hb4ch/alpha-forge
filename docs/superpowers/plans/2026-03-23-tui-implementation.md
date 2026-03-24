# Alpha Forge TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Textual-based TUI that replaces CLI scripts as the primary way to run Alpha Forge, with live LLM streaming, judge verdict display, metrics dashboards, and autopilot/semi-auto control modes.

**Architecture:** EventBus-driven pub/sub bridges the synchronous orchestrator (running in a worker thread) to async Textual widgets. Backtest/robustness runs are process-isolated via multiprocessing. Tiered LLM config enables cost-optimized model assignment per agent role.

**Tech Stack:** Python 3.12+, Textual, Rich, anthropic SDK, openai SDK, multiprocessing, asyncio, pydantic, PyYAML

**Spec:** `docs/superpowers/specs/2026-03-22-tui-design.md`

---

## File Map

### New files

| File | Responsibility |
|------|---------------|
| `alpha_forge/app/event_bus.py` | EventBus: thread-safe pub/sub with gate mechanism for semi-auto |
| `alpha_forge/app/agents/llm_config.py` | Tiered LLM config loader, `get_client_for_role()` factory |
| `configs/llm.yaml` | Provider/tier/role config |
| `alpha_forge/tui/__init__.py` | Package marker |
| `alpha_forge/tui/app.py` | Textual App subclass, screen composition, keybindings |
| `alpha_forge/tui/screens/main_screen.py` | Main IDE-style screen layout |
| `alpha_forge/tui/widgets/state_sidebar.py` | Left sidebar: pipeline, family info, family selector |
| `alpha_forge/tui/widgets/conversation.py` | LLM conversation stream widget |
| `alpha_forge/tui/widgets/metrics_panel.py` | Backtest results table + scores |
| `alpha_forge/tui/widgets/code_panel.py` | Syntax-highlighted code + diff view |
| `alpha_forge/tui/widgets/verdicts_panel.py` | Judge verdict cards |
| `alpha_forge/tui/widgets/guards_panel.py` | Guard pass/fail display |
| `alpha_forge/tui/widgets/log_panel.py` | Filtered Python log viewer |
| `alpha_forge/tui/widgets/override_modal.py` | Semi-auto verdict override + text input |
| `alpha_forge/tui/widgets/command_palette.py` | Slash command input |
| `alpha_forge/tui/styles/theme.tcss` | Textual CSS theme |
| `alpha_forge/tui/workers/loop_worker.py` | Background worker driving orchestrator |
| `alpha_forge/tui/workers/subprocess_runner.py` | Process-isolated backtest/robustness |
| `alpha_forge/scripts/run_tui.py` | CLI entry point |

### Modified files

| File | What changes |
|------|-------------|
| `alpha_forge/app/agents/llm_client.py` | Multi-provider, `stream_callback`, retry backoff |
| `alpha_forge/app/agents/base_judge.py` | Accept `role` param, use `get_client_for_role()` |
| `alpha_forge/app/agents/researcher.py` | Use `get_client_for_role("researcher")` |
| `alpha_forge/app/domain/models.py` | Add `overfit_flag_history`, `score_history` to `IdeaFamily` |
| `alpha_forge/app/domain/states.py` | Rename `CANCELLED_3_STRIKES` → `PAUSED_FOR_REVIEW` |
| `alpha_forge/app/domain/strikes.py` | Pattern-based detection, `should_pause_for_review()` |
| `alpha_forge/app/workflow/transitions.py` | Import `should_pause_for_review`, update target state |
| `alpha_forge/app/workflow/family_flow.py` | Emit events, gate for override, snapshot code |
| `alpha_forge/app/workflow/orchestrator.py` | Accept EventBus, emit events, update WAITING_STATES |
| `alpha_forge/app/storage/artifact_store.py` | Add `save_code_snapshot()` / `load_code_snapshot()` |
| `configs/guardrails.yaml` | Add `resource_limits` section |
| `pyproject.toml` | Add `textual`, `openai` dependencies |

### New test files

| File | What it tests |
|------|--------------|
| `tests/unit/test_event_bus.py` | EventBus pub/sub, thread-safe emit, gate mechanism |
| `tests/unit/domain/test_strikes_v2.py` | Pattern-based strike detection |
| `tests/unit/agents/test_llm_config.py` | Tiered config loading, role→client mapping |
| `tests/unit/agents/test_llm_client_v2.py` | Multi-provider, streaming, retry |
| `tests/unit/tui/test_subprocess_runner.py` | Process isolation, timeout, OOM classification |
| `tests/unit/storage/test_code_snapshot.py` | Code snapshot save/load |

---

## Task 1: Add dependencies

**Files:**
- Modify: `pyproject.toml:12-20`

- [ ] **Step 1: Add textual and openai to pyproject.toml**

```toml
# Add to dependencies list:
"textual>=0.80",
"openai>=1.0",
```

- [ ] **Step 2: Install dependencies**

Run: `pip install -e ".[dev]"`
Expected: Success, textual and openai installed

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add textual and openai dependencies"
```

---

## Task 2: EventBus

**Files:**
- Create: `alpha_forge/app/event_bus.py` (NOT `events.py` — avoids confusion with existing `alpha_forge/app/domain/events.py`)
- Test: `tests/unit/test_event_bus.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_event_bus.py
"""Tests for EventBus thread-safe pub/sub."""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from alpha_forge.app.event_bus import EventBus


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
    def test_emit_sync_schedules_on_loop(self) -> None:
        loop = asyncio.new_event_loop()
        bus = EventBus(loop)
        received = []
        bus.subscribe("sync_test", lambda data: received.append(data))

        # Start loop in background thread
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        # Emit from this thread (simulating worker thread)
        bus.emit_sync("sync_test", {"from": "worker"})

        # Give event loop time to process
        import time
        time.sleep(0.1)
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=1)

        assert len(received) == 1
        assert received[0]["from"] == "worker"
        loop.close()


class TestEventBusGate:
    def test_gate_blocks_and_releases(self) -> None:
        loop = asyncio.new_event_loop()
        bus = EventBus(loop)
        bus.semi_auto = True
        gate_results = []
        override_emitted = []

        bus.subscribe("verdict_awaiting_override", lambda d: override_emitted.append(d))

        # Start loop in background
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        def worker():
            result = bus.gate_for_override({"verdict": "revise", "judge": "overfit"})
            gate_results.append(result)

        worker_thread = threading.Thread(target=worker)
        worker_thread.start()

        import time
        time.sleep(0.1)  # Let worker block on gate

        # Release from "TUI side"
        bus.release_gate({"action": "override", "verdict": "approve"})
        worker_thread.join(timeout=1)

        assert len(gate_results) == 1
        assert gate_results[0]["action"] == "override"
        assert gate_results[0]["verdict"] == "approve"

        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=1)
        loop.close()

    def test_gate_skipped_in_autopilot(self) -> None:
        loop = asyncio.new_event_loop()
        bus = EventBus(loop)
        bus.semi_auto = False

        result = bus.gate_for_override({"verdict": "revise"})
        assert result is None  # No blocking in autopilot
        loop.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_event_bus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'alpha_forge.app.events'`

- [ ] **Step 3: Implement EventBus**

```python
# alpha_forge/app/event_bus.py
"""Thread-safe event bus for TUI ↔ orchestrator communication."""
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
    - emit_sync(): for worker thread, uses call_soon_threadsafe
    - gate_for_override(): blocks worker thread until TUI user decides
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
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

        Uses loop.call_soon_threadsafe() to schedule callbacks
        on the Textual event loop.
        """
        for cb in self._subscribers.get(event, []):
            self._loop.call_soon_threadsafe(cb, data)

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_event_bus.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add alpha_forge/app/event_bus.py tests/unit/test_event_bus.py
git commit -m "feat: add EventBus with thread-safe pub/sub and gate mechanism"
```

---

## Task 3: Revised strike policy

**Files:**
- Modify: `alpha_forge/app/domain/models.py:316-352`
- Modify: `alpha_forge/app/domain/states.py:27,31-37`
- Rewrite: `alpha_forge/app/domain/strikes.py`
- Modify: `alpha_forge/app/workflow/transitions.py:14,94-109`
- Modify: `alpha_forge/app/domain/events.py` (the existing TRANSITION_TABLE file)
- Create: `tests/unit/domain/test_strikes_v2.py`

- [ ] **Step 1: Write failing tests for new strike functions**

```python
# tests/unit/domain/test_strikes_v2.py
"""Tests for pattern-based strike detection."""
from __future__ import annotations

from alpha_forge.app.domain.strikes import (
    add_strike,
    detect_death_spiral,
    detect_overfit_loop,
    reset_strikes,
    should_pause_for_review,
)
from tests.conftest import make_family


class TestDetectOverfitLoop:
    def test_no_loop_with_different_tags(self) -> None:
        family = make_family(overfit_flag_history=["tag_a", "tag_b", "tag_c"])
        assert detect_overfit_loop(family, ["tag_d"]) is False

    def test_detects_3_consecutive_same_tag(self) -> None:
        family = make_family(overfit_flag_history=["tag_a", "tag_a"])
        assert detect_overfit_loop(family, ["tag_a"]) is True

    def test_no_loop_with_only_2_same(self) -> None:
        family = make_family(overfit_flag_history=["tag_a"])
        assert detect_overfit_loop(family, ["tag_a"]) is False

    def test_empty_history(self) -> None:
        family = make_family(overfit_flag_history=[])
        assert detect_overfit_loop(family, ["tag_a"]) is False


class TestDetectDeathSpiral:
    def test_no_spiral_with_improving_scores(self) -> None:
        family = make_family(score_history=[0.3, 0.4, 0.5])
        assert detect_death_spiral(family, 0.6) is False

    def test_detects_3_consecutive_declines(self) -> None:
        family = make_family(score_history=[0.5, 0.4, 0.3])
        assert detect_death_spiral(family, 0.2) is True

    def test_no_spiral_with_recovery(self) -> None:
        family = make_family(score_history=[0.5, 0.4, 0.3])
        assert detect_death_spiral(family, 0.35) is False

    def test_short_history(self) -> None:
        family = make_family(score_history=[0.5])
        assert detect_death_spiral(family, 0.3) is False


class TestShouldPauseForReview:
    def test_pause_at_3_yellow(self) -> None:
        family = make_family(strike_count=3, red_strike_count=0)
        assert should_pause_for_review(family) is True

    def test_pause_at_2_red(self) -> None:
        family = make_family(strike_count=2, red_strike_count=2)
        assert should_pause_for_review(family) is True

    def test_no_pause_below_threshold(self) -> None:
        family = make_family(strike_count=2, red_strike_count=0)
        assert should_pause_for_review(family) is False


class TestResetStrikesV2:
    def test_clears_pattern_trackers(self) -> None:
        family = make_family(
            strike_count=2,
            red_strike_count=1,
            overfit_flag_history=["a", "a"],
            score_history=[0.5, 0.4],
        )
        updated = reset_strikes(family)
        assert updated.strike_count == 0
        assert updated.red_strike_count == 0
        assert updated.overfit_flag_history == []
        assert updated.score_history == []
        # History preserved
        assert len(updated.strike_history) == len(family.strike_history)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/domain/test_strikes_v2.py -v`
Expected: FAIL — missing fields and functions

- [ ] **Step 3: Add new fields to IdeaFamily model**

In `alpha_forge/app/domain/models.py`, add after line 332 (`failure_taxonomy`):

```python
    overfit_flag_history: list[str] = Field(default_factory=list)
    score_history: list[float] = Field(default_factory=list)
```

- [ ] **Step 4: Rename CANCELLED_3_STRIKES in states.py**

In `alpha_forge/app/domain/states.py`, change line 27:
```python
    PAUSED_FOR_REVIEW = "PAUSED_FOR_REVIEW"
```

Update `is_terminal` property (line 33-34) to remove it from terminal states:
```python
    @property
    def is_terminal(self) -> bool:
        return self in {
            FamilyState.ARCHIVED_REJECTED,
            FamilyState.DONE,
        }
```

- [ ] **Step 5: Rewrite strikes.py with pattern-based detection**

```python
# alpha_forge/app/domain/strikes.py
"""Pattern-based strike accounting.

Strikes only prevent overfitting death loops, not normal iteration.
Individual REVISE/REJECT verdicts do not add strikes.
"""
from __future__ import annotations

from alpha_forge.app.domain.models import IdeaFamily, StrikeRecord

MAX_STRIKES = 3
MAX_RED_STRIKES = 2
CONSECUTIVE_OVERFIT_THRESHOLD = 3
DEATH_SPIRAL_LENGTH = 3


def add_strike(
    family: IdeaFamily,
    iteration_id: str,
    reason: str,
    is_red: bool = False,
) -> IdeaFamily:
    """Add a strike to a family and return the updated family."""
    record = StrikeRecord(
        iteration_id=iteration_id,
        reason=reason,
        is_red=is_red,
    )
    return family.model_copy(
        update={
            "strike_count": family.strike_count + 1,
            "red_strike_count": family.red_strike_count + (1 if is_red else 0),
            "strike_history": [*family.strike_history, record],
        }
    )


def detect_overfit_loop(family: IdeaFamily, latest_tags: list[str]) -> bool:
    """Check if the same overfit flag appears in 3+ consecutive iterations.

    Looks at the tail of overfit_flag_history plus latest_tags to see if
    any single tag has appeared CONSECUTIVE_OVERFIT_THRESHOLD times in a row.
    """
    if not latest_tags:
        return False

    history = [*family.overfit_flag_history, *latest_tags]
    if len(history) < CONSECUTIVE_OVERFIT_THRESHOLD:
        return False

    # Check if the last N entries share any common tag
    tail = history[-(CONSECUTIVE_OVERFIT_THRESHOLD):]
    # All must be the same tag
    return len(set(tail)) == 1


def detect_death_spiral(family: IdeaFamily, latest_score: float) -> bool:
    """Check if composite score has decreased N iterations in a row.

    Returns True if the last (DEATH_SPIRAL_LENGTH - 1) scores in history
    plus the latest_score form a strictly decreasing sequence.
    """
    scores = [*family.score_history, latest_score]
    if len(scores) < DEATH_SPIRAL_LENGTH + 1:
        return False

    tail = scores[-(DEATH_SPIRAL_LENGTH + 1):]
    return all(tail[i] > tail[i + 1] for i in range(len(tail) - 1))


def should_pause_for_review(family: IdeaFamily) -> bool:
    """Check if a family should pause for human review.

    Replaces the old should_cancel(). Returns True if thresholds hit,
    but the family transitions to PAUSED_FOR_REVIEW instead of being cancelled.
    """
    return family.strike_count >= MAX_STRIKES or family.red_strike_count >= MAX_RED_STRIKES


def reset_strikes(family: IdeaFamily) -> IdeaFamily:
    """Reset strikes and pattern trackers after qualified improvement.

    History is preserved for audit trail.
    """
    return family.model_copy(
        update={
            "strike_count": 0,
            "red_strike_count": 0,
            "overfit_flag_history": [],
            "score_history": [],
        }
    )
```

- [ ] **Step 6: Update transitions.py to use should_pause_for_review and PAUSED_FOR_REVIEW**

In `alpha_forge/app/workflow/transitions.py`:
- Line 14: change `from alpha_forge.app.domain.strikes import add_strike, should_cancel` to `from alpha_forge.app.domain.strikes import add_strike, should_pause_for_review`
- Line 95: change `if should_cancel(updated_family):` to `if should_pause_for_review(updated_family):`
- Line 96: change `FamilyState.CANCELLED_3_STRIKES` to `FamilyState.PAUSED_FOR_REVIEW`

- [ ] **Step 7: Rename FamilyEvent.CANCELLED_3_STRIKES in domain/events.py**

In `alpha_forge/app/domain/events.py`:

a) Line 33: rename the enum member:
```python
    PAUSED_FOR_REVIEW = "PAUSED_FOR_REVIEW"
```

b) Update ALL 5 entries in TRANSITION_TABLE that reference `CANCELLED_3_STRIKES` (lines 46, 55, 62, 65, 70):
```python
    (FamilyState.PLAN_IN_REVIEW, FamilyEvent.PAUSED_FOR_REVIEW): FamilyState.PAUSED_FOR_REVIEW,
    (FamilyState.CODE_IN_REVIEW, FamilyEvent.PAUSED_FOR_REVIEW): FamilyState.PAUSED_FOR_REVIEW,
    (FamilyState.GUARDS_RUNNING, FamilyEvent.PAUSED_FOR_REVIEW): FamilyState.PAUSED_FOR_REVIEW,
    (FamilyState.BACKTEST_RUNNING, FamilyEvent.PAUSED_FOR_REVIEW): FamilyState.PAUSED_FOR_REVIEW,
    (FamilyState.RESULTS_IN_REVIEW, FamilyEvent.PAUSED_FOR_REVIEW): FamilyState.PAUSED_FOR_REVIEW,
```

c) Add new transitions FROM `PAUSED_FOR_REVIEW` (user decides: continue, archive, or fork):
```python
    # From PAUSED_FOR_REVIEW (user decides)
    (FamilyState.PAUSED_FOR_REVIEW, FamilyEvent.ITERATE): FamilyState.QUEUED,
    (FamilyState.PAUSED_FOR_REVIEW, FamilyEvent.HUMAN_REJECTED): FamilyState.ARCHIVED_REJECTED,
    (FamilyState.PAUSED_FOR_REVIEW, FamilyEvent.FAMILY_CREATED): FamilyState.QUEUED,  # Fork: creates new family, this one archives
```

- [ ] **Step 8: Update TransitionResult.cancelled field in transitions.py**

In `alpha_forge/app/workflow/transitions.py`:
- Rename `cancelled` field to `paused_for_review` in the `TransitionResult` dataclass (line ~20)
- Line 107: change `cancelled=True` to `paused_for_review=True`
- Line 109: change `cancelled=False` to `paused_for_review=False` (in the else branch)

- [ ] **Step 9: Update orchestrator WAITING_STATES**

In `alpha_forge/app/workflow/orchestrator.py`, line 36: change `FamilyState.CANCELLED_3_STRIKES` to `FamilyState.PAUSED_FOR_REVIEW`.

- [ ] **Step 10: Update family_flow.py imports and remove direct add_strike on tier-1 rejection**

In `alpha_forge/app/workflow/family_flow.py`:
- Line 23: change `from alpha_forge.app.domain.strikes import add_strike, reset_strikes, should_cancel` to `from alpha_forge.app.domain.strikes import add_strike, reset_strikes, should_pause_for_review`
- Line 131: remove the `family = add_strike(...)` call. Tier-1 plan rejection no longer adds a strike directly. Keep the state transition.
- Search for any other references to `should_cancel` or `CANCELLED_3_STRIKES` in the file and update them.

- [ ] **Step 11: Run all tests**

Run: `pytest tests/unit/domain/test_strikes_v2.py tests/unit/domain/test_strikes.py tests/unit/workflow/test_transitions.py tests/unit/domain/test_states.py -v`
Expected: `test_strikes_v2.py` all PASS. Some existing tests will need updating for renames.

- [ ] **Step 12: Fix any broken existing tests**

Update `test_strikes.py`: rename `TestShouldCancel` → `TestShouldPauseForReview`, update imports (`should_cancel` → `should_pause_for_review`).
Update `test_transitions.py`: all references to `CANCELLED_3_STRIKES` → `PAUSED_FOR_REVIEW` (both FamilyState and FamilyEvent).
Update `test_states.py`: `CANCELLED_3_STRIKES` → `PAUSED_FOR_REVIEW`, update `is_terminal` expectations (no longer terminal).

- [ ] **Step 13: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 14: Commit**

```bash
git add alpha_forge/app/domain/models.py alpha_forge/app/domain/states.py \
  alpha_forge/app/domain/strikes.py alpha_forge/app/domain/events.py \
  alpha_forge/app/workflow/transitions.py alpha_forge/app/workflow/orchestrator.py \
  alpha_forge/app/workflow/family_flow.py \
  tests/unit/domain/test_strikes_v2.py tests/unit/domain/test_strikes.py \
  tests/unit/workflow/test_transitions.py tests/unit/domain/test_states.py
git commit -m "feat: pattern-based strike policy, rename CANCELLED_3_STRIKES to PAUSED_FOR_REVIEW"
```

---

## Task 4: Tiered LLM config

**Files:**
- Create: `configs/llm.yaml`
- Create: `alpha_forge/app/agents/llm_config.py`
- Modify: `alpha_forge/app/agents/llm_client.py`
- Modify: `alpha_forge/app/agents/base_judge.py:29-30`
- Modify: `alpha_forge/app/agents/researcher.py:19,25-26`
- Test: `tests/unit/agents/test_llm_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/agents/test_llm_config.py
"""Tests for tiered LLM configuration."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from alpha_forge.app.agents.llm_config import LLMConfig, get_client_for_role


class TestLLMConfig:
    def test_loads_from_yaml(self, tmp_path: Path) -> None:
        config_data = {
            "providers": {
                "anthropic": {"api_key_env": "ANTHROPIC_API_KEY"},
            },
            "tiers": {
                "heavy": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
            },
            "roles": {
                "researcher": "heavy",
            },
        }
        path = tmp_path / "llm.yaml"
        path.write_text(yaml.dump(config_data))

        config = LLMConfig.from_yaml(path)
        assert config.roles["researcher"] == "heavy"
        assert config.tiers["heavy"].model == "claude-sonnet-4-20250514"

    def test_get_tier_for_role(self, tmp_path: Path) -> None:
        config_data = {
            "providers": {
                "anthropic": {"api_key_env": "ANTHROPIC_API_KEY"},
                "openai": {"api_key_env": "OPENAI_API_KEY", "base_url": "https://api.openai.com/v1"},
            },
            "tiers": {
                "heavy": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
                "light": {"provider": "openai", "model": "gpt-4o-mini"},
            },
            "roles": {
                "researcher": "heavy",
                "leakage_judge": "light",
            },
        }
        path = tmp_path / "llm.yaml"
        path.write_text(yaml.dump(config_data))

        config = LLMConfig.from_yaml(path)
        heavy = config.get_tier("researcher")
        light = config.get_tier("leakage_judge")
        assert heavy.provider == "anthropic"
        assert light.provider == "openai"
        assert light.model == "gpt-4o-mini"

    def test_fallback_when_no_config(self) -> None:
        config = LLMConfig.default()
        tier = config.get_tier("researcher")
        assert tier.provider == "anthropic"
        assert "claude" in tier.model


class TestGetClientForRole:
    def test_returns_client_with_correct_model(self, tmp_path: Path, monkeypatch) -> None:
        config_data = {
            "providers": {
                "anthropic": {"api_key_env": "ANTHROPIC_API_KEY"},
            },
            "tiers": {
                "heavy": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
            },
            "roles": {"researcher": "heavy"},
        }
        path = tmp_path / "llm.yaml"
        path.write_text(yaml.dump(config_data))

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        client = get_client_for_role("researcher", config_path=path)
        assert client.model == "claude-sonnet-4-20250514"
        assert client.provider == "anthropic"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/agents/test_llm_config.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create configs/llm.yaml**

```yaml
# configs/llm.yaml
providers:
  anthropic:
    api_key_env: ANTHROPIC_API_KEY
    # base_url: optional, defaults to Anthropic API
  openai:
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1

tiers:
  heavy:
    provider: anthropic
    model: claude-sonnet-4-20250514
  light:
    provider: openai
    model: gpt-4o-mini

roles:
  researcher: heavy
  seed_judge: heavy
  leakage_judge: light
  overfit_judge: heavy
  realism_judge: light
  code_judge: light
  result_judge: heavy
  mutation_judge: heavy
```

- [ ] **Step 4: Implement LLMConfig**

```python
# alpha_forge/app/agents/llm_config.py
"""Tiered LLM configuration: maps agent roles to model providers."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "llm.yaml"
DEFAULT_MODEL = "claude-sonnet-4-20250514"


class ProviderConfig(BaseModel):
    api_key_env: str
    base_url: str | None = None


class TierConfig(BaseModel):
    provider: str
    model: str


class LLMConfig(BaseModel):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    tiers: dict[str, TierConfig] = Field(default_factory=dict)
    roles: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> LLMConfig:
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)

    @classmethod
    def default(cls) -> LLMConfig:
        return cls(
            providers={"anthropic": ProviderConfig(api_key_env="ANTHROPIC_API_KEY")},
            tiers={"heavy": TierConfig(provider="anthropic", model=DEFAULT_MODEL)},
            roles={
                "researcher": "heavy",
                "seed_judge": "heavy",
                "leakage_judge": "heavy",
                "overfit_judge": "heavy",
                "realism_judge": "heavy",
                "code_judge": "heavy",
                "result_judge": "heavy",
                "mutation_judge": "heavy",
            },
        )

    def get_tier(self, role: str) -> TierConfig:
        tier_name = self.roles.get(role, "heavy")
        return self.tiers.get(tier_name, TierConfig(provider="anthropic", model=DEFAULT_MODEL))

    def get_provider(self, role: str) -> ProviderConfig:
        tier = self.get_tier(role)
        return self.providers.get(tier.provider, ProviderConfig(api_key_env="ANTHROPIC_API_KEY"))


# Module-level singleton (loaded lazily)
_config: LLMConfig | None = None


def load_config(path: Path | None = None) -> LLMConfig:
    global _config
    if _config is not None and path is None:
        return _config
    config_path = path or DEFAULT_CONFIG_PATH
    if config_path.exists():
        _config = LLMConfig.from_yaml(config_path)
    else:
        _config = LLMConfig.default()
    return _config


def get_client_for_role(role: str, config_path: Path | None = None):
    """Factory: return an LLMClient configured for the given role."""
    from alpha_forge.app.agents.llm_client import LLMClient

    config = load_config(config_path)
    tier = config.get_tier(role)
    provider_config = config.get_provider(role)

    return LLMClient(
        model=tier.model,
        provider=tier.provider,
        base_url=provider_config.base_url,
    )
```

- [ ] **Step 5: Update LLMClient for multi-provider support**

In `alpha_forge/app/agents/llm_client.py`, update `__init__` and `call` methods:

```python
class LLMClient:
    """Multi-provider LLM client for judge and researcher calls."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        provider: str = "anthropic",
        base_url: str | None = None,
        stream_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self.stream_callback = stream_callback

        if provider == "openai":
            import openai
            kwargs = {}
            if base_url:
                kwargs["base_url"] = base_url
            self.openai_client = openai.OpenAI(**kwargs)
            self.anthropic_client = None
        else:
            self.anthropic_client = anthropic.Anthropic()
            self.openai_client = None
```

Update `call()` method to dispatch to correct provider and support streaming. Update `call_json()` to add retry with exponential backoff for network errors.

- [ ] **Step 6: Update base_judge.py**

In `alpha_forge/app/agents/base_judge.py`, line 29-30:
```python
    def __init__(self, client: LLMClient | None = None, role: str | None = None) -> None:
        if client:
            self.client = client
        elif role:
            from alpha_forge.app.agents.llm_config import get_client_for_role
            self.client = get_client_for_role(role)
        else:
            self.client = LLMClient()
```

Each judge subclass should pass its `role` name. E.g., in `judge_leakage.py`:
```python
    def __init__(self, client: LLMClient | None = None) -> None:
        super().__init__(client=client, role="leakage_judge")
```

- [ ] **Step 7: Update researcher.py**

Remove `RESEARCHER_MODEL` constant. Update `__init__`:
```python
    def __init__(self, client: LLMClient | None = None) -> None:
        if client:
            self.client = client
        else:
            from alpha_forge.app.agents.llm_config import get_client_for_role
            self.client = get_client_for_role("researcher")
```

- [ ] **Step 8: Run tests**

Run: `pytest tests/unit/agents/test_llm_config.py tests/unit/agents/test_llm_client.py -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add configs/llm.yaml alpha_forge/app/agents/llm_config.py \
  alpha_forge/app/agents/llm_client.py alpha_forge/app/agents/base_judge.py \
  alpha_forge/app/agents/researcher.py tests/unit/agents/test_llm_config.py
git commit -m "feat: tiered LLM config with multi-provider support"
```

---

## Task 5: LLM streaming and retry

**Note:** Task 4 changed `LLMClient.__init__` signature (added `provider`, `base_url`). This task adds streaming and retry to the SAME file. Both tasks modify `llm_client.py` — Task 4 does the constructor and multi-provider dispatch, Task 5 adds streaming + retry on top. Run Task 4 tests after Task 5 to confirm nothing broke.

**Files:**
- Modify: `alpha_forge/app/agents/llm_client.py` (already modified in Task 4)
- Test: `tests/unit/agents/test_llm_client_v2.py`

- [ ] **Step 1: Write failing tests for streaming and retry**

```python
# tests/unit/agents/test_llm_client_v2.py
"""Tests for LLM streaming and retry behavior."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from alpha_forge.app.agents.llm_client import LLMClient


class TestStreamCallback:
    @patch("alpha_forge.app.agents.llm_client.anthropic")
    def test_streaming_calls_callback(self, mock_anthropic) -> None:
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.text_stream = iter(["Hello", " world"])
        mock_stream.get_final_text.return_value = "Hello world"

        mock_client = MagicMock()
        mock_client.messages.stream.return_value = mock_stream
        mock_anthropic.Anthropic.return_value = mock_client

        tokens = []
        client = LLMClient(stream_callback=lambda t: tokens.append(t))
        result = client.call("system", "user")

        assert result == "Hello world"
        assert tokens == ["Hello", " world"]

    @patch("alpha_forge.app.agents.llm_client.anthropic")
    def test_no_callback_uses_batch(self, mock_anthropic) -> None:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="batch response")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.Anthropic.return_value = mock_client

        client = LLMClient()
        result = client.call("system", "user")

        assert result == "batch response"
        mock_client.messages.create.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/agents/test_llm_client_v2.py -v`
Expected: FAIL — `stream_callback` not yet implemented in `call()`

- [ ] **Step 3: Implement streaming in LLMClient**

The `call()` method in `llm_client.py` should accept an optional `stream_callback` parameter and dispatch to provider-specific streaming:

```python
    def call(
        self,
        system: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        stream_callback: Callable[[str], None] | None = None,
    ) -> str:
        cb = stream_callback or self.stream_callback
        return self._retry_call(self._do_call, system, user_prompt, max_tokens, temperature, cb)

    def _do_call(self, system, user_prompt, max_tokens, temperature, cb):
        if self.provider == "anthropic":
            return self._call_anthropic(system, user_prompt, max_tokens, temperature, cb)
        return self._call_openai(system, user_prompt, max_tokens, temperature, cb)

    def _call_anthropic(self, system, user_prompt, max_tokens, temperature, cb):
        if cb:
            with self.anthropic_client.messages.stream(
                model=self.model, max_tokens=max_tokens, temperature=temperature,
                system=system, messages=[{"role": "user", "content": user_prompt}],
            ) as stream:
                for token in stream.text_stream:
                    cb(token)
                return stream.get_final_text()
        else:
            response = self.anthropic_client.messages.create(
                model=self.model, max_tokens=max_tokens, temperature=temperature,
                system=system, messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text

    def _call_openai(self, system, user_prompt, max_tokens, temperature, cb):
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}]
        if cb:
            stream = self.openai_client.chat.completions.create(
                model=self.model, max_tokens=max_tokens, temperature=temperature,
                messages=msgs, stream=True,
            )
            chunks = []
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    cb(token)
                    chunks.append(token)
            return "".join(chunks)
        else:
            response = self.openai_client.chat.completions.create(
                model=self.model, max_tokens=max_tokens, temperature=temperature, messages=msgs,
            )
            return response.choices[0].message.content
```

- [ ] **Step 4: Add network retry with exponential backoff**

```python
import time

NETWORK_RETRIES = 3
BACKOFF_BASE = 1  # 1s, 4s, 16s

    def _retry_call(self, fn, *args, **kwargs):
        for attempt in range(NETWORK_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                if attempt == NETWORK_RETRIES or not self._is_retriable(e):
                    raise
                wait = BACKOFF_BASE * (4 ** attempt)
                logger.warning("LLM call failed (attempt %d), retrying in %ds: %s", attempt + 1, wait, e)
                time.sleep(wait)

    @staticmethod
    def _is_retriable(e: Exception) -> bool:
        """Check if exception is a transient network/API error."""
        err_str = str(e).lower()
        if isinstance(e, (ConnectionError, TimeoutError)):
            return True
        if "429" in err_str or "500" in err_str or "502" in err_str or "503" in err_str:
            return True
        return False
```

- [ ] **Step 5: Run ALL LLM client tests (Task 4 + Task 5)**

Run: `pytest tests/unit/agents/test_llm_config.py tests/unit/agents/test_llm_client.py tests/unit/agents/test_llm_client_v2.py -v`
Expected: All PASS (confirms Task 4 changes are still intact)

- [ ] **Step 6: Commit**

```bash
git add alpha_forge/app/agents/llm_client.py tests/unit/agents/test_llm_client_v2.py
git commit -m "feat: LLM streaming callback and network retry with backoff"
```

---

## Task 6: Code snapshot support in ArtifactStore

**Files:**
- Modify: `alpha_forge/app/storage/artifact_store.py`
- Test: `tests/unit/storage/test_code_snapshot.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/storage/test_code_snapshot.py
"""Tests for code snapshot save/load in ArtifactStore."""
from __future__ import annotations

from alpha_forge.app.storage.artifact_store import ArtifactStore


class TestCodeSnapshot:
    def test_save_and_load(self, tmp_path) -> None:
        store = ArtifactStore(tmp_path / "alpha_research")
        files = {"features.py": "def compute_features(): pass", "signal_combiner.py": "def combine(): pass"}
        store.save_code_snapshot("fam_001", 1, files)

        loaded = store.load_code_snapshot("fam_001", 1)
        assert loaded == files

    def test_load_missing_returns_none(self, tmp_path) -> None:
        store = ArtifactStore(tmp_path / "alpha_research")
        assert store.load_code_snapshot("fam_001", 99) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/storage/test_code_snapshot.py -v`
Expected: FAIL — `save_code_snapshot` not found

- [ ] **Step 3: Add methods to ArtifactStore**

Append to `alpha_forge/app/storage/artifact_store.py`:

```python
    # ------------------------------------------------------------------
    # Code snapshots (for TUI diff view)
    # ------------------------------------------------------------------

    def save_code_snapshot(
        self,
        family_id: str,
        iteration: int,
        files: dict[str, str],
    ) -> Path:
        """Save research code snapshot for an iteration."""
        path = self._reports_dir(family_id) / f"iter_{iteration}_code.json"
        self._atomic_write_json(path, files)
        return path

    def load_code_snapshot(
        self,
        family_id: str,
        iteration: int,
    ) -> dict[str, str] | None:
        """Load research code snapshot for an iteration."""
        path = self._reports_dir(family_id) / f"iter_{iteration}_code.json"
        if not path.exists():
            return None
        return self.load_json(path)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/storage/test_code_snapshot.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add alpha_forge/app/storage/artifact_store.py tests/unit/storage/test_code_snapshot.py
git commit -m "feat: code snapshot save/load in ArtifactStore for TUI diff view"
```

---

## Task 7: Subprocess runner for process isolation

**Files:**
- Create: `alpha_forge/tui/__init__.py`
- Create: `alpha_forge/tui/workers/__init__.py` (empty marker needed for package)
- Create: `alpha_forge/tui/workers/subprocess_runner.py`
- Modify: `configs/guardrails.yaml`
- Test: `tests/unit/tui/__init__.py`
- Test: `tests/unit/tui/test_subprocess_runner.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/tui/test_subprocess_runner.py
"""Tests for process-isolated backtest/robustness runner."""
from __future__ import annotations

import pytest

from alpha_forge.tui.workers.subprocess_runner import SubprocessRunner, SubprocessResult


class TestSubprocessRunner:
    def test_successful_run(self) -> None:
        def target_fn(x, y):
            return {"result": x + y}

        runner = SubprocessRunner(timeout_seconds=5)
        result = runner.run(target_fn, args=(2, 3))

        assert result.success is True
        assert result.data["result"] == 5
        assert result.failure_type is None

    def test_timeout_returns_infrastructure_failure(self) -> None:
        import time
        def slow_fn():
            time.sleep(10)
            return {}

        runner = SubprocessRunner(timeout_seconds=1)
        result = runner.run(slow_fn)

        assert result.success is False
        assert result.failure_type == "infrastructure"
        assert "timeout" in result.error.lower()

    def test_exception_returns_research_failure(self) -> None:
        def bad_fn():
            raise ValueError("bad data")

        runner = SubprocessRunner(timeout_seconds=5)
        result = runner.run(bad_fn)

        assert result.success is False
        assert result.failure_type == "research"
        assert "bad data" in result.error

    def test_oom_classified_as_infrastructure(self) -> None:
        def oom_fn():
            # Simulate by raising MemoryError
            raise MemoryError("out of memory")

        runner = SubprocessRunner(timeout_seconds=5)
        result = runner.run(oom_fn)

        assert result.success is False
        assert result.failure_type == "infrastructure"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/tui/test_subprocess_runner.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create package markers and implement SubprocessRunner**

Create empty `__init__.py` files:
- `alpha_forge/tui/__init__.py`
- `alpha_forge/tui/workers/__init__.py`
- `tests/unit/tui/__init__.py`

```python
# alpha_forge/tui/workers/subprocess_runner.py
"""Process-isolated runner for backtest and robustness operations.

Protects the TUI/orchestrator from DuckDB OOM or crashes.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import traceback
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class SubprocessResult:
    """Result from a subprocess execution."""
    success: bool
    data: dict[str, Any] | None = None
    error: str = ""
    failure_type: str | None = None  # "infrastructure" or "research"


class SubprocessRunner:
    """Runs functions in child processes with resource limits."""

    def __init__(
        self,
        timeout_seconds: int = 300,
        max_memory_mb: int | None = None,
        duckdb_threads: int | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_memory_mb = max_memory_mb
        self.duckdb_threads = duckdb_threads

    def run(self, fn: Callable, args: tuple = (), kwargs: dict | None = None) -> SubprocessResult:
        """Run fn(*args, **kwargs) in a child process.

        Returns SubprocessResult with success/failure classification.
        """
        kwargs = kwargs or {}
        result_queue: multiprocessing.Queue = multiprocessing.Queue()

        def _worker(q, f, a, kw):
            try:
                # Apply DuckDB thread limit if configured
                if self.duckdb_threads:
                    os.environ["DUCKDB_THREADS"] = str(self.duckdb_threads)
                # Apply memory limit via DuckDB pragma env var
                if self.max_memory_mb:
                    os.environ["DUCKDB_MEMORY_LIMIT"] = f"{self.max_memory_mb}MB"
                result = f(*a, **kw)
                q.put(("success", result))
            except MemoryError as e:
                q.put(("infrastructure", str(e)))
            except Exception as e:
                q.put(("research", f"{type(e).__name__}: {e}"))

        process = multiprocessing.Process(target=_worker, args=(result_queue, fn, args, kwargs))
        process.start()
        process.join(timeout=self.timeout_seconds)

        if process.is_alive():
            process.kill()
            process.join(timeout=5)
            return SubprocessResult(
                success=False,
                error=f"Timeout after {self.timeout_seconds}s",
                failure_type="infrastructure",
            )

        if process.exitcode != 0 and result_queue.empty():
            return SubprocessResult(
                success=False,
                error=f"Process crashed with exit code {process.exitcode}",
                failure_type="infrastructure",
            )

        if result_queue.empty():
            return SubprocessResult(
                success=False,
                error="No result from subprocess",
                failure_type="infrastructure",
            )

        status, payload = result_queue.get_nowait()
        if status == "success":
            return SubprocessResult(success=True, data=payload)
        else:
            return SubprocessResult(
                success=False,
                error=payload,
                failure_type=status,
            )
```

- [ ] **Step 4: Add resource_limits to guardrails.yaml**

Append to `configs/guardrails.yaml`:

```yaml
resource_limits:
  backtest_max_memory_mb: 4096
  backtest_timeout_seconds: 300
  duckdb_threads: 2
  robustness_max_memory_mb: 4096
  robustness_timeout_seconds: 600
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/tui/test_subprocess_runner.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add alpha_forge/tui/__init__.py alpha_forge/tui/workers/__init__.py \
  alpha_forge/tui/workers/subprocess_runner.py configs/guardrails.yaml \
  tests/unit/tui/__init__.py tests/unit/tui/test_subprocess_runner.py
git commit -m "feat: process-isolated subprocess runner for backtest/robustness"
```

---

## Task 8: EventBus integration into orchestrator and family_flow

**Files:**
- Modify: `alpha_forge/app/workflow/orchestrator.py`
- Modify: `alpha_forge/app/workflow/family_flow.py`

This task wires the EventBus into the existing workflow so events are emitted at each step. No tests needed beyond existing tests still passing — the bus is optional (`bus=None` is the default, preserving backward compat).

- [ ] **Step 1: Update FamilyFlow to accept and use EventBus**

In `alpha_forge/app/workflow/family_flow.py`:

Add `bus` parameter to `__init__`:
```python
    def __init__(
        self,
        store: MarkdownStore,
        artifact_store: ArtifactStore,
        configs_dir: str | Path = "configs",
        client: LLMClient | None = None,
        bus=None,  # EventBus | None
    ) -> None:
        ...
        self.bus = bus
```

Add helper method:
```python
    def _emit(self, event: str, data: dict) -> None:
        if self.bus:
            self.bus.emit_sync(event, data)
```

Add event emissions throughout `run_iteration()`:
- After plan draft (line ~107): `self._emit("stage_changed", {"stage": "DRAFT_PLAN", ...})`
- After tier-1 judge (line ~126): `self._emit("verdict_received", {...})` + gate_for_override call
- After code write (line ~157): `self._emit("stage_changed", {"stage": "CODE_WRITE", ...})`
- After tier-2 judge (line ~178): `self._emit("verdict_received", {...})` + gate_for_override call
- After guards (line ~206): `self._emit("guards_complete", {...})`
- After backtest (line ~238): `self._emit("backtest_complete", {...})`
- After robustness (line ~256): `self._emit("robustness_complete", {...})`
- After tier-3 judge (line ~276): `self._emit("verdict_received", {...})` + gate_for_override call
- After scoring (line ~282): `self._emit("score_computed", {...})`
- At end: `self._emit("iteration_complete", {...})`

Also add code snapshot save before code write:
```python
# Before overwriting research files with new code:
old_code = self._read_research_code_dict(family_id)
if old_code:
    self.artifact_store.save_code_snapshot(family_id, iter_num - 1, old_code)
```

- [ ] **Step 2: Update Orchestrator to accept and pass EventBus**

In `alpha_forge/app/workflow/orchestrator.py`:

Add `bus` parameter:
```python
    def __init__(
        self,
        store: MarkdownStore,
        artifact_store: ArtifactStore,
        configs_dir: str | Path = "configs",
        client: LLMClient | None = None,
        max_iterations: int = MAX_ITERATIONS,
        bus=None,  # EventBus | None
    ) -> None:
        ...
        self.bus = bus
        self.flow = FamilyFlow(store, artifact_store, configs_dir, self.client, bus=bus)
        self._paused = False
```

Add pause support in the `while` loop:
```python
    # At top of while loop, after reading family:
    if self._paused:
        if self.bus:
            self.bus.emit_sync("loop_paused", {"family_id": family_id})
        break
```

Add `pause()` and `resume()` methods:
```python
    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
```

- [ ] **Step 3: Run existing tests to verify backward compat**

Run: `pytest tests/ -v`
Expected: All existing tests still PASS (bus=None is default)

- [ ] **Step 4: Commit**

```bash
git add alpha_forge/app/workflow/orchestrator.py alpha_forge/app/workflow/family_flow.py
git commit -m "feat: wire EventBus into orchestrator and family_flow"
```

---

## Task 9: TUI Textual CSS theme

**Files:**
- Create: `alpha_forge/tui/styles/theme.tcss`

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p alpha_forge/tui/styles alpha_forge/tui/screens alpha_forge/tui/widgets
touch alpha_forge/tui/screens/__init__.py alpha_forge/tui/widgets/__init__.py
```

- [ ] **Step 2: Write the theme**

```css
/* alpha_forge/tui/styles/theme.tcss */
Screen {
    background: $surface;
}

#sidebar {
    width: 26;
    border-right: solid $primary;
    padding: 1;
}

#main-area {
    width: 1fr;
}

#conversation {
    height: 2fr;
    border-bottom: solid $primary;
}

#bottom-tabs {
    height: 1fr;
}

#status-bar {
    dock: bottom;
    height: 1;
    background: $primary;
    color: $text;
}

.pipeline-step {
    height: 1;
}

.pipeline-step.completed {
    color: $success;
}

.pipeline-step.active {
    color: $warning;
}

.pipeline-step.pending {
    color: $text-muted;
}

.verdict-approve {
    color: $success;
}

.verdict-approve-constraints {
    color: $warning;
}

.verdict-revise {
    color: #f0883e;
}

.verdict-reject {
    color: $error;
}

.mode-autopilot {
    background: $primary;
    color: $text;
}

.mode-semi-auto {
    background: #f0883e;
    color: $text;
}

.strike-active {
    color: $error;
}

.strike-inactive {
    color: $text-muted;
}

.risk-low {
    color: $success;
}

.risk-medium {
    color: $warning;
}

.risk-high {
    color: $error;
}
```

- [ ] **Step 3: Commit**

```bash
git add alpha_forge/tui/styles/ alpha_forge/tui/screens/__init__.py alpha_forge/tui/widgets/__init__.py
git commit -m "feat: TUI Textual CSS theme and package structure"
```

---

## Task 10: TUI widgets — state sidebar

**Files:**
- Create: `alpha_forge/tui/widgets/state_sidebar.py`

- [ ] **Step 1: Implement state sidebar widget**

```python
# alpha_forge/tui/widgets/state_sidebar.py
"""Left sidebar: pipeline steps, family info, family selector."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView, Static

from alpha_forge.app.domain.models import IdeaFamily
from alpha_forge.app.domain.states import IterationStage

PIPELINE_STEPS = [
    IterationStage.DRAFT_PLAN,
    IterationStage.PLAN_JUDGED,
    IterationStage.CODE_WRITE,
    IterationStage.CODE_JUDGED,
    IterationStage.RUN_GUARDS,
    IterationStage.RUN_BACKTEST,
    IterationStage.RUN_ROBUSTNESS,
    IterationStage.RESULT_JUDGED,
]


class PipelineView(Static):
    """Shows iteration stages as a vertical checklist."""

    def __init__(self) -> None:
        super().__init__()
        self._current_stage: IterationStage | None = None

    def update_stage(self, stage: IterationStage) -> None:
        self._current_stage = stage
        self._render_pipeline()

    def _render_pipeline(self) -> None:
        lines = []
        reached = False
        for step in PIPELINE_STEPS:
            if self._current_stage and step == self._current_stage:
                lines.append(f"  [bold yellow]▸ {step.value}[/]")
                reached = True
            elif not reached and self._current_stage:
                # Check if current stage is past this step
                if PIPELINE_STEPS.index(step) < PIPELINE_STEPS.index(self._current_stage):
                    lines.append(f"  [green]✓ {step.value}[/]")
                else:
                    lines.append(f"  [dim]○ {step.value}[/]")
            else:
                lines.append(f"  [dim]○ {step.value}[/]")
        self.update("\n".join(lines))


class FamilyInfo(Static):
    """Shows current family metadata."""

    def update_family(self, family: IdeaFamily) -> None:
        strikes = "".join(
            "[red]●[/]" if i < family.strike_count else "[dim]○[/]"
            for i in range(3)
        )
        budget = family.mutation_budget
        self.update(
            f"  [bold]{family.family_id}[/]\n"
            f"  Iteration: {family.current_iteration}\n"
            f"  Seed: {family.seed_id}\n"
            f"  State: {family.state}\n"
            f"\n"
            f"  Strikes: {strikes}\n"
            f"  Best: {family.best_qualified_score:.2f}\n"
            f"  Budget: H:{budget.horizon} V:{budget.venue}\n"
        )


class StateSidebar(Vertical):
    """Left sidebar combining family info, pipeline, and family list."""

    def compose(self) -> ComposeResult:
        yield FamilyInfo(id="family-info")
        yield Label("Pipeline:", id="pipeline-label")
        yield PipelineView(id="pipeline-view")
        yield Label("─" * 20)
        yield Label("Families:")
        yield ListView(id="family-list")

    def update_family(self, family: IdeaFamily) -> None:
        self.query_one("#family-info", FamilyInfo).update_family(family)

    def update_stage(self, stage: IterationStage) -> None:
        self.query_one("#pipeline-view", PipelineView).update_stage(stage)

    def set_families(self, families: list[str], active: str) -> None:
        lv = self.query_one("#family-list", ListView)
        lv.clear()
        for fid in families:
            prefix = "▶ " if fid == active else "  "
            lv.append(ListItem(Label(f"{prefix}{fid}")))
```

- [ ] **Step 2: Commit**

```bash
git add alpha_forge/tui/widgets/state_sidebar.py
git commit -m "feat: TUI state sidebar widget with pipeline view and family info"
```

---

## Task 11: TUI widgets — conversation stream

**Files:**
- Create: `alpha_forge/tui/widgets/conversation.py`

- [ ] **Step 1: Implement conversation widget**

```python
# alpha_forge/tui/widgets/conversation.py
"""Streaming LLM conversation log widget."""
from __future__ import annotations

from rich.text import Text
from textual.widgets import RichLog

VERDICT_COLORS = {
    "approve": "green",
    "approve_with_constraints": "yellow",
    "revise": "#f0883e",
    "reject": "red",
    "fork_required": "magenta",
}


class ConversationStream(RichLog):
    """Auto-scrolling conversation log showing LLM and judge output."""

    def __init__(self, **kwargs) -> None:
        super().__init__(highlight=True, markup=True, auto_scroll=True, wrap=True, **kwargs)

    def add_iteration_header(self, iteration_id: str) -> None:
        self.write(Text(f"── {iteration_id} " + "─" * 40, style="dim"))

    def add_researcher_token(self, token: str) -> None:
        """Append a streaming token from the researcher."""
        self.write(token, shrink=False, scroll_end=True)

    def add_researcher_message(self, label: str, text: str) -> None:
        self.write(Text(f"\n🔬 {label}", style="bold cyan"))
        self.write(text)

    def add_verdict(self, judge_type: str, verdict: str, reasoning: str, must_fix: list[str]) -> None:
        color = VERDICT_COLORS.get(verdict, "white")
        self.write(Text(f"\n⚖ {judge_type.title()} Judge ", style="bold magenta") + Text(verdict.upper(), style=f"bold {color}"))
        if reasoning:
            self.write(reasoning)
        if must_fix:
            self.write(Text("  must_fix: " + str(must_fix), style="dim"))

    def add_override_prompt(self, verdict: str, judge_type: str) -> None:
        self.write("")
        self.write(Text(" SEMI-AUTO ", style="bold on #f0883e") + Text(f" Override {judge_type} verdict ({verdict})?", style="#f0883e"))
        self.write("  [a] Accept verdict")
        self.write("  [o] Override → APPROVE")
        self.write("  [r] Override → REJECT")
        self.write("  [v] Override → REVISE with custom feedback")
        self.write("  [s] Skip to autopilot")

    def add_system_message(self, text: str) -> None:
        self.write(Text(f"\n{text}", style="dim italic"))
```

- [ ] **Step 2: Commit**

```bash
git add alpha_forge/tui/widgets/conversation.py
git commit -m "feat: TUI conversation stream widget with verdict coloring"
```

---

## Task 12: TUI widgets — bottom tabbed panels (metrics, code, verdicts, guards, log)

**Files:**
- Create: `alpha_forge/tui/widgets/metrics_panel.py`
- Create: `alpha_forge/tui/widgets/code_panel.py`
- Create: `alpha_forge/tui/widgets/verdicts_panel.py`
- Create: `alpha_forge/tui/widgets/guards_panel.py`
- Create: `alpha_forge/tui/widgets/log_panel.py`

- [ ] **Step 1: Implement all five tab panels**

Each panel is a Textual widget that receives data via update methods and renders accordingly. See spec for each tab's content:
- **MetricsPanel**: DataTable with per-symbol backtest results + composite score
- **CodePanel**: Syntax-highlighted code viewer via RichLog + diff toggle. Diff computed using `difflib.unified_diff()` against snapshots from `ArtifactStore.load_code_snapshot(family_id, iteration - 1)`. A keybinding `d` toggles between full file and diff view.
- **VerdictsPanel**: Collapsible judge output cards with risk levels
- **GuardsPanel**: 5 guard checks with pass/fail badges
- **LogPanel**: RichLog with log handler that captures Python logging output

Implementation details for each are straightforward Textual widget patterns. Key imports:
- `textual.widgets.DataTable` for metrics
- `textual.widgets.RichLog` with `rich.syntax.Syntax` for code
- `textual.widgets.Collapsible` for verdicts
- `textual.widgets.Static` for guards
- `textual.widgets.RichLog` with custom `logging.Handler` for log

- [ ] **Step 2: Commit**

```bash
git add alpha_forge/tui/widgets/metrics_panel.py alpha_forge/tui/widgets/code_panel.py \
  alpha_forge/tui/widgets/verdicts_panel.py alpha_forge/tui/widgets/guards_panel.py \
  alpha_forge/tui/widgets/log_panel.py
git commit -m "feat: TUI bottom tab panels — metrics, code, verdicts, guards, log"
```

---

## Task 13: TUI widgets — override modal and command palette

**Files:**
- Create: `alpha_forge/tui/widgets/override_modal.py`
- Create: `alpha_forge/tui/widgets/command_palette.py`

- [ ] **Step 1: Implement override modal**

Override modal appears when semi-auto mode triggers. Shows verdict info and key options (a/o/r/v/s). Option `v` opens a TextArea for custom feedback. Returns decision dict to EventBus via `release_gate()`.

- [ ] **Step 2: Implement command palette**

A Textual `Input` widget activated by `/` key. Parses and dispatches these commands:

| Command | Implementation |
|---------|---------------|
| `/seed <text>` | Spawns a second `threading.Thread` running `ingest_seed() → distill_seed() → screen_seed() → create_family()`. Does NOT block the main loop worker. On completion, emits `seed_processed` event to update sidebar family list. |
| `/strike reset` | Calls `reset_strikes(family)` on the active family — clears `strike_count`, `red_strike_count`, `overfit_flag_history`, `score_history`. Preserves `strike_history`. |
| `/override <verdict>` | Forces a verdict on the current stage. Only works when the loop is at a verdict gate (semi-auto) or paused. Calls `bus.release_gate({"action": "override", "verdict": verdict})`. |
| `/family <id>` | Switches the active family in the sidebar. If loop is running, pauses first. |
| `/config` | Displays current guardrails, universe, costs, and LLM tier assignments in a modal. |
| `/history` | Shows iteration history for current family (reads HISTORY.md) in a scrollable modal. |
| `/export` | Exports current iteration data as JSON to `exports/` directory. |
| `/retry` | Re-runs the last failed subprocess (backtest/robustness). Only available after infrastructure failure. |
| `/threads <N>` | Updates `duckdb_threads` in the subprocess runner config for next run. |
| `/tier <role> <tier>` | Calls `load_config()` to get the singleton `LLMConfig`, updates `config.roles[role] = tier`, then resets `_config = None` so `get_client_for_role()` picks up the change on next call. Live — takes effect on the next LLM call for that role. |

- [ ] **Step 3: Commit**

```bash
git add alpha_forge/tui/widgets/override_modal.py alpha_forge/tui/widgets/command_palette.py
git commit -m "feat: TUI override modal and command palette widgets"
```

---

## Task 14: TUI main screen and app composition

**Files:**
- Create: `alpha_forge/tui/screens/main_screen.py`
- Create: `alpha_forge/tui/app.py`
- Create: `alpha_forge/tui/workers/loop_worker.py`

- [ ] **Step 1: Implement main screen layout**

Composes the IDE-style layout: `StateSidebar` on left, `ConversationStream` top-right, `TabbedContent` bottom-right (containing all 5 tab panels), status bar at bottom.

- [ ] **Step 2: Implement loop worker**

Background worker (using Textual `run_worker`) that creates `Orchestrator` with EventBus, runs it in a thread. Handles pause/resume via orchestrator methods.

- [ ] **Step 3: Implement the Textual App**

`AlphaForgeApp(App)` with:
- Keybindings: `shift+tab` (mode toggle), `tab` (focus cycle), `1-5` (tabs), `/` (command palette), `f` (family selector), `p` (pause), `q` (quit)
- EventBus subscriber setup: wire all events to widget update methods
- Status bar showing mode badge and keybinding hints
- Mode state (`autopilot` / `semi_auto`)

- [ ] **Step 4: Commit**

```bash
git add alpha_forge/tui/screens/main_screen.py alpha_forge/tui/app.py \
  alpha_forge/tui/workers/loop_worker.py
git commit -m "feat: TUI main screen, app composition, and loop worker"
```

---

## Task 15: CLI entry point

**Files:**
- Create: `alpha_forge/scripts/run_tui.py`

- [ ] **Step 1: Create the entry point**

```python
# alpha_forge/scripts/run_tui.py
"""CLI entry point for the Alpha Forge TUI."""
import click

from alpha_forge.tui.app import AlphaForgeApp


@click.command()
@click.option("--workspace", default="alpha_research", help="Workspace root directory")
@click.option("--configs", default="configs", help="Configs directory")
@click.option("--family", default=None, help="Start with specific family")
@click.option("--max-iterations", default=10, help="Max iterations per run")
def main(workspace: str, configs: str, family: str | None, max_iterations: int) -> None:
    """Launch the Alpha Forge TUI dashboard."""
    app = AlphaForgeApp(
        workspace=workspace,
        configs_dir=configs,
        family_id=family,
        max_iterations=max_iterations,
    )
    app.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test that it launches**

Run: `python -m alpha_forge.scripts.run_tui --help`
Expected: Shows help text with options

- [ ] **Step 3: Commit**

```bash
git add alpha_forge/scripts/run_tui.py
git commit -m "feat: TUI CLI entry point via run_tui.py"
```

---

## Task 16: Integration testing and polish

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Manual smoke test the TUI**

Run: `python -m alpha_forge.scripts.run_tui --workspace alpha_research --configs configs`

Verify:
- TUI launches and shows IDE-style layout
- Sidebar shows pipeline steps and family info
- Conversation panel is ready for streaming
- Bottom tabs switch with 1-5 keys
- Shift+Tab toggles mode badge between AUTOPILOT and SEMI-AUTO
- `p` pauses, `/` opens command palette
- `q` quits cleanly

- [ ] **Step 3: Commit any fixes from smoke test**

```bash
git add -A
git commit -m "fix: polish from TUI smoke testing"
```

---

## Verification Plan

After all tasks complete:

1. `pytest tests/ -v` — all unit tests pass
2. Launch TUI with `--workspace` pointing to a test workspace
3. Start a family iteration — verify LLM tokens stream to conversation panel
4. Switch to semi-auto — verify override prompt appears at verdict gates
5. Override a verdict with custom feedback — verify researcher receives it
6. Switch tabs — verify metrics, code, verdicts, guards, log all render
7. Test `/config` — verify LLM tier assignments display
8. Test `/tier leakage_judge heavy` — verify live tier switching
9. Test pause (`p`) — verify loop stops at next iteration boundary
10. Kill a backtest subprocess — verify TUI shows infrastructure failure, no strike
