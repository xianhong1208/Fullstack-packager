"""REST API routes for task management."""

import asyncio
import csv
import logging
import io
import os
import re
import tarfile
import threading
from pathlib import Path
from typing import AsyncIterator, Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.schemas.task import (
    BuildConfig,
    HistoryItem,
    SourceType,
    TaskCreate,
    TaskResponse,
    TaskStatus,
)
from app.services.auth import (
    DOWNLOAD_TICKET_EXPIRE_SECONDS,
    create_download_ticket,
    decode_download_ticket,
)
from app.services.build_dispatcher import start_build_task
from app.services.task_manager import task_manager
from app.services.permission import (
    get_current_user,
    get_user_permissions,
    has_permission,
    CurrentUser,
    require_permission,
    require_any_permission,
    PermissionCode,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["tasks"])

DB = Annotated[AsyncSession, Depends(get_db)]
AuthUser = Annotated[CurrentUser, Depends(get_current_user)]

# Directories to exclude from listing
EXCLUDED_DIRS = {
    "__pycache__",
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    ".venv",
    "venv",
    ".env",
    "env",
    ".idea",
    ".vscode",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "dist",
    "build",
    "egg-info",
    ".eggs",
}

# Allowed base paths for local-source file system access, from LOCAL_SOURCE_ROOTS.
# Empty by default: local-source mode is off until an operator lists trusted roots.
def _allowed_path_prefixes() -> tuple[str, ...]:
    from app.config import get_settings
    return tuple(get_settings().local_source_root_list)


def _config_for(raw_config: dict | None) -> BuildConfig | None:
    """Validate a stored config for display.

    Env values are returned as they were entered, deliberately. They were
    masked for everyone but the owner for a while, and that cost more than it
    bought: reading how a working build is configured is the main reason to
    open someone else's record on a shared build platform, and key names alone
    do not tell you what to put in yours.

    It bought little because no role can read another user's record without
    also holding task:create — 'user' is limited to history:view_own and
    'admin' is *:*. Anyone able to see a masked config could equally well
    submit a build that reads the database directly. The real boundary is
    account approval, as the trust model in CLAUDE.md says.

    What stays true is that these values are other systems' live credentials
    and history keeps them forever.
    """
    if raw_config is None:
        return None

    try:
        config = BuildConfig.model_validate(raw_config)
    except ValidationError:
        # A stored config is a record of what already ran, not a request to
        # run something — it has to survive a schema that has moved on since.
        # This list validates every row it returns, so one unreadable record
        # used to fail the whole request: tightening frontend_output_dir made
        # 73 of 698 rows undeserialisable and the history page went blank for
        # everyone who had one, with a Dockerfile-injection message that named
        # nothing the user could act on.
        logger.warning(
            "history config failed validation, listing the record without it",
            exc_info=True,
        )
        return None

    return config


def _validate_build_config_paths(config: BuildConfig) -> None:
    """Confine a local-mode build to the allowed roots, before anything runs.

    Seven read-only endpoints already route through _validate_allowed_path, and
    the README promises paths outside the configured LOCAL_SOURCE_ROOTS are refused.
    The one path that WRITES and RECURSIVELY DELETES did not check at all:
    create_task handed request.config straight to the dispatcher.

    That mattered because the build pipeline treats project_path as a place it
    owns. _build_frontend_only() does `shutil.rmtree(project_path/output_dir)`
    when the built output lands elsewhere, and nuitka_worker does the same for
    each data_dir — so project_path="/" with output_dir="etc" is an rmtree of
    /etc as the service user. frontend_worker writes frontend_env_content into
    project_path/frontend_dir/<.env name>, which is an arbitrary file write.

    "task:create is effectively shell access" does not cover this. That risk is
    bounded by the directories a user can already reach; this reached the whole
    filesystem, and contradicted a control the same file implements.

    Git mode is exempt: its project_path is assigned by _prepare_git_workspace
    into a per-task directory under git_workspace_dir, never supplied by the
    caller.
    """
    if config.source_type == SourceType.GIT:
        return

    project_root = _validate_allowed_path(config.project_path)

    # Directory names are joined onto project_root and then deleted or written
    # to. normpath collapses "a/../.." before the containment check, so a value
    # that walks out is rejected rather than silently escaping.
    def _require_inside(raw: str, field: str) -> None:
        for part in (raw or "").split(","):
            name = part.strip()
            if not name:
                continue
            candidate = (project_root / name).resolve()
            if not candidate.is_relative_to(project_root):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{field} 的 '{name}' 會指向專案目錄之外。"
                        "這些名稱是相對於專案路徑的子目錄,不能使用 '..' 或絕對路徑。"
                    ),
                )

    _require_inside(config.output_dir, "輸出目錄")
    _require_inside(config.data_dirs, "資料目錄")
    _require_inside(config.extra_dirs, "額外程式碼目錄")
    _require_inside(config.frontend_dir, "前端目錄")


