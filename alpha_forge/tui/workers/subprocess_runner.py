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
        """Run fn(*args, **kwargs) in a child process."""
        kwargs = kwargs or {}
        result_queue: multiprocessing.Queue = multiprocessing.Queue()

        def _worker(q, f, a, kw):
            try:
                if self.duckdb_threads:
                    os.environ["DUCKDB_THREADS"] = str(self.duckdb_threads)
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
