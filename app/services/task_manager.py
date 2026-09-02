"""Task manager service for handling build tasks."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.task import Task
from app.schemas.task import (
    BuildConfig,
    TaskCreate,
    TaskResponse,
    TaskStatus,
    WebSocketMessage,
)

settings = get_settings()
logger = logging.getLogger(__name__)


class TaskManager:
    """Manages build tasks with in-memory state and DB persistence."""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._websocket_connections: dict[str, set[WebSocket]] = {}
        self._global_connections: set[WebSocket] = set()
        self._eviction_tasks: dict[str, asyncio.Task] = {}

    def _schedule_eviction(self, task_id: str) -> None:
        """Evict a finished task from memory after the retention window.

        Without this, _tasks (each entry holding up to max_log_lines of
        logs) grows forever on a long-running server. History remains
        queryable from the DB after eviction.
        """
        if task_id in self._eviction_tasks:
            return

        async def _evict() -> None:
            try:
                await asyncio.sleep(settings.finished_task_retention_minutes * 60)
                async with self._lock:
                    task = self._tasks.get(task_id)
                    if task and task["status"] not in (
                        TaskStatus.PENDING,
                        TaskStatus.RUNNING,
                    ):
                        del self._tasks[task_id]
            finally:
                self._eviction_tasks.pop(task_id, None)

        self._eviction_tasks[task_id] = asyncio.create_task(_evict())

    async def create_task(
        self,
        request: TaskCreate,
        db: AsyncSession,
    ) -> TaskResponse:
        """Create a new task."""
        task_id = str(uuid4())
        now = datetime.now(timezone.utc)

        task_data = {
            "id": task_id,
            "user_name": request.user_name,
            "project_name": request.project_name,
            "python_version": request.config.python_version,
            "status": TaskStatus.PENDING,
            "progress": 0,
            "status_msg": "Initializing...",
            "stage": "queued",
            "result": None,
            "created_at": now,
            "updated_at": now,
            "config": request.config,
            "logs": [],
            "process": None,
        }

        async with self._lock:
            self._tasks[task_id] = task_data

        # Persist to database
        db_task = Task(
            task_id=task_id,
            user_name=request.user_name,
            project_name=request.project_name,
            python_version=request.config.python_version,
            status=TaskStatus.PENDING.value,
            output_dir=request.config.output_dir,
            config=request.config.model_dump(),
        )
        db.add(db_task)
        await db.commit()

        return self._to_response(task_data)

    async def get_task(self, task_id: str) -> TaskResponse | None:
        """Get a task by ID from memory."""
        task_data = self._tasks.get(task_id)
        if task_data:
            return self._to_response(task_data)
        return None

    async def get_all_active_tasks(self) -> list[TaskResponse]:
        """Get all active tasks from memory."""
        return [
            self._to_response(task)
            for task in self._tasks.values()
            if task["status"] in (TaskStatus.PENDING, TaskStatus.RUNNING)
        ]

    async def get_all_tasks(self) -> list[TaskResponse]:
        """Get all tasks from memory."""
        return [self._to_response(task) for task in self._tasks.values()]

    async def update_task(
        self,
        task_id: str,
        key: str,
        value: Any,
        db: AsyncSession | None = None,
    ) -> None:
        """Update a task property."""
        async with self._lock:
            if task_id not in self._tasks:
                return

            # A cancelled task is terminal from the user's point of view.
            # The killed worker races us here with its own FAILED/COMPLETED
            # write — don't let it overwrite the CANCELLED verdict.
            if (
                key == "status"
                and self._tasks[task_id].get("status") == TaskStatus.CANCELLED
                and value in (TaskStatus.FAILED, TaskStatus.COMPLETED, TaskStatus.RUNNING)
            ):
                return

            self._tasks[task_id][key] = value
            self._tasks[task_id]["updated_at"] = datetime.now(timezone.utc)

            # Bound memory: finished tasks leave the in-memory map after
            # the retention window (history persists in the DB).
            if key == "status" and value in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                self._schedule_eviction(task_id)

        # Both of these are I/O, and the lock is process-wide — held here it
        # covers every task, not this one. _send_to_connections awaits each
        # WebSocket in turn, so a single client whose receive buffer is full
        # (a backgrounded tab, a slow link) stalls the send; while that stall
        # held the lock, every append_log on the server queued behind it and
        # all builds stopped streaming logs. append_log has always released
        # the lock before broadcasting for exactly this reason — this is the
        # same treatment.
        #
        # Ordering is unaffected: a task's updates come from one coroutine
        # awaiting them in sequence, so the next call cannot start until this
        # one's writes are done.
        if key == "status" and db:
            await self._update_db_status(task_id, value, db)

        if key in ("status", "progress", "status_msg", "stage"):
            await self._broadcast_update(task_id, key, value)

    async def _update_db_status(
        self,
        task_id: str,
        status: TaskStatus,
        db: AsyncSession,
    ) -> None:
        """Update task status in database."""
        result = await db.execute(
            select(Task).where(Task.task_id == task_id)
        )
        db_task = result.scalar_one_or_none()
        if db_task:
            db_task.status = status.value
            if status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                db_task.end_time = datetime.now(timezone.utc)
            await db.commit()

    async def append_log(self, task_id: str, message: str) -> None:
        """Append a log message to a task."""
        async with self._lock:
            if task_id not in self._tasks:
                return

            logs = self._tasks[task_id]["logs"]
            logs.append(message)

            # Limit log size
            if len(logs) > settings.max_log_lines:
                logs.pop(0)

        # Broadcast log via WebSocket
        await self._broadcast_log(task_id, message)

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task from memory."""
        async with self._lock:
            if task_id in self._tasks:
                # Cancel process if running
                process = self._tasks[task_id].get("process")
                if process:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass  # already exited — the normal case
                    except Exception:
                        logger.warning(
                            "Could not terminate process for task %s; it may still be running",
                            task_id,
                            exc_info=True,
                        )
                del self._tasks[task_id]
                return True
        return False

    async def cancel_task(
        self,
        task_id: str,
        db: AsyncSession,
    ) -> bool:
        """Cancel a running task (SIGTERM, then SIGKILL if it won't die)."""
        async with self._lock:
            if task_id not in self._tasks:
                return False

            task = self._tasks[task_id]
            if task["status"] not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                return False

            process = task.get("process")

        # Escalate OUTSIDE the lock so a slow-to-die process doesn't block
        # other task operations. terminate → wait 5s → kill.
        if process:
            try:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await self.append_log(
                        task_id, "Process did not stop in 5s — sent SIGKILL"
                    )
            except ProcessLookupError:
                pass  # finished on its own between the status check and here
            except Exception:
                # The task is marked CANCELLED immediately below regardless, so
                # swallowing this silently would let the UI report "cancelled"
                # while the compile keeps running and consuming CPU — the system
                # saying something untrue with no trace of it anywhere.
                logger.warning(
                    "Failed to stop the process for task %s; it may still be running",
                    task_id,
                    exc_info=True,
                )
                await self.append_log(
                    task_id,
                    "警告:無法停止建置行程,它可能仍在背景執行。請通知管理員。",
                )

        await self.update_task(task_id, "status", TaskStatus.CANCELLED, db)
        await self.append_log(task_id, "Task cancelled by user")
        return True

    async def set_process(self, task_id: str, process: Any) -> None:
        """Set the subprocess handle for a task."""
        async with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["process"] = process

    async def get_logs(self, task_id: str) -> list[str]:
        """Get logs for a task."""
        async with self._lock:
            task = self._tasks.get(task_id)
            return list(task["logs"]) if task else []

    # WebSocket management
    async def register_websocket(
        self,
        websocket: WebSocket,
        task_id: str | None = None,
    ) -> None:
        """Register a WebSocket connection."""
        if task_id:
            if task_id not in self._websocket_connections:
                self._websocket_connections[task_id] = set()
            self._websocket_connections[task_id].add(websocket)
        else:
            self._global_connections.add(websocket)

    async def unregister_websocket(
        self,
        websocket: WebSocket,
        task_id: str | None = None,
    ) -> None:
        """Unregister a WebSocket connection."""
        if task_id:
            if task_id in self._websocket_connections:
                self._websocket_connections[task_id].discard(websocket)
        else:
            self._global_connections.discard(websocket)

    async def _broadcast_log(self, task_id: str, message: str) -> None:
        """Broadcast a log message to connected clients."""
        ws_message = WebSocketMessage(
            type="log",
            task_id=task_id,
            data=message,
        )
        await self._send_to_connections(task_id, ws_message)

    async def _broadcast_update(
        self,
        task_id: str,
        key: str,
        value: Any,
    ) -> None:
        """Broadcast a task update to connected clients."""
        ws_message = WebSocketMessage(
            type=key,
            task_id=task_id,
            data=value if not isinstance(value, TaskStatus) else value.value,
        )
        await self._send_to_connections(task_id, ws_message)

    async def _send_to_connections(
        self,
        task_id: str,
        message: WebSocketMessage,
    ) -> None:
        """Send message to all relevant WebSocket connections."""
        message_data = message.model_dump_json()

        # Send to task-specific connections
        task_connections = self._websocket_connections.get(task_id, set())
        for ws in list(task_connections):
            try:
                await ws.send_text(message_data)
            except Exception:
                task_connections.discard(ws)

        # Send to global connections
        for ws in list(self._global_connections):
            try:
                await ws.send_text(message_data)
            except Exception:
                self._global_connections.discard(ws)

    def _to_response(self, task_data: dict[str, Any]) -> TaskResponse:
        """Convert internal task data to response model."""
        return TaskResponse(
            id=task_data["id"],
            user_name=task_data["user_name"],
            project_name=task_data["project_name"],
            python_version=task_data["python_version"],
            status=task_data["status"],
            progress=task_data["progress"],
            status_msg=task_data["status_msg"],
            stage=task_data.get("stage", ""),
            created_at=task_data["created_at"],
            updated_at=task_data.get("updated_at"),
            config=task_data["config"],
            logs=task_data["logs"],
            result=task_data.get("result"),
        )

    async def set_stage(self, task_id: str, stage: str) -> None:
        """Set the coarse build stage (preflight/compile/bundle/verify/done)."""
        await self.update_task(task_id, "stage", stage)

    async def set_result(
        self,
        task_id: str,
        result: dict[str, Any],
        db: AsyncSession | None = None,
    ) -> None:
        """Attach the structured BuildResult to a task (memory + DB + broadcast).

        Called incrementally by the worker — first with just preflight, then
        again with the full artifact/verify payload — so the detail page can
        show progress as it accrues. Each call replaces the prior result.
        """
        async with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["result"] = result

        if db is not None:
            db_result = await db.execute(
                select(Task).where(Task.task_id == task_id)
            )
            db_task = db_result.scalar_one_or_none()
            if db_task:
                db_task.result = result
                await db.commit()

        await self._broadcast_update(task_id, "result", result)

    async def merge_result(
        self,
        task_id: str,
        patch: dict[str, Any],
        db: AsyncSession | None = None,
    ) -> None:
        """Shallow-merge `patch` into a task's existing result and persist.

        Used to attach failure diagnosis without clobbering preflight/artifact
        data the worker may have already stored. Reads under the lock, then
        delegates to set_result (which re-locks) for the DB + broadcast tail.
        """
        async with self._lock:
            if task_id not in self._tasks:
                return
            current = dict(self._tasks[task_id].get("result") or {})
        current.update(patch)
        await self.set_result(task_id, current, db)

    async def get_history(
        self,
        db: AsyncSession,
        limit: int = 200,
        offset: int = 0,
        user_name: str | None = None,
    ) -> list[Task]:
        """Get task history from database with pagination.

        When ``user_name`` is given, the ownership filter is applied IN SQL
        (in the WHERE clause) BEFORE limit/offset — so the page contains that
        user's newest N rows, not "the global newest N, then filtered". The
        latter (the old behaviour) silently hid a user's history whenever other
        users had enough recent builds to fill the limit window.
        """
        stmt = select(Task)
        if user_name:
            stmt = stmt.where(Task.user_name == user_name)
        stmt = stmt.order_by(Task.start_time.desc()).limit(limit).offset(offset)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_history_usernames(self, db: AsyncSession) -> list[str]:
        """Return the distinct, sorted user_names that appear in history.

        Powers the "filter by user" dropdown on the History page (admins /
        history:view_all only).
        """
        result = await db.execute(
            select(Task.user_name).distinct().order_by(Task.user_name)
        )
        return [u for u in result.scalars().all() if u]

    async def fail_orphaned_tasks(self, db: AsyncSession) -> int:
        """Mark tasks left in PENDING/RUNNING by a previous run as FAILED.

        In-memory task state does NOT survive a server restart, so any task
        still 'running' in the DB after boot is an orphan whose subprocess is
        already gone. Without this they'd spin in the UI forever. Returns how
        many rows were reconciled. Uses enum members so SQLAlchemy binds the
        native pg enum labels correctly.
        """
        from sqlalchemy import update

        result = await db.execute(
            update(Task)
            .where(Task.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]))
            .values(status=TaskStatus.FAILED, end_time=datetime.now(timezone.utc))
        )
        await db.commit()
        return result.rowcount or 0

    async def get_history_task(self, db: AsyncSession, task_id: str) -> Task | None:
        """Get a single history task by ID from database."""
        result = await db.execute(
            select(Task).where(Task.task_id == task_id)
        )
        return result.scalar_one_or_none()


# Global singleton instance
task_manager = TaskManager()