def _validate_allowed_path(path: str) -> Path:
    """Validate that a path falls within allowed base directories."""
    resolved = Path(path).resolve()
    if not any(str(resolved).startswith(prefix) for prefix in _allowed_path_prefixes()):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be under {' or '.join(_allowed_path_prefixes())}",
        )
    return resolved


@router.get("/system-info")
async def get_system_info(current_user: AuthUser) -> dict:
    """Return server system information.

    - cpu_count: for the Nuitka jobs selector
    - python_versions: compile targets actually available on this server
      (detected from the per-version Nuitka venvs, newest first) — the
      frontend builds its version dropdown from this instead of hardcoding
    - git_workspace_dir: for display in git-mode UI hints
    """
    import sys

    from app.config import get_settings
    from app.services.nuitka_worker import _BUILD_CENTER_ROOT

    versions: list[str] = []
    for venv_dir in _BUILD_CENTER_ROOT.glob(".venv-py3*"):
        compact = venv_dir.name.removeprefix(".venv-py")  # e.g. "314"
        python_bin = venv_dir / "bin" / "python"
        if compact.isdigit() and len(compact) >= 2 and python_bin.exists():
            versions.append(f"{compact[0]}.{compact[1:]}")
    if not versions:
        # No dedicated venvs — only the service's own version can compile
        versions = [f"{sys.version_info.major}.{sys.version_info.minor}"]
    versions.sort(key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True)

    return {
        "cpu_count": os.cpu_count() or 1,
        "python_versions": versions,
        "git_workspace_dir": get_settings().git_workspace_dir,
    }


@router.get("/directories")
async def list_directories(
    current_user: AuthUser,
    _: None = Depends(require_permission(PermissionCode.TASK_CREATE)),
    path: str = Query(..., description="Project path to list directories from"),
) -> dict:
    """List subdirectories in the given project path."""
    try:
        project_path = _validate_allowed_path(path)

        if not project_path.exists():
            raise HTTPException(status_code=404, detail="Path does not exist")

        if not project_path.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")

        directories = []
        for item in sorted(project_path.iterdir()):
            # Check if it's a directory and not hidden (except .streamlit)
            if item.is_dir() and (not item.name.startswith(".") or item.name == ".streamlit"):
                # Skip excluded directories
                if item.name.lower() in EXCLUDED_DIRS or item.name.endswith(".egg-info"):
                    continue
                directories.append(item.name)

        return {"path": str(project_path), "directories": directories}

    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"Cannot access path: {e.strerror}")


@router.get("/analyze-project")
async def analyze_project(
    current_user: AuthUser,
    _: None = Depends(require_permission(PermissionCode.TASK_CREATE)),
    path: str = Query(..., description="Local project path to analyze"),
    entry_point: str = Query("main.py", description="Entry point for import analysis"),
) -> dict:
    """Analyze a LOCAL project and suggest what to bundle.

    Classifies each top-level dir as source/data/skip and flags the ones the
    entry point actually imports, so the CreateTask form can pre-fill and
    label extra_dirs / data_dirs instead of making the user judge each one.
    """
    from app.services.project_analysis import analyze_project_layout

    from app.services.nuitka_worker import detect_project_python_version

    project_path = _validate_allowed_path(path)
    if not project_path.exists() or not project_path.is_dir():
        raise HTTPException(status_code=404, detail="Path does not exist")

    analysis = await asyncio.to_thread(
        analyze_project_layout, str(project_path), entry_point
    )

    # Same ABI detection preflight runs — moved forward to form time.
    # It already knew the answer, just too late: the user had filled in four
    # steps, queued, and waited for the build to start before being told the
    # Python version they picked could not load their own .so files. One
    # project submitted 3.13 thirty-seven times that way. Telling them here
    # turns a failed build into a corrected dropdown.
    detected, detail = await asyncio.to_thread(
        detect_project_python_version, project_path
    )
    analysis["detected_python"] = {
        "version": detected,
        "detail": detail,
        # None with a "mixed" detail means the venv itself is inconsistent —
        # worth saying out loud, because no version choice can fix it.
        "inconsistent": detected is None and "mixed" in (detail or "").lower(),
    }
    return analysis


@router.get("/pyproject-groups")
async def list_pyproject_groups(
    current_user: AuthUser,
    _: None = Depends(require_permission(PermissionCode.TASK_CREATE)),
    path: str = Query(..., description="Project path containing pyproject.toml"),
) -> dict:
    """Return the PEP 735 ``[dependency-groups]`` names from a local project's
    pyproject.toml. A missing/unreadable file yields an empty list (the picker
    simply shows no options) rather than an error — local-mode parity with the
    git-mode scan-tree response.
    """
    from app.services.git_service import parse_dependency_groups

    project_path = _validate_allowed_path(path)
    pyproject = project_path / "pyproject.toml"
    if not pyproject.exists() or not pyproject.is_file():
        return {"groups": []}
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return {"groups": []}
    return {"groups": parse_dependency_groups(text)}


