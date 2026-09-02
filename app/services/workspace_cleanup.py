"""Background cleanup of per-task git workspaces.

SAFETY INVARIANTS:
    1. Only deletes paths that resolve under `settings.git_workspace_dir`.
       A resolved path outside the root is NEVER touched.
    2. Never deletes workspaces of running / pending tasks — status is
       read from the history table, not the filesystem.
    3. Uses shutil.rmtree(..., ignore_errors=True) which does NOT follow
       symlinks (by default), so a malicious symlink inside the workspace
       cannot cause deletion elsewhere.
    4. On any error, logs and continues — a single bad workspace must
       not crash the whole sweep.

POLICY (driven by settings):
    - pending / running       → never touched
    - completed (success)     → deleted after `workspace_success_ttl_days`
    - failed / cancelled      → deleted after `workspace_failed_ttl_days`
    - orphan (no DB row)      → deleted after `workspace_orphan_ttl_days`,
                                 aged by filesystem mtime
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.database import async_session
from app.models.task import Task

logger = logging.getLogger(__name__)


def _is_inside(path: Path, root: Path) -> bool:
    """True iff path (resolved) is inside root (resolved)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_rmtree(path: Path) -> bool:
    """Best-effort recursive delete. Returns True on success or if absent."""
    try:
        if not path.exists():
            return True
        shutil.rmtree(path, ignore_errors=True)
        return not path.exists()
    except Exception as e:
        logger.warning("Failed to rmtree %s: %s", path, e)
        return False


