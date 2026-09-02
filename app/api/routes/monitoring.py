"""System monitoring API routes."""

import asyncio
import sys
from typing import Annotated

import psutil
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
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
    params: dict = {"days": max(1, min(days, 365))}
    where_user = ""
    if scoped:
        where_user = " AND user_name = :user"
        params["user"] = current_user.username

    summary_row = (
        await db.execute(
            text(f"""
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status::text = 'COMPLETED') AS completed,
                   count(*) FILTER (WHERE status::text = 'FAILED')    AS failed,
                   count(*) FILTER (WHERE status::text = 'CANCELLED') AS cancelled,
                   avg(extract(epoch FROM (end_time - start_time)))
                       FILTER (WHERE status::text = 'COMPLETED' AND end_time IS NOT NULL)
                       AS avg_seconds
            FROM history
            WHERE start_time > now() - make_interval(days => :days){where_user}
        """),
            params,
        )
    ).mappings().one()

    daily = [
        dict(r)
        for r in (
            await db.execute(
                text(f"""
            SELECT start_time::date AS day,
                   count(*) AS total,
                   count(*) FILTER (WHERE status::text = 'COMPLETED') AS completed
            FROM history
            WHERE start_time > now() - make_interval(days => :days){where_user}
            GROUP BY 1 ORDER BY 1
        """),
                params,
            )
        ).mappings()
    ]

    # Per-project rollup. Every project is listed, not just the failing ones —
    # "which of my projects build cleanly" is the question people actually
    # arrive with, and showing only failures makes a healthy project invisible.
    #
    # `recent` carries the last few outcomes newest-first so the UI can render
    # a run strip: three greens then a red reads very differently from an
    # alternating pattern, and neither is visible in a success percentage.
    projects = [
        dict(r)
        for r in (
            await db.execute(
                text(f"""
            SELECT project_name,
                   count(*) AS total,
                   count(*) FILTER (WHERE status::text = 'COMPLETED') AS completed,
                   count(*) FILTER (WHERE status::text = 'FAILED')    AS failed,
                   max(start_time) AS last_run,
                   (array_agg(status::text ORDER BY start_time DESC))[1] AS last_status,
                   (array_agg(status::text ORDER BY start_time DESC))[1:12] AS recent
            FROM history
            WHERE start_time > now() - make_interval(days => :days){where_user}
            GROUP BY 1
            ORDER BY last_run DESC
            LIMIT 25
        """),
                params,
            )
        ).mappings()
    ]

    # Failure reasons come from the diagnosis the build already produced —
    # result is a JSON column, so it needs the ::jsonb cast for -> traversal.
    failures = [
        dict(r)
        for r in (
            await db.execute(
                text(f"""
            SELECT d->>'problem' AS problem, count(*) AS count
            FROM history,
                 LATERAL jsonb_array_elements(result::jsonb->'diagnosis') AS d
            WHERE status::text = 'FAILED'
              AND result IS NOT NULL
              AND result::jsonb ? 'diagnosis'
              AND start_time > now() - make_interval(days => :days){where_user}
            GROUP BY 1 ORDER BY count DESC LIMIT 8
        """),
                params,
            )
        ).mappings()
    ]

    total = summary_row["total"] or 0
    failed = summary_row["failed"] or 0
    diagnosed = sum(f["count"] for f in failures)

    return {
        "window_days": params["days"],
        "scope": "own" if scoped else "all",
        "summary": {
            "total": total,
            "completed": summary_row["completed"] or 0,
            "failed": failed,
            "cancelled": summary_row["cancelled"] or 0,
            "success_rate": round((summary_row["completed"] or 0) / total * 100, 1) if total else None,
            "avg_duration_seconds": round(float(summary_row["avg_seconds"]), 1)
            if summary_row["avg_seconds"] is not None
            else None,
        },
        "daily": [
            {
                "day": str(d["day"]),
                "total": d["total"],
                "completed": d["completed"],
                "success_rate": round(d["completed"] / d["total"] * 100, 1) if d["total"] else None,
            }
            for d in daily
        ],
        "projects": [
            {
                "project_name": p["project_name"],
                "total": p["total"],
                "completed": p["completed"],
                "failed": p["failed"],
                "success_rate": round(p["completed"] / p["total"] * 100, 1) if p["total"] else None,
                "last_status": (p["last_status"] or "").lower(),
                "last_run": p["last_run"].isoformat() if p["last_run"] else None,
                # Newest-first, lowercased to match the status vocabulary the
                # frontend already uses everywhere else.
                "recent": [s.lower() for s in (p["recent"] or [])],
            }
            for p in projects
        ],
        "top_failures": [{"problem": f["problem"], "count": f["count"]} for f in failures],
        # How many failures explained themselves. Low coverage means users are
        # retrying blind, which is a worse problem than the failure rate itself.
        "diagnosis_coverage": round(diagnosed / failed * 100, 1) if failed else None,
        "environment": _collect_build_environment(),
    }
