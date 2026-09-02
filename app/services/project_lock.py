"""Serialise builds that share a project directory.

Both build workers treat `project_path` as scratch space they own for the
duration of a build:

- nuitka_worker injects the EXTERNAL-mode libs loader into the entry point IN
  PLACE and restores it afterwards.
- docker_worker writes .dockerignore (backing up an existing one to
  .dockerignore.bak), writes Dockerfile.generated, and overwrites the project's
  frontend .env with the task's env content — then restores all three.

Each of those is a read-modify-restore cycle over a file the user also owns.
MAX_CONCURRENT_BUILDS bounds how many builds run at once but says nothing about
WHICH project they touch, so two tasks against the same local path interleave:

- A backs up .dockerignore to .bak; B backs up A's generated file over that
  .bak; A finishes and restores the wrong content; B finishes and finds no .bak
  at all, so the user's original .dockerignore is gone for good.
- A writes Dockerfile.generated and, before `docker build` reads it, B
  overwrites it with a different config. A builds B's image and reports success
  — the image simply does not match what A asked for, with no error anywhere.
- Worst is the frontend .env. A saves the user's real file to memory and writes
  its own; B then reads A's content as ITS backup; A restores the original; B
  writes A's content back. The user's .env is now permanently replaced by
  another task's environment — usually another system's credentials.

nuitka_worker had a lock for its half of this and docker_worker had none, so
the lock lives here now: one lock per resolved project path, taken by the
dispatcher around the whole prepare -> build -> restore window rather than by
either worker.

Git mode never contends — every task gets its own workspace directory — but it
takes the lock too, since the key is the path and per-task paths are unique.
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_project_locks: dict[str, asyncio.Lock] = {}


def get_project_lock(project_path: str) -> asyncio.Lock:
    """Return the lock guarding `project_path`, creating it on first use."""
    key = str(Path(project_path).resolve())
    return _project_locks.setdefault(key, asyncio.Lock())


def release_project_lock(project_path: str) -> None:
    """Drop the lock entry once nothing is waiting on it.

    Without this the dict only grows. In local mode the key set is the number
    of distinct project directories, which is small; in git mode every task
    gets a fresh workspace path, so each build would leave a permanent entry
    for a directory that post-build cleanup has already deleted.

    Only removes an unlocked, uncontended lock — dropping one that a waiter
    holds a reference to would let the next caller create a second lock for the
    same path and defeat the serialisation entirely.
    """
    key = str(Path(project_path).resolve())
    lock = _project_locks.get(key)
    if lock is None or lock.locked():
        return
    _project_locks.pop(key, None)


def tracked_path_count() -> int:
    """Number of paths currently tracked — for tests and diagnostics."""
    return len(_project_locks)