def _is_valid_env_filename(name: str) -> bool:
    """Check if a filename matches .env or .env.* pattern."""
    import re
    return bool(re.match(r"^\.env(\.\w+)*$", name))


@router.get("/detect-frontend-config")
async def detect_frontend_config(
    current_user: AuthUser,
    _: None = Depends(require_permission(PermissionCode.TASK_CREATE)),
    path: str = Query(..., description="Frontend directory path"),
) -> dict:
    """Detect frontend build configuration from vite.config and package.json.

    Reads vite.config.js/ts for build.outDir, package.json for scripts
    and packageManager, and lock files for build tool detection.
    """
    import json
    import re as _re

    fe_path = _validate_allowed_path(path)

    if not fe_path.exists():
        raise HTTPException(status_code=404, detail="Path does not exist")
    if not fe_path.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    result: dict = {
        "detected": False,
        "build_tool": None,
        "build_command": None,
        "output_dir": None,
        "scripts": [],
        "has_vite_config": False,
    }

    # 1. Detect vite.config and extract build.outDir
    vite_config_names = [
        "vite.config.ts", "vite.config.js",
        "vite.config.mts", "vite.config.mjs",
    ]
    for cfg_name in vite_config_names:
        cfg_file = fe_path / cfg_name
        if cfg_file.exists():
            result["has_vite_config"] = True
            try:
                content = cfg_file.read_text(encoding="utf-8")
                # Match outDir: 'xxx' or outDir: "xxx"
                match = _re.search(r"""outDir\s*:\s*['"](.+?)['"]""", content)
                if match:
                    result["output_dir"] = match.group(1)
            except Exception:
                pass
            break

    # 2. Read package.json for scripts and packageManager
    pkg_json = fe_path / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = pkg.get("scripts", {})
            # Collect build-related scripts
            result["scripts"] = list(scripts.keys())

            # Determine best build command
            for cmd in ["build", "build:prod", "build:production"]:
                if cmd in scripts:
                    result["build_command"] = cmd
                    break

            # Detect package manager from packageManager field
            pm_field = pkg.get("packageManager", "")
            if pm_field:
                pm_name = pm_field.split("@")[0].strip().lower()
                if pm_name in ("npm", "yarn", "pnpm", "bun"):
                    result["build_tool"] = pm_name
        except Exception:
            pass

    # 3. Detect build tool from lock files (fallback)
    if not result["build_tool"]:
        lock_map = {
            "bun.lockb": "bun",
            "bun.lock": "bun",
            "pnpm-lock.yaml": "pnpm",
            "yarn.lock": "yarn",
            "package-lock.json": "npm",
        }
        for lock_file, tool in lock_map.items():
            if (fe_path / lock_file).exists():
                result["build_tool"] = tool
                break

    result["detected"] = bool(
        result["has_vite_config"] or result["build_tool"] or result["build_command"]
    )

    return result


@router.get("/list-env-files")
async def list_env_files(
    current_user: AuthUser,
    _: None = Depends(require_permission(PermissionCode.TASK_CREATE)),
    path: str = Query(..., description="Directory path to scan for .env files"),
) -> dict:
    """List all .env* files in the given directory."""
    dir_path = _validate_allowed_path(path)

    if not dir_path.exists() or not dir_path.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    env_files = sorted(
        f.name for f in dir_path.iterdir()
        if f.is_file() and _is_valid_env_filename(f.name)
    )
    return {"path": str(dir_path), "files": env_files}


@router.get("/env-file")
async def read_env_file(
    current_user: AuthUser,
    _: None = Depends(require_permission(PermissionCode.TASK_CREATE)),
    path: str = Query(..., description="Frontend directory path"),
    filename: str = Query(".env", description="Env filename to read"),
) -> dict:
    """Read an existing env file from the given frontend directory."""
    if not _is_valid_env_filename(filename):
        raise HTTPException(status_code=400, detail="Invalid env filename")

    base_dir = _validate_allowed_path(path)
    env_path = (base_dir / filename).resolve()

    # Prevent path traversal
    if not env_path.is_relative_to(base_dir):
        raise HTTPException(status_code=403, detail="Access denied")

    if not env_path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found in {path}")

    if not env_path.is_file():
        raise HTTPException(status_code=400, detail=f"{filename} is not a file")

    try:
        content = env_path.read_text(encoding="utf-8")
        return {"path": str(env_path), "filename": filename, "content": content}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"Cannot read file: {e.strerror}")


