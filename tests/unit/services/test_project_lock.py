"""
Unit tests for per-project build serialisation.
Source: app/services/project_lock

Both workers use project_path as scratch space they own for the duration of a
build — nuitka_worker injects a libs loader into the entry point in place;
docker_worker backs up and overwrites .dockerignore, writes
Dockerfile.generated, and replaces the project's frontend .env — and each
restores afterwards. MAX_CONCURRENT_BUILDS caps how many builds run, not which
project they touch.

nuitka_worker had a lock for its half; docker_worker had none. Two builds of
one local path interleaved and destroyed each other's backups — worst case the
user's real .env ends up permanently replaced by another task's environment.
"""

import asyncio

import pytest

from app.services.project_lock import (
    get_project_lock,
    release_project_lock,
    tracked_path_count,
)


class TestLockIdentity:
    @pytest.mark.unit
    def test_tc_lck_001_same_path_yields_the_same_lock(self):
        """TC-LCK-001: get_project_lock — one lock per project directory.

        Two locks for one path would serialise nothing.
        """
        a = get_project_lock("/media/disk0/proj")
        b = get_project_lock("/media/disk0/proj")
        assert a is b
        release_project_lock("/media/disk0/proj")

    @pytest.mark.unit
    def test_tc_lck_002_path_is_normalised(self):
        """TC-LCK-002: get_project_lock — trailing slashes and '.' are the same project.

        Two tasks submitted as ".../proj" and ".../proj/" address one directory
        and must not each get their own lock.
        """
        a = get_project_lock("/media/disk0/proj")
        b = get_project_lock("/media/disk0/proj/")
        c = get_project_lock("/media/disk0/proj/./")
        assert a is b is c
        release_project_lock("/media/disk0/proj")

    @pytest.mark.unit
    def test_tc_lck_003_different_paths_get_different_locks(self):
        """TC-LCK-003: get_project_lock — unrelated projects must not block each other."""
        a = get_project_lock("/media/disk0/one")
        b = get_project_lock("/media/disk0/two")
        assert a is not b
        release_project_lock("/media/disk0/one")
        release_project_lock("/media/disk0/two")


class TestSerialisation:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_lck_010_same_project_builds_do_not_interleave(self):
        """TC-LCK-010: two builds of one project run one after the other.

        Interleaving is what corrupts the backup/restore cycles: A saves the
        user's file, B saves A's version as ITS backup, and the original is
        gone once both restore.
        """
        order: list[str] = []

        async def build(name: str) -> None:
            async with get_project_lock("/media/disk0/shared"):
                order.append(f"{name}-start")
                await asyncio.sleep(0.02)
                order.append(f"{name}-end")

        await asyncio.gather(build("A"), build("B"))
        release_project_lock("/media/disk0/shared")

        assert order in (
            ["A-start", "A-end", "B-start", "B-end"],
            ["B-start", "B-end", "A-start", "A-end"],
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_lck_011_different_projects_still_run_concurrently(self):
        """TC-LCK-011: unrelated projects are not serialised.

        The other direction matters: a global build lock would fix the
        corruption and destroy throughput on a host sized for concurrent builds.
        """
        order: list[str] = []

        async def build(name: str, path: str) -> None:
            async with get_project_lock(path):
                order.append(f"{name}-start")
                await asyncio.sleep(0.02)
                order.append(f"{name}-end")

        await asyncio.gather(
            build("X", "/media/disk0/p1"), build("Y", "/media/disk0/p2")
        )
        release_project_lock("/media/disk0/p1")
        release_project_lock("/media/disk0/p2")

        assert order[0].endswith("-start")
        assert order[1].endswith("-start")


class TestLockTableGrowth:
    """The dict is process-global; git mode gives every task a fresh path."""

    @pytest.mark.unit
    def test_tc_lck_020_released_locks_are_dropped(self):
        """TC-LCK-020: release_project_lock — the entry does not linger.

        Git-mode workspaces are per-task UUID directories that post-build
        cleanup then deletes, so without this the table accumulates one entry
        per build forever, for directories that no longer exist.
        """
        before = tracked_path_count()
        get_project_lock("/media/disk0/ephemeral-task-id")
        assert tracked_path_count() == before + 1

        release_project_lock("/media/disk0/ephemeral-task-id")
        assert tracked_path_count() == before

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_lck_021_a_held_lock_is_never_dropped(self):
        """TC-LCK-021: release_project_lock — releasing while held is refused.

        Dropping a held lock lets the next caller create a second one for the
        same path, which silently removes the serialisation this exists for.
        """
        path = "/media/disk0/still-building"
        lock = get_project_lock(path)

        async with lock:
            release_project_lock(path)
            assert get_project_lock(path) is lock

        release_project_lock(path)
