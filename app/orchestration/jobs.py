"""Small worker boundary; deployments can replace it with a durable queue later."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol


class WorkflowJobRunner(Protocol):
    def submit(self, job_id: str, operation: Callable[[], Awaitable[None]]) -> None: ...
    def cancel(self, job_id: str) -> bool: ...


class InProcessWorkflowJobRunner:
    """Bounded asyncio runner; workflow snapshots remain persisted by the orchestrator."""

    def __init__(self, *, max_concurrent: int = 2) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[str, asyncio.Task] = {}

    def submit(self, job_id: str, operation: Callable[[], Awaitable[None]]) -> None:
        if job_id in self._tasks and not self._tasks[job_id].done():
            raise ValueError("A job with this id is already active")

        async def bounded() -> None:
            async with self._semaphore:
                await operation()

        task = asyncio.create_task(bounded(), name=f"workflow:{job_id}")
        self._tasks[job_id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(job_id, None))

    def cancel(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    @property
    def active_job_ids(self) -> tuple[str, ...]:
        return tuple(job_id for job_id, task in self._tasks.items() if not task.done())