# ─────────────────────────── Git source mode ───────────────────────────
# POST (not GET) is used so the URL never lands in nginx access logs or
# browser history. All GitLab token handling happens inside git_service;
# these routes only pass settings.gitlab_token through.

from pydantic import BaseModel as _PydBase  # local alias: keep top imports tidy


class GitRefsRequest(_PydBase):
    git_url: str


class GitDiagnoseRequest(_PydBase):
    path: str


class GitScanTreeRequest(_PydBase):
    git_url: str
    git_ref: str
    git_ref_type: str = "branch"  # "branch" or "tag"


class GitPreviewFrontendRequest(_PydBase):
    git_url: str
    git_ref: str
    git_ref_type: str = "branch"
    frontend_dir: str = "."


@router.post("/git/refs")
async def git_list_refs(
    request: GitRefsRequest,
    current_user: AuthUser,
    db: DB,
    _: None = Depends(require_permission(PermissionCode.TASK_CREATE)),
) -> dict:
    """List branches and tags for a remote Git URL.

    Runs `git ls-remote` server-side. The GitLab token (if configured) is
    injected into the URL only at subprocess call time and never returned.
    """
    from app.config import get_settings
    from app.services import git_credentials, git_service

    settings = get_settings()
    try:
        git_service.validate_git_url(request.git_url, settings.allowed_git_hosts)
    except git_service.GitUrlError as e:
        raise HTTPException(status_code=400, detail=str(e))

    token, provider = await git_credentials.resolve_for_url(db, request.git_url)
    try:
        refs = await git_service.list_refs(request.git_url, token, provider=provider)
    except git_service.GitUrlError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return refs


@router.post("/git/diagnose")
async def git_diagnose(
    request: GitDiagnoseRequest,
    current_user: AuthUser,
    _: None = Depends(require_permission(PermissionCode.TASK_CREATE)),
) -> dict:
    """Scan an already-cloned (or local) project directory and return a
    list of structural status chips (in Traditional Chinese)."""
    from app.services import git_service

    target = _validate_allowed_path(request.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path does not exist")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    return git_service.diagnose_repo(target)


@router.post("/git/preview-frontend")
async def git_preview_frontend(
    request: GitPreviewFrontendRequest,
    current_user: AuthUser,
    db: DB,
    _: None = Depends(require_permission(PermissionCode.TASK_CREATE)),
) -> dict:
    """Shallow-clone a repo and return its frontend config + .env* file
    contents for the CreateTask form preview. Used in git mode to give
    the Frontend Settings card the same auto-detect / load-env behaviour
    as local mode has.

    The temporary clone is removed before this function returns.
    """
    from app.config import get_settings
    from app.services import git_credentials, git_service

    settings = get_settings()
    if request.git_ref_type not in ("branch", "tag"):
        raise HTTPException(status_code=400, detail="git_ref_type must be 'branch' or 'tag'")

    try:
        git_service.validate_git_url(request.git_url, settings.allowed_git_hosts)
        git_service.validate_git_ref(request.git_ref)
    except git_service.GitUrlError as e:
        raise HTTPException(status_code=400, detail=str(e))

    token, provider = await git_credentials.resolve_for_url(db, request.git_url)
    try:
        result = await git_service.preview_frontend(
            url=request.git_url,
            ref=request.git_ref,
            ref_type=request.git_ref_type,  # type: ignore[arg-type]
            token=token,
            provider=provider,
            frontend_dir=request.frontend_dir or ".",
        )
    except git_service.GitUrlError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result


@router.post("/git/scan-tree")
async def git_scan_tree(
    request: GitScanTreeRequest,
    current_user: AuthUser,
    db: DB,
    _: None = Depends(require_permission(PermissionCode.TASK_CREATE)),
) -> dict:
    """List top-level directories of a remote Git repo at a specific ref
    WITHOUT a full clone. Uses a partial (blob-less) shallow clone to a
    temporary directory, which is removed before the response returns.

    Used by the CreateTask form in git mode to populate the
    extra_dirs / data_dirs checkbox grids (parity with local mode).
    """
    from app.config import get_settings
    from app.services import git_credentials, git_service

    settings = get_settings()
    if request.git_ref_type not in ("branch", "tag"):
        raise HTTPException(status_code=400, detail="git_ref_type must be 'branch' or 'tag'")

    try:
        git_service.validate_git_url(request.git_url, settings.allowed_git_hosts)
        git_service.validate_git_ref(request.git_ref)
    except git_service.GitUrlError as e:
        raise HTTPException(status_code=400, detail=str(e))

    token, provider = await git_credentials.resolve_for_url(db, request.git_url)
    try:
        result = await git_service.scan_tree(
            url=request.git_url,
            ref=request.git_ref,
            ref_type=request.git_ref_type,  # type: ignore[arg-type]
            token=token,
            provider=provider,
        )
    except git_service.GitUrlError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # result = {"directories": [...], "dependency_groups": [...]}
    return result


@router.post("/tasks", response_model=TaskResponse, status_code=201)
async def create_task(
    request: TaskCreate,
    background_tasks: BackgroundTasks,
    db: DB,
    current_user: AuthUser,
    _: None = Depends(require_permission(PermissionCode.TASK_CREATE)),
) -> TaskResponse:
    """Create a new build task (requires task:create permission).

    The task is created immediately and the build is started in the background.
    Connect to WebSocket endpoint to receive real-time updates.
    """
    # Inject user_name from authenticated user
    # Confine local-mode builds to the allowed roots before any worker runs —
    # this is the only request path that writes and deletes on disk.
    _validate_build_config_paths(request.config)

    request.user_name = current_user.username

    task = await task_manager.create_task(request, db)

    # Start build in background
    background_tasks.add_task(start_build_task, task.id, request.config)

    return task


@router.get("/tasks", response_model=list[TaskResponse])
async def get_active_tasks(
    current_user: AuthUser,
    _: None = Depends(require_any_permission(PermissionCode.TASK_VIEW_OWN, PermissionCode.TASK_VIEW_ALL)),
) -> list[TaskResponse]:
    """Get active (pending or running) tasks.

    - Users with task:view_all see all tasks
    - Users with task:view_own see only their own tasks
    """
    all_tasks = await task_manager.get_all_active_tasks()

    # Filter by ownership if user doesn't have view_all permission
    if not current_user.has_permission(PermissionCode.TASK_VIEW_ALL):
        all_tasks = [t for t in all_tasks if t.user_name == current_user.username]

    return all_tasks


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    current_user: AuthUser,
    _: None = Depends(require_any_permission(PermissionCode.TASK_VIEW_OWN, PermissionCode.TASK_VIEW_ALL)),
) -> TaskResponse:
    """Get a specific task by ID.

    - Users with task:view_all can see any task
    - Users with task:view_own can only see their own tasks
    """
    task = await task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Check ownership if user doesn't have view_all permission
    if not current_user.has_permission(PermissionCode.TASK_VIEW_ALL):
        if task.user_name != current_user.username:
            raise HTTPException(status_code=403, detail="Access denied")

    return task


