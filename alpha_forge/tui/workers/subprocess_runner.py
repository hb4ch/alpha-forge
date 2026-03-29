"""Process-isolated runner for backtest and robustness operations."""
from __future__ import annotations

import logging
import multiprocessing
import os
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


def _subprocess_worker(
    q: multiprocessing.queues.Queue,
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    duckdb_threads: int | None,
    max_memory_mb: int | None,
) -> None:
    """Run a callable in a child process and report the result via a queue."""
    try:
        if duckdb_threads:
            os.environ["DUCKDB_THREADS"] = str(duckdb_threads)
        if max_memory_mb:
            os.environ["DUCKDB_MEMORY_LIMIT"] = f"{max_memory_mb}MB"
        result = fn(*args, **kwargs)
        q.put(("success", result))
    except MemoryError as e:
        q.put(("infrastructure", str(e)))
    except Exception as e:
        q.put(("research", f"{type(e).__name__}: {e}"))


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
        self._ctx = multiprocessing.get_context("spawn")

    def run(self, fn: Callable, args: tuple = (), kwargs: dict | None = None) -> SubprocessResult:
        """Run fn(*args, **kwargs) in a child process."""
        kwargs = kwargs or {}
        result_queue = self._ctx.Queue()
        process = self._ctx.Process(
            target=_subprocess_worker,
            args=(
                result_queue,
                fn,
                args,
                kwargs,
                self.duckdb_threads,
                self.max_memory_mb,
            ),
        )
        try:
            process.start()
        except Exception as e:
            return SubprocessResult(
                success=False,
                error=f"Failed to start subprocess: {type(e).__name__}: {e}",
                failure_type="infrastructure",
            )
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