def _ensure_aware(dt: datetime | None) -> datetime | None:
    """Ensure a datetime is timezone-aware (assume UTC if naive)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def sweep_workspaces() -> dict:
    """Iterate the workspace root once and apply the TTL policy.

    Returns a small summary dict suitable for logging.
    """
    settings = get_settings()

    if not settings.workspace_cleanup_enabled:
        return {"skipped": "disabled"}

    workspace_root = Path(settings.git_workspace_dir).resolve()
    if not workspace_root.exists() or not workspace_root.is_dir():
        return {"skipped": "workspace_root_missing"}

    now = datetime.now(timezone.utc)
    success_cutoff = now - timedelta(days=max(0, settings.workspace_success_ttl_days))
    failed_cutoff = now - timedelta(days=max(0, settings.workspace_failed_ttl_days))
    orphan_cutoff = now - timedelta(days=max(0, settings.workspace_orphan_ttl_days))

    # 1. Collect candidate workspaces (top-level dirs only, inside root).
    candidates: list[Path] = []
    try:
        for entry in workspace_root.iterdir():
            try:
                if not entry.is_dir() or entry.is_symlink():
                    continue
                if not _is_inside(entry, workspace_root):
                    continue
                candidates.append(entry)
            except OSError:
                continue
    except OSError as e:
        logger.warning("Cannot iterate workspace root %s: %s", workspace_root, e)
        return {"skipped": f"iter_failed: {e}"}

    if not candidates:
        return {
            "deleted_success": 0,
            "deleted_failed": 0,
            "deleted_orphan": 0,
            "kept": 0,
        }

    # 2. Batch-fetch tasks from DB. Dir names are task_ids (UUIDs).
    task_ids = [c.name for c in candidates]
    tasks_by_id: dict[str, Task] = {}
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Task).where(Task.task_id.in_(task_ids))
            )
            for t in result.scalars().all():
                tasks_by_id[t.task_id] = t
    except Exception as e:
        logger.exception("Workspace sweep DB lookup failed: %s", e)
        return {"skipped": f"db_failed: {e}"}

    # 3. Apply policy per candidate.
    deleted_success = 0
    deleted_failed = 0
    deleted_orphan = 0
    kept = 0

    for workspace_dir in candidates:
        task_id = workspace_dir.name
        task = tasks_by_id.get(task_id)

        # --- Orphan path: no DB row. Age by filesystem mtime. ---
        if task is None:
            try:
                mtime = datetime.fromtimestamp(
                    workspace_dir.stat().st_mtime, tz=timezone.utc
                )
            except OSError:
                kept += 1
                continue
            if mtime < orphan_cutoff:
                # Off-loop delete — a workspace (.venv/node_modules/.git) can
                # be gigabytes; a sync rmtree here stalls the whole server.
                if await asyncio.to_thread(_safe_rmtree, workspace_dir):
                    deleted_orphan += 1
                else:
                    kept += 1
            else:
                kept += 1
            continue

        # --- Known task path: use task.status + task.end_time ---
        status = (task.status or "").lower()
        if status in ("running", "pending"):
            kept += 1
            continue

        end_time = _ensure_aware(task.end_time)
        if end_time is None:
            # Task is completed/failed/cancelled but end_time is missing —
            # fall back to mtime, treat conservatively (keep unless very old).
            try:
                mtime = datetime.fromtimestamp(
                    workspace_dir.stat().st_mtime, tz=timezone.utc
                )
                end_time = mtime
            except OSError:
                kept += 1
                continue

        if status == "completed":
            cutoff = success_cutoff
            counter_key = "success"
        elif status in ("failed", "cancelled"):
            cutoff = failed_cutoff
            counter_key = "failed"
        else:
            kept += 1
            continue

        if end_time < cutoff:
            if await asyncio.to_thread(_safe_rmtree, workspace_dir):
                if counter_key == "success":
                    deleted_success += 1
                else:
                    deleted_failed += 1
            else:
                kept += 1
        else:
            kept += 1

    summary = {
        "deleted_success": deleted_success,
        "deleted_failed": deleted_failed,
        "deleted_orphan": deleted_orphan,
        "kept": kept,
    }
    if deleted_success or deleted_failed or deleted_orphan:
        logger.info("Workspace sweep: %s", summary)
    return summary


async def sweep_docker_images() -> dict:
    """Delete exported Docker images (.tar / .tar.gz) older than the TTL.

    Age is judged by file mtime — exports are write-once, so mtime is the
    export time. Files are deleted off-loop; the directory is never removed.
    """
    settings = get_settings()

    if not settings.docker_images_cleanup_enabled:
        return {"skipped": "disabled"}

    images_dir = Path(settings.docker_images_dir)
    if not images_dir.exists() or not images_dir.is_dir():
        return {"skipped": "images_dir_missing"}

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=max(0, settings.docker_images_ttl_days)
    )

    deleted = 0
    freed_bytes = 0
    kept = 0
    for entry in images_dir.iterdir():
        try:
            if not entry.is_file() or entry.is_symlink():
                continue
            if not (entry.name.endswith(".tar.gz") or entry.name.endswith(".tar")):
                continue
            stat = entry.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        except OSError:
            continue

        if mtime < cutoff:
            try:
                await asyncio.to_thread(entry.unlink)
                deleted += 1
                freed_bytes += stat.st_size
            except OSError as e:
                logger.warning("Failed to delete old image %s: %s", entry, e)
                kept += 1
        else:
            kept += 1

    summary = {"deleted": deleted, "freed_mb": round(freed_bytes / 1024 / 1024, 1), "kept": kept}
    if deleted:
        logger.info("Docker image sweep: %s", summary)
    return summary


async def sweep_refresh_tokens() -> dict:
    """Delete refresh tokens that can never be used again.

    Token rotation issues a fresh row on every refresh and revokes the old
    one, so an open browser tab produces roughly one row per 15 minutes. That
    is correct security behaviour with no garbage collection behind it: this
    table reached 21,455 rows for 7 users, 91% of them already expired, with
    the oldest dating back to the install.

    Rows are removed once they are BOTH unusable and past the grace period.
    The grace window keeps recently revoked tokens around so the login-history
    and active-session views still make sense right after a logout.

    Returns a summary dict; never raises out to the caller's loop.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import and_, delete, or_

    from app.database import async_session
    from app.models.refresh_token import RefreshToken

    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=max(1, settings.refresh_token_retention_days)
    )

    async with async_session() as db:
        result = await db.execute(
            delete(RefreshToken).where(
                or_(
                    # Expired long enough ago that nothing can present it.
                    RefreshToken.expires_at < cutoff,
                    # Revoked long enough ago — these may still carry a future
                    # expires_at (rotation revokes a token mid-life), so the
                    # expiry clause above would never reach them.
                    and_(
                        RefreshToken.revoked_at.is_not(None),
                        RefreshToken.revoked_at < cutoff,
                    ),
                )
            )
        )
        await db.commit()
        deleted = result.rowcount or 0

    if deleted:
        logger.info("Deleted %d unusable refresh token(s)", deleted)
    return {"deleted": deleted, "cutoff": cutoff.isoformat()}


