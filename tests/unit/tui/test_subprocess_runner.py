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
            raise MemoryError("out of memory")

        runner = SubprocessRunner(timeout_seconds=5)
        result = runner.run(oom_fn)

        assert result.success is False
        assert result.failure_type == "infrastructure"
