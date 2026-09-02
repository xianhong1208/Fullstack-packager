"""System monitoring API routes."""

import asyncio
import sys
from typing import Annotated

import psutil
from fastapi import APIRouter, Depends
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.task import Task
from app.services.permission import (
    CurrentUser,
    PermissionCode,
    get_current_user,
    require_any_permission,
)

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


def _get_gpu_info() -> list[dict]:
    """Get GPU information using GPUtil if available."""
    try:
        import GPUtil
        gpus = GPUtil.getGPUs()
        return [
            {
                "id": gpu.id,
                "name": gpu.name,
                "load": round(gpu.load * 100, 1),
                "memory_used": round(gpu.memoryUsed, 1),
                "memory_total": round(gpu.memoryTotal, 1),
                "memory_percent": round(gpu.memoryUsed / gpu.memoryTotal * 100, 1) if gpu.memoryTotal > 0 else 0,
                "temperature": gpu.temperature,
            }
            for gpu in gpus
        ]
    except Exception:
        return []


def _collect_stats() -> dict:
    """Blocking stats collection — cpu_percent(interval=0.1) sleeps and the
    GPU probe shells out to nvidia-smi, so this must run off the event loop."""
    # CPU
    cpu_percent = psutil.cpu_percent(interval=0.1)
    cpu_count = psutil.cpu_count()
    cpu_count_logical = psutil.cpu_count(logical=True)
    cpu_freq = psutil.cpu_freq()

    # Per-CPU usage
    cpu_percent_per_core = psutil.cpu_percent(interval=0.1, percpu=True)

    # Memory
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # Disk
    disk = psutil.disk_usage("/")

    # Network (bytes since boot)
    net_io = psutil.net_io_counters()

    # GPU
    gpus = _get_gpu_info()

    return {
        "cpu": {
            "percent": cpu_percent,
            "percent_per_core": cpu_percent_per_core,
            "count_physical": cpu_count,
            "count_logical": cpu_count_logical,
            "frequency_current": round(cpu_freq.current, 0) if cpu_freq else None,
            "frequency_max": round(cpu_freq.max, 0) if cpu_freq and cpu_freq.max else None,
        },
        "memory": {
            "total": memory.total,
            "available": memory.available,
            "used": memory.used,
            "percent": memory.percent,
        },
        "swap": {
            "total": swap.total,
            "used": swap.used,
            "percent": swap.percent,
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent,
        },
        "network": {
            "bytes_sent": net_io.bytes_sent,
            "bytes_recv": net_io.bytes_recv,
        },
        "gpus": gpus,
    }