async def cleanup_loop() -> None:
    """Run sweep_workspaces() + sweep_docker_images() forever with the
    configured interval.

    Designed to be started as a background task from the FastAPI
    lifespan. Handles cancellation cleanly on shutdown.
    """
    settings = get_settings()

    # Small delay so the app finishes startup before first sweep runs.
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        return

    while True:
        try:
            summary = await sweep_workspaces()
            logger.debug("Workspace sweep complete: %s", summary)
        except asyncio.CancelledError:
            logger.info("Workspace cleanup loop cancelled")
            return
        except Exception:
            logger.exception("Workspace sweep raised an exception")

        try:
            image_summary = await sweep_docker_images()
            logger.debug("Docker image sweep complete: %s", image_summary)
        except asyncio.CancelledError:
            logger.info("Workspace cleanup loop cancelled")
            return
        except Exception:
            logger.exception("Docker image sweep raised an exception")

        try:
            token_summary = await sweep_refresh_tokens()
            logger.debug("Refresh token sweep complete: %s", token_summary)
        except asyncio.CancelledError:
            logger.info("Workspace cleanup loop cancelled")
            return
        except Exception:
            logger.exception("Refresh token sweep raised an exception")

        interval_hours = max(1, settings.workspace_cleanup_interval_hours)
        try:
            await asyncio.sleep(interval_hours * 3600)
        except asyncio.CancelledError:
            return


def delete_one_workspace(task_id: str) -> bool:
    """Synchronously delete the workspace for a specific task.

    Returns True on success (or if the workspace was already absent).
    Raises ValueError if the resolved path would escape the workspace root.
    """
    settings = get_settings()
    workspace_root = Path(settings.git_workspace_dir).resolve()
    target = (workspace_root / task_id).resolve()
    if not _is_inside(target, workspace_root):
        raise ValueError(f"Refusing to delete outside workspace root: {target}")
    if not target.exists():
        return True
    return _safe_rmtree(target)


def shrink_workspace(
    task_id: str,
    output_dir_name: str,
) -> dict:
    """Delete everything in a task's workspace EXCEPT the output subdir.

    Used right after a successful non-Docker build to reclaim disk space
    while keeping the download endpoint functional (which tars up
    ``workspace/<output_dir>`` on demand).

    Returns a summary dict: { preserved, removed, failed }.
    A ``skipped`` key is set if the operation was a no-op
    (workspace missing, no preserve target, etc.).

    Raises ValueError if the resolved workspace path escapes the root.
    """
    settings = get_settings()
    workspace_root = Path(settings.git_workspace_dir).resolve()
    workspace_dir = (workspace_root / task_id).resolve()
    if not _is_inside(workspace_dir, workspace_root):
        raise ValueError(f"Refusing to shrink outside workspace root: {workspace_dir}")
    if not workspace_dir.exists() or not workspace_dir.is_dir():
        return {"skipped": "workspace_missing"}

    # Normalize preserve target: take the first non-empty path segment.
    # Rejects ".", "", "..", and any traversal attempt.
    raw = (output_dir_name or "").strip().strip("/")
    if not raw or raw == "." or ".." in raw.split("/"):
        return {"skipped": "no_preserve_target"}
    preserve = raw.split("/", 1)[0]

    removed = 0
    failed = 0
    for item in workspace_dir.iterdir():
        if item.name == preserve:
            continue
        try:
            if item.is_symlink() or item.is_file():
                item.unlink()
                removed += 1
            elif item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
                # rmtree with ignore_errors doesn't raise, but may leave
                # partial state — count as removed iff the dir is gone.
                if not item.exists():
                    removed += 1
                else:
                    failed += 1
        except OSError as e:
            failed += 1
            logger.warning("shrink_workspace: failed to remove %s: %s", item, e)

    return {"preserved": preserve, "removed": removed, "failed": failed}
