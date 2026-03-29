"""Tests for process-isolated backtest/robustness runner."""
from __future__ import annotations

import time

from alpha_forge.tui.workers.subprocess_runner import SubprocessRunner


def target_add(x: int, y: int) -> dict[str, int]:
    return {"result": x + y}


def slow_target() -> dict[str, bool]:
    time.sleep(10)
    return {}


def bad_target() -> dict[str, bool]:
    raise ValueError("bad data")


def oom_target() -> dict[str, bool]:
    raise MemoryError("out of memory")


class TestSubprocessRunner:
    def test_successful_run(self) -> None:
        runner = SubprocessRunner(timeout_seconds=5)
        result = runner.run(target_add, args=(2, 3))

        assert result.success is True
        assert result.data["result"] == 5
        assert result.failure_type is None

    def test_timeout_returns_infrastructure_failure(self) -> None:
        runner = SubprocessRunner(timeout_seconds=1)
        result = runner.run(slow_target)

        assert result.success is False
        assert result.failure_type == "infrastructure"
        assert "timeout" in result.error.lower()

    def test_exception_returns_research_failure(self) -> None:
        runner = SubprocessRunner(timeout_seconds=5)
        result = runner.run(bad_target)

        assert result.success is False
        assert result.failure_type == "research"
        assert "bad data" in result.error

    def test_oom_classified_as_infrastructure(self) -> None:
        runner = SubprocessRunner(timeout_seconds=5)
        result = runner.run(oom_target)

        assert result.success is False
        assert result.failure_type == "infrastructure"
