"""
Unit tests keeping I/O out of the TaskManager's process-wide lock.
Source: app/services/task_manager.TaskManager.update_task

_lock guards the whole in-memory task map, not one task, so anything awaited
while holding it blocks every task on the server. update_task held it across
both a database round-trip and a WebSocket broadcast; _send_to_connections
awaits each connection in turn, so one client whose receive buffer is full —
a backgrounded tab, a slow link — stalled the send, and every append_log
queued behind it until that socket drained or errored. Every build's log
stream stops, and nothing in the logs points at a WebSocket.

append_log has always released the lock before broadcasting, which is what
made this a discrepancy rather than a design choice.

These tests assert the lock is free while the I/O runs, and that a stalled
broadcast does not stop another task from being written.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from pytest_mock import MockerFixture

from app.schemas.task import TaskStatus
from app.services.task_manager import TaskManager


def _seed(manager: TaskManager, task_id: str) -> None:
    """Put a task in the map directly — create_task needs a request and a DB."""
    manager._tasks[task_id] = {
        "id": task_id,
        "status": TaskStatus.PENDING,
        "progress": 0,
        "logs": [],
        "updated_at": datetime.now(timezone.utc),
    }


@pytest.fixture
def manager() -> TaskManager:
    return TaskManager()


class TestLockIsReleasedBeforeIO:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_tml_001_broadcast_runs_with_the_lock_free(
        self, manager: TaskManager, mocker: MockerFixture
    ):
        """TC-TML-001: update_task — the WebSocket send is not under the lock."""
        held: list[bool] = []

        async def record(*_args, **_kwargs) -> None:
            held.append(manager._lock.locked())

        mocker.patch.object(manager, "_broadcast_update", record)
        _seed(manager, "t1")

        await manager.update_task("t1", "progress", 50)

        assert held == [False]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_tml_002_the_database_write_runs_with_the_lock_free(
        self, manager: TaskManager, mocker: MockerFixture
    ):
        """TC-TML-002: update_task — the status sync is not under the lock either.

        Normally a millisecond, but a session waiting on an exhausted
        connection pool is the same stall with a different cause.
        """
        held: list[bool] = []

        async def record(*_args, **_kwargs) -> None:
            held.append(manager._lock.locked())

        mocker.patch.object(manager, "_update_db_status", record)
        mocker.patch.object(manager, "_broadcast_update", autospec=True)
        _seed(manager, "t1")

        await manager.update_task("t1", "status", TaskStatus.RUNNING, db=object())

        assert held == [False]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_tml_003_a_stalled_broadcast_does_not_block_another_task(
        self, manager: TaskManager, mocker: MockerFixture
    ):
        """TC-TML-003: update_task — one unresponsive client cannot stop the server.

        This is the failure as it actually appears: not an error, just every
        other build's log stream going quiet for as long as one socket takes
        to drain.
        """
        stalled = asyncio.Event()
        entered = asyncio.Event()

        async def stall(*_args, **_kwargs) -> None:
            entered.set()
            await stalled.wait()

        mocker.patch.object(manager, "_broadcast_update", stall)
        mocker.patch.object(manager, "_broadcast_log", autospec=True)
        _seed(manager, "slow")
        _seed(manager, "other")

        blocked = asyncio.create_task(manager.update_task("slow", "progress", 10))
        # Wait for the stall to actually begin — sleeping a tick instead would
        # let this test pass against a broadcast that never ran at all.
        await asyncio.wait_for(entered.wait(), timeout=1)

        await asyncio.wait_for(manager.append_log("other", "still moving"), timeout=1)
        assert manager._tasks["other"]["logs"] == ["still moving"]

        stalled.set()
        await blocked


class TestBehaviourIsUnchanged:
    """The reordering must not alter what update_task actually does."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_tml_010_the_value_is_written(
        self, manager: TaskManager, mocker: MockerFixture
    ):
        """TC-TML-010: update_task — the ordinary case still updates the map."""
        mocker.patch.object(manager, "_broadcast_update", autospec=True)
        _seed(manager, "t1")

        await manager.update_task("t1", "progress", 42)

        assert manager._tasks["t1"]["progress"] == 42

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_tml_011_an_unknown_task_does_no_io(
        self, manager: TaskManager, mocker: MockerFixture
    ):
        """TC-TML-011: update_task — an evicted task broadcasts nothing.

        The early return is inside the lock and the I/O is now outside it, so
        this is exactly the case a careless move would break.
        """
        broadcast = mocker.patch.object(manager, "_broadcast_update", autospec=True)
        db_write = mocker.patch.object(manager, "_update_db_status", autospec=True)

        await manager.update_task("gone", "status", TaskStatus.COMPLETED, db=object())

        broadcast.assert_not_called()
        db_write.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_tml_012_a_cancelled_task_is_not_overwritten_or_broadcast(
        self, manager: TaskManager, mocker: MockerFixture
    ):
        """TC-TML-012: update_task — the cancellation guard still wins.

        A killed worker writes its own FAILED as it dies. Letting that through
        would replace the user's "cancelled" with "failed", and broadcasting it
        would put the wrong verdict on screen.
        """
        broadcast = mocker.patch.object(manager, "_broadcast_update", autospec=True)
        db_write = mocker.patch.object(manager, "_update_db_status", autospec=True)
        _seed(manager, "t1")
        manager._tasks["t1"]["status"] = TaskStatus.CANCELLED

        await manager.update_task("t1", "status", TaskStatus.FAILED, db=object())

        assert manager._tasks["t1"]["status"] == TaskStatus.CANCELLED
        broadcast.assert_not_called()
        db_write.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", ["progress", "status_msg", "stage"])
    async def test_tc_tml_013_broadcast_keys_still_broadcast(
        self, manager: TaskManager, mocker: MockerFixture, key: str
    ):
        """TC-TML-013: update_task — the set of broadcast keys is unchanged."""
        broadcast = mocker.patch.object(manager, "_broadcast_update", autospec=True)
        _seed(manager, "t1")

        await manager.update_task("t1", key, "x")

        broadcast.assert_called_once_with("t1", key, "x")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_tml_014_other_keys_broadcast_nothing(
        self, manager: TaskManager, mocker: MockerFixture
    ):
        """TC-TML-014: update_task — an internal field does not reach clients."""
        broadcast = mocker.patch.object(manager, "_broadcast_update", autospec=True)
        _seed(manager, "t1")

        await manager.update_task("t1", "process", object())

        broadcast.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_tml_015_a_finished_task_is_still_scheduled_for_eviction(
        self, manager: TaskManager, mocker: MockerFixture
    ):
        """TC-TML-015: update_task — memory is still bounded.

        Eviction stayed inside the lock because it only registers a timer; if
        it had moved out with the I/O, two terminal writes could race into two
        eviction tasks for one id.
        """
        mocker.patch.object(manager, "_broadcast_update", autospec=True)
        evict = mocker.patch.object(manager, "_schedule_eviction")
        _seed(manager, "t1")

        await manager.update_task("t1", "status", TaskStatus.COMPLETED)

        evict.assert_called_once_with("t1")