@router.delete("/tasks/{task_id}")
async def cancel_task(
    task_id: str,
    db: DB,
    current_user: AuthUser,
    _: None = Depends(require_any_permission(PermissionCode.TASK_CANCEL_OWN, PermissionCode.TASK_CANCEL_ALL)),
) -> dict:
    """Cancel a running task or delete a completed task.

    - Users with task:cancel_all can cancel any task
    - Users with task:cancel_own can only cancel their own tasks
    """
    task = await task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Check ownership if user doesn't have cancel_all permission
    if not current_user.has_permission(PermissionCode.TASK_CANCEL_ALL):
        if task.user_name != current_user.username:
            raise HTTPException(status_code=403, detail="Access denied")

    if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
        success = await task_manager.cancel_task(task_id, db)
        if success:
            return {"message": "Task cancelled", "task_id": task_id}
        raise HTTPException(status_code=400, detail="Failed to cancel task")
    else:
        success = await task_manager.delete_task(task_id)
        if success:
            return {"message": "Task deleted", "task_id": task_id}
        raise HTTPException(status_code=400, detail="Failed to delete task")


@router.get("/tasks/{task_id}/logs")
async def get_task_logs(
    task_id: str,
    current_user: AuthUser,
    _: None = Depends(require_any_permission(PermissionCode.TASK_VIEW_OWN, PermissionCode.TASK_VIEW_ALL)),
) -> dict:
    """Get logs for a specific task.

    - Users with task:view_all can see any task's logs
    - Users with task:view_own can only see their own task's logs
    """
    task = await task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Check ownership if user doesn't have view_all permission
    if not current_user.has_permission(PermissionCode.TASK_VIEW_ALL):
        if task.user_name != current_user.username:
            raise HTTPException(status_code=403, detail="Access denied")

    logs = await task_manager.get_logs(task_id)
    return {"task_id": task_id, "logs": logs}