@router.get("/stats")
async def get_system_stats(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict:
    """Get current system resource usage."""
    return await asyncio.to_thread(_collect_stats)


def _collect_build_environment() -> dict:
    """Report which compile targets are actually usable right now.

    This exists because the failure it detects is silent. A per-version venv
    is a symlink to a uv-managed interpreter; change the user that runs the
    service, move the machine, or clean uv's cache and the link dangles.
    find_nuitka_python() then correctly refuses it and quietly falls back to
    the service's own interpreter, so users keep picking "3.14" and keep
    getting a 3.13 binary that only fails at their customer's runtime.

    Nothing surfaced that state before: it took reading the history table to
    notice. Surfacing it is the whole point of putting it on a dashboard.
    """
    from app.services.nuitka_worker import _BUILD_CENTER_ROOT

    service_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    targets: list[dict] = []
    for venv_dir in sorted(_BUILD_CENTER_ROOT.glob(".venv-py3*")):
        compact = venv_dir.name.removeprefix(".venv-py")
        if not compact.isdigit() or len(compact) < 2:
            continue
        python_bin = venv_dir / "bin" / "python"
        # exists() follows the symlink — that is exactly the check that matters.
        usable = python_bin.exists()
        targets.append(
            {
                "version": f"{compact[0]}.{compact[1:]}",
                "venv": venv_dir.name,
                "usable": usable,
                # Show where a broken link points; without it the operator has
                # no idea whether the target moved or the user changed.
                "target": str(python_bin.readlink()) if python_bin.is_symlink() else None,
            }
        )

    return {
        "service_python": service_version,
        "targets": targets,
        "degraded": [t["version"] for t in targets if not t["usable"]],
    }


@router.get("/build-stats")
async def get_build_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    _: None = Depends(
        require_any_permission(
            PermissionCode.HISTORY_VIEW_OWN, PermissionCode.HISTORY_VIEW_ALL
        )
    ),
    days: int = 30,
) -> dict:
    """Aggregate build outcomes for the dashboard.

    Scoped the same way as GET /api/history: holders of history:view_all see
    everyone, everyone else sees only their own builds — so the numbers a user
    reads always match the list they can open.
    """
    scoped = not current_user.has_permission(PermissionCode.HISTORY_VIEW_ALL)
    window_days = max(1, min(days, 365))
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    # Fetch the window's rows once and aggregate in Python. The window is bounded
    # (<= 365 days), so this stays cheap while remaining portable across SQLite
    # and PostgreSQL — no dialect-specific SQL (FILTER / array_agg / jsonb / casts).
    stmt = select(Task).where(Task.start_time > cutoff)
    if scoped:
        stmt = stmt.where(Task.user_name == current_user.username)
    rows = list((await db.execute(stmt)).scalars().all())

    def _status(t: Task) -> str:
        return (t.status or "").upper()

    total = len(rows)
    completed = sum(1 for t in rows if _status(t) == "COMPLETED")
    failed = sum(1 for t in rows if _status(t) == "FAILED")
    cancelled = sum(1 for t in rows if _status(t) == "CANCELLED")

    durations = [
        (t.end_time - t.start_time).total_seconds()
        for t in rows
        if _status(t) == "COMPLETED" and t.end_time is not None and t.start_time is not None
    ]
    avg_seconds = sum(durations) / len(durations) if durations else None

    # Daily totals, keyed by calendar day.
    by_day: dict[date, dict] = defaultdict(lambda: {"total": 0, "completed": 0})
    for t in rows:
        if t.start_time is None:
            continue
        d = t.start_time.date()
        by_day[d]["total"] += 1
        if _status(t) == "COMPLETED":
            by_day[d]["completed"] += 1
    daily = [
        {
            "day": str(d),
            "total": v["total"],
            "completed": v["completed"],
            "success_rate": round(v["completed"] / v["total"] * 100, 1) if v["total"] else None,
        }
        for d, v in sorted(by_day.items())
    ]

    # Per-project rollup. `recent` is the last 12 outcomes newest-first so the UI
    # can render a run strip. Every project is listed, not only the failing ones.
    by_project: dict[str, list[Task]] = defaultdict(list)
    for t in rows:
        by_project[t.project_name].append(t)
    projects = []
    for name, tasks in by_project.items():
        tasks.sort(key=lambda t: (t.start_time or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        p_completed = sum(1 for t in tasks if _status(t) == "COMPLETED")
        p_failed = sum(1 for t in tasks if _status(t) == "FAILED")
        last_run = tasks[0].start_time if tasks else None
        projects.append({
            "project_name": name,
            "total": len(tasks),
            "completed": p_completed,
            "failed": p_failed,
            "success_rate": round(p_completed / len(tasks) * 100, 1) if tasks else None,
            "last_status": _status(tasks[0]).lower() if tasks else "",
            "last_run": last_run.isoformat() if last_run else None,
            "recent": [_status(t).lower() for t in tasks[:12]],
        })
    projects.sort(key=lambda p: p["last_run"] or "", reverse=True)
    projects = projects[:25]

    # Failure reasons from the diagnosis each build already produced (result JSON).
    problem_counts: dict[str, int] = defaultdict(int)
    for t in rows:
        if _status(t) != "FAILED" or not isinstance(t.result, dict):
            continue
        for d in t.result.get("diagnosis") or []:
            problem = d.get("problem") if isinstance(d, dict) else None
            if problem:
                problem_counts[problem] += 1
    top_failures = sorted(
        ({"problem": k, "count": v} for k, v in problem_counts.items()),
        key=lambda f: f["count"],
        reverse=True,
    )[:8]
    diagnosed = sum(f["count"] for f in top_failures)

    return {
        "window_days": window_days,
        "scope": "own" if scoped else "all",
        "summary": {
            "total": total,
            "completed": completed,
            "failed": failed,
            "cancelled": cancelled,
            "success_rate": round(completed / total * 100, 1) if total else None,
            "avg_duration_seconds": round(avg_seconds, 1) if avg_seconds is not None else None,
        },
        "daily": daily,
        "projects": projects,
        "top_failures": top_failures,
        "diagnosis_coverage": round(diagnosed / failed * 100, 1) if failed else None,
        "environment": _collect_build_environment(),
    }
