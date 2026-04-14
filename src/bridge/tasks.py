"""Shared helper for creating asyncio tasks with automatic exception logging."""

import asyncio
import logging

log = logging.getLogger(__name__)


def create_logged_task(coro, *, name: str = "") -> asyncio.Task:
    """Create an asyncio task with automatic exception logging."""
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(_on_task_done)
    return task


async def cancel_tasks(*tasks: asyncio.Task | None) -> None:
    """Cancel tasks and wait for them to exit cleanly."""
    pending = [task for task in tasks if task is not None]
    if not pending:
        return

    for task in pending:
        task.cancel()

    for task in pending:
        try:
            await task
        except asyncio.CancelledError:
            pass


def _on_task_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("Background task %s died: %s", task.get_name(), exc, exc_info=exc)