@router.get("/history", response_model=list[HistoryItem])
async def get_history(
    db: DB,
    current_user: AuthUser,
    _: None = Depends(require_any_permission(PermissionCode.HISTORY_VIEW_OWN, PermissionCode.HISTORY_VIEW_ALL)),
    limit: int = Query(default=200, ge=1, le=1000, description="Max records to return"),
    offset: int = Query(default=0, ge=0, description="Records to skip"),
    user_name: str | None = Query(default=None, description="Filter to a specific user (view_all only)"),
) -> list[HistoryItem]:
    """Get task history from database.

    - Users with history:view_all see all history, and may pass ``user_name``
      to narrow the list to one user.
    - Users with history:view_own only ever see their own; any ``user_name``
      param is ignored and forced to the caller.

    The ownership filter is applied in SQL before limit/offset, so the limit
    bounds the *returned* rows rather than a pre-filter window.
    """
    if current_user.has_permission(PermissionCode.HISTORY_VIEW_ALL):
        effective_user = user_name.strip() if user_name and user_name.strip() else None
    else:
        effective_user = current_user.username

    tasks = await task_manager.get_history(
        db, limit=limit, offset=offset, user_name=effective_user
    )

    return [
        HistoryItem(
            task_id=t.task_id,
            user_name=t.user_name,
            project_name=t.project_name,
            python_version=t.python_version,
            start_time=t.start_time,
            end_time=t.end_time,
            status=TaskStatus(t.status),
            output_dir=t.output_dir or "",
            config=_config_for(t.config),
        )
        for t in tasks
    ]


@router.get("/history/users", response_model=list[str])
async def list_history_users(
    db: DB,
    current_user: AuthUser,
    _: None = Depends(require_permission(PermissionCode.HISTORY_VIEW_ALL)),
) -> list[str]:
    """Distinct user_names present in history — powers the History page's
    "filter by user" dropdown. Restricted to history:view_all holders, since
    view_own users can only ever see themselves.

    NOTE: this route must stay declared BEFORE ``/history/{task_id}`` or the
    literal ``users`` segment would be captured as a task_id.
    """
    return await task_manager.get_history_usernames(db)


@router.get("/history/export")
async def export_history(
    db: DB,
    current_user: AuthUser,
    _: None = Depends(require_permission(PermissionCode.HISTORY_EXPORT)),
    user_name: str | None = Query(default=None, description="Filter to a specific user (view_all only)"),
) -> StreamingResponse:
    """Export task history as CSV (requires history:export permission).

    - Users with history:view_all export all history (optionally narrowed to
      one ``user_name``).
    - Users with history:view_own export only their own history.
    """
    if current_user.has_permission(PermissionCode.HISTORY_VIEW_ALL):
        effective_user = user_name.strip() if user_name and user_name.strip() else None
    else:
        effective_user = current_user.username

    # Export the full matching set, not just one page.
    # Page through the whole set rather than taking one 1000-row slice.
    #
    # The previous hardcoded limit=1000 was already close to binding — 680 rows
    # today, growing ~145 a month — and it fails in the worst way: the CSV looks
    # complete, carries no marker, and is still called history.csv, so an audit
    # or a yearly report silently omits the oldest builds with nothing to
    # suggest anything is missing.
    tasks = []
    page_size = 1000
    offset = 0
    while True:
        page = await task_manager.get_history(
            db, limit=page_size, offset=offset, user_name=effective_user
        )
        tasks.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
        # Backstop against an unbounded loop if get_history ever stops honouring
        # offset; 100k rows is far past any real history and still exports.
        if offset >= 100_000:
            break

    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow([
        "task_id",
        "user_name",
        "project_name",
        "python_version",
        "start_time",
        "end_time",
        "status",
        "output_dir",
    ])

    # Write data
    for t in tasks:
        writer.writerow([
            t.task_id,
            t.user_name,
            t.project_name,
            t.python_version,
            t.start_time.isoformat() if t.start_time else "",
            t.end_time.isoformat() if t.end_time else "",
            t.status,
            t.output_dir or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=history.csv"},
    )


@router.get("/history/{task_id}", response_model=HistoryItem)
async def get_history_item(
    task_id: str,
    db: DB,
    current_user: AuthUser,
    _: None = Depends(require_any_permission(PermissionCode.HISTORY_VIEW_OWN, PermissionCode.HISTORY_VIEW_ALL)),
) -> HistoryItem:
    """Get a single history record by task ID.

    - Users with history:view_all can see any record
    - Users with history:view_own can only see their own records
    """
    task = await task_manager.get_history_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="History record not found")

    # Check ownership if user doesn't have view_all permission
    if not current_user.has_permission(PermissionCode.HISTORY_VIEW_ALL):
        if task.user_name != current_user.username:
            raise HTTPException(status_code=403, detail="Access denied")

    return HistoryItem(
        task_id=task.task_id,
        user_name=task.user_name,
        project_name=task.project_name,
        python_version=task.python_version,
        start_time=task.start_time,
        end_time=task.end_time,
        status=TaskStatus(task.status),
        output_dir=task.output_dir or "",
        config=_config_for(task.config),
        result=task.result,
    )


@router.post("/history/{task_id}/download-ticket")
async def create_output_download_ticket(
    task_id: str,
    db: DB,
    current_user: AuthUser,
    _: None = Depends(require_any_permission(PermissionCode.HISTORY_VIEW_OWN, PermissionCode.HISTORY_VIEW_ALL)),
) -> dict:
    """Exchange the caller's session for a short-lived download ticket.

    The browser's native downloader cannot send an Authorization header, and
    routing a multi-GB artifact through XHR would buffer it entirely in page
    memory (no resume, no progress). This hands back a ticket that travels in
    the URL instead — see create_download_ticket() for why that is safe here
    and not for the session token.
    """
    task = await task_manager.get_history_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not current_user.has_permission(PermissionCode.HISTORY_VIEW_ALL):
        if task.user_name != current_user.username:
            raise HTTPException(status_code=403, detail="Access denied")

    if task.status != "completed":
        raise HTTPException(status_code=400, detail="Only completed tasks can be downloaded")

    return {
        "ticket": create_download_ticket(current_user.id, task_id),
        "expires_in": DOWNLOAD_TICKET_EXPIRE_SECONDS,
    }


@router.get("/download/{ticket}")
async def download_output_with_ticket(ticket: str, db: DB) -> Response:
    """Serve a build artifact to a ticket holder.

    Deliberately has no auth dependency: the ticket IS the credential. It is
    still re-validated against live state — an account disabled or a permission
    revoked between minting and redemption must take effect immediately.
    """
    decoded = decode_download_ticket(ticket)
    if not decoded:
        # This lands in the browser's download manager, not in the SPA, so it is
        # the only text the user gets — Chrome reduces the status alone to
        # "Needs authorization", which explains nothing.
        raise HTTPException(
            status_code=401,
            detail=(
                "下載連結已失效。請回到 Build Center 重新點一次下載 —— "
                "連結有時效,不能重複使用或分享給別人。"
            ),
        )
    user_id, task_id = decoded

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    permissions = await get_user_permissions(db, user.id)
    can_view_all = has_permission(permissions, PermissionCode.HISTORY_VIEW_ALL.value)
    if not can_view_all and not has_permission(
        permissions, PermissionCode.HISTORY_VIEW_OWN.value
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    task = await task_manager.get_history_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not can_view_all and task.user_name != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    return _serve_task_output(task)


@router.get("/history/{task_id}/download")
async def download_output(
    task_id: str,
    db: DB,
    current_user: AuthUser,
    _: None = Depends(require_any_permission(PermissionCode.HISTORY_VIEW_OWN, PermissionCode.HISTORY_VIEW_ALL)),
) -> Response:
    """Download the build output as a .tar.gz archive.

    Header-authenticated entry point, kept for API/CLI callers. The web UI goes
    through the ticket route above so the browser downloads natively.
    """
    task = await task_manager.get_history_task(db, task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Check ownership if user doesn't have view_all permission
    if not current_user.has_permission(PermissionCode.HISTORY_VIEW_ALL):
        if task.user_name != current_user.username:
            raise HTTPException(status_code=403, detail="Access denied")

    return _serve_task_output(task)


def _serve_task_output(task) -> Response:
    """Build the response that streams a completed task's artifact.

    Shared by both download entry points so the permission model is the only
    thing that differs between them.
    """
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="Only completed tasks can be downloaded")

    if not task.config:
        raise HTTPException(status_code=400, detail="Task has no config")

    project_path = Path(task.config.get("project_path", ""))
    output_dir_name = task.config.get("output_dir", "dist")
    output_path = (project_path / output_dir_name).resolve()

    # Path traversal check
    if not output_path.is_relative_to(project_path.resolve()):
        raise HTTPException(status_code=403, detail="Invalid output path")

    # Docker builds: the exported image .tar.gz already exists on disk, so serve
    # it with FileResponse rather than a hand-rolled StreamingResponse. This is
    # what fixes the "download runs to 100% then shows failed" symptom on big
    # (multi-GB) images: StreamingResponse sent NO Content-Length (chunked
    # transfer), so the browser couldn't tell a clean EOF from a dropped
    # connection and flagged a fully-received file as failed. FileResponse sets
    # Content-Length (exact size → clean completion), supports HTTP Range (so an
    # interrupted download can RESUME instead of restarting), and uses sendfile
    # for speed.
    if task.config.get("docker_enabled"):
        docker_export_dir = Path(get_settings().docker_images_dir)
        # Reconstruct the exported filename from image name
        image_name = task.config.get("docker_image_name", "") or f"{project_path.name}:latest"
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", image_name)
        image_file = docker_export_dir / f"{safe_name}.tar.gz"

        if not image_file.exists():
            raise HTTPException(status_code=404, detail="Docker image export not found")

        # Use docker_image_name for download filename, fallback to project name
        dl_name = task.config.get("docker_image_name", "") or task.project_name
        dl_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", dl_name)
        return FileResponse(
            path=image_file,
            media_type="application/gzip",
            filename=f"{dl_name}.tar.gz",  # sets Content-Disposition: attachment
            headers={"X-Accel-Buffering": "no"},  # no-op without nginx; harmless
        )

    if not output_path.exists() or not output_path.is_dir():
        raise HTTPException(status_code=404, detail="Output directory not found")

    # Stream the .tar.gz to the client AS IT IS BUILT, rather than building the
    # whole archive to a temp file first and only then streaming. The old
    # approach broke on big GPU/ML outputs (torch/rocm libs run to several GB):
    #   * the synchronous tarfile.add() blocked the async event loop for the
    #     entire build — freezing every other request AND delaying the FIRST
    #     response byte by minutes, so the browser gave up ("system busy") before
    #     the download even started;
    #   * the temp file could exhaust a small /tmp tmpfs.
    # `w|gz` is tarfile's non-seekable STREAMING mode: compressed bytes flow out
    # chunk by chunk. The blocking compression runs in a worker thread and hands
    # chunks to the event loop through a small bounded queue (natural backpressure
    # — the worker parks when the queue is full, so we never buffer the whole
    # archive in RAM).
    dl_name = task.config.get("output_name", "") or task.project_name
    dl_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", dl_name)
    src_dir = str(output_path)
    arcname = output_dir_name

    async def stream_targz() -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        chunks: asyncio.Queue = asyncio.Queue(maxsize=8)
        abort = threading.Event()

        class _QueueWriter:
            """File-like sink handed to tarfile. Each compressed chunk written by
            the worker thread is forwarded onto the event loop's queue; the
            .result() call blocks the worker while the queue is full, giving us
            backpressure without buffering the whole archive."""

            def write(self, data: bytes) -> int:
                if abort.is_set():
                    raise BrokenPipeError("client disconnected")
                asyncio.run_coroutine_threadsafe(
                    chunks.put(bytes(data)), loop
                ).result()
                return len(data)

            def flush(self) -> None:
                pass

        def _build() -> None:
            # Runs in a thread. Sentinel: None = clean EOF, Exception = failure.
            sentinel: object = None
            try:
                # compresslevel=1: build outputs are mostly ALREADY-compressed
                # binaries (Nuitka onefile exe, .so libs) — near-incompressible, so
                # gzip level 9 burns CPU for almost no size gain and throttles a
                # multi-GB download to a crawl (the browser then times out). Level 1
                # streams several times faster at nearly the same size.
                with tarfile.open(
                    fileobj=_QueueWriter(), mode="w|gz", compresslevel=1
                ) as tar:
                    tar.add(src_dir, arcname=arcname)
            except BaseException as exc:  # surface the error to the consumer
                sentinel = exc
            asyncio.run_coroutine_threadsafe(chunks.put(sentinel), loop)

        worker = loop.run_in_executor(None, _build)
        try:
            while True:
                item = await chunks.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            # Normal finish: no-op. Client disconnect: unblock a worker parked on
            # a full queue (drain a slot), then let it observe `abort` and stop.
            abort.set()
            try:
                while True:
                    chunks.get_nowait()
            except asyncio.QueueEmpty:
                pass
            await asyncio.gather(worker, return_exceptions=True)

    return StreamingResponse(
        stream_targz(),
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{dl_name}.tar.gz"',
            # Tell nginx NOT to buffer this response to its own temp file before
            # forwarding — otherwise the multi-GB stream gets re-serialized
            # ("build fully, then send"), reviving the timeout/abort symptom.
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/history/{task_id}/workspace")
async def delete_task_workspace(
    task_id: str,
    db: DB,
    current_user: AuthUser,
    _: None = Depends(
        require_any_permission(
            PermissionCode.HISTORY_VIEW_OWN,
            PermissionCode.HISTORY_VIEW_ALL,
        )
    ),
) -> dict:
    """Force-delete the per-task workspace on disk for a git-sourced task.

    Only removes the clone directory under ``GIT_WORKSPACE_DIR``; the
    history DB row is left untouched so the task remains visible in History.

    Refuses for running/pending tasks (their workspace is in active use)
    and for local-source tasks (we never manage those paths).
    """
    from app.services import workspace_cleanup

    task = await task_manager.get_history_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Ownership check unless the user has view_all.
    if not current_user.has_permission(PermissionCode.HISTORY_VIEW_ALL):
        if task.user_name != current_user.username:
            raise HTTPException(status_code=403, detail="Access denied")

    status = (task.status or "").lower()
    if status in ("running", "pending"):
        raise HTTPException(
            status_code=400,
            detail="Cannot delete workspace of a running/pending task",
        )

    if not task.config or task.config.get("source_type") != "git":
        raise HTTPException(
            status_code=400,
            detail="Task is not git-sourced — no managed workspace to delete",
        )

    try:
        deleted = await asyncio.to_thread(
            workspace_cleanup.delete_one_workspace, task_id
        )
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"task_id": task_id, "deleted": deleted}
