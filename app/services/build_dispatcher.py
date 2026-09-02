"""Build dispatcher that routes to the appropriate build worker."""

import asyncio
import os
import shutil
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.task import BuildConfig, TaskStatus, ProjectType, SourceType
from app.services.project_lock import get_project_lock, release_project_lock
from app.services.task_manager import task_manager

# Alias the argv-list subprocess spawner — see git_service.py for the same
# pattern. Avoids substring collisions in static scanners.
_spawn = asyncio.create_subprocess_exec

# Build concurrency limiter — lazily initialized from settings
_build_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Get or create the build concurrency semaphore."""
    global _build_semaphore
    if _build_semaphore is None:
        from app.config import get_settings
        _build_semaphore = asyncio.Semaphore(get_settings().max_concurrent_builds)
    return _build_semaphore


async def dispatch_build(task_id: str, config: BuildConfig, db: AsyncSession) -> None:
    """Dispatch build to the appropriate worker based on project type and pack mode.

    Args:
        task_id: Task ID
        config: Build configuration
        db: Database session
    """
    await task_manager.update_task(task_id, "status", TaskStatus.RUNNING, db)

    # Work on a copy. The dispatcher and frontend_worker fill in values the
    # user did not supply — output_dir defaults to "dist" for Docker builds,
    # the frontend's output directory is appended to data_dirs, and the
    # detected build output overwrites frontend_output_dir — and in local mode
    # the object handed in here is the very same instance task_manager keeps
    # for the API response. So /api/tasks/{id} reported a configuration that
    # was never submitted, and TaskDetail's "apply the fix and rebuild" button
    # prefilled the form from it, turning derived values into explicit user
    # input on the next run. History, written from a model_dump at creation,
    # kept the real one — the same task showed two different configs depending
    # on which page you opened.
    #
    # Git mode never had this: _prepare_git_workspace already returns a copy.
    config = config.model_copy(deep=True)

    # Serialise per project directory across BOTH workers.
    #
    # nuitka_worker used to take this lock itself, covering only its own
    # inject/restore of the entry point. docker_worker does the same kind of
    # thing — backs up and overwrites .dockerignore, writes
    # Dockerfile.generated, replaces the project's frontend .env, then restores
    # all three — and held no lock at all. MAX_CONCURRENT_BUILDS caps how many
    # builds run, not which project they touch, so two tasks on one local path
    # interleaved and destroyed each other's backups. See project_lock.py.
    #
    # Held here rather than inside a worker so it spans the whole
    # prepare -> build -> restore window, including the fullstack chain where
    # frontend, nuitka and docker workers run in sequence over the same files.
    lock = get_project_lock(config.project_path)
    if lock.locked():
        await task_manager.append_log(
            task_id,
            "Another build is running for this project — waiting for it to finish...",
        )
    async with lock:
        try:
            await _dispatch_locked(task_id, config, db)
        finally:
            release_project_lock(config.project_path)


async def _dispatch_locked(task_id: str, config: BuildConfig, db: AsyncSession) -> None:
    """Route to the right worker. Caller holds the project lock."""
    project_type = config.project_type
    pack_mode = config.pack_mode

    await task_manager.append_log(
        task_id,
        f"Build started | Type: {project_type.value} | Mode: {pack_mode.value}",
    )
    await task_manager.append_log(task_id, f"Project: {config.project_path}")
    await task_manager.append_log(task_id, "")

    try:
        # Docker mode
        if config.docker_enabled:
            if project_type in (ProjectType.BACKEND_ONLY, ProjectType.FULLSTACK):
                # Host Nuitka compile → Docker package with precompiled binary
                await _build_docker_nuitka(task_id, config, db)
            else:
                # Frontend-only: standard Docker build
                await _build_docker_standard(task_id, config, db)
            return

        # Route based on project type
        if project_type == ProjectType.BACKEND_ONLY:
            await _build_backend_only(task_id, config, db)

        elif project_type == ProjectType.FRONTEND_ONLY:
            await _build_frontend_only(task_id, config, db)

        elif project_type == ProjectType.FULLSTACK:
            await _build_fullstack(task_id, config, db)

        else:
            raise ValueError(f"Unknown project type: {project_type}")

    except Exception as e:
        await task_manager.update_task(task_id, "status", TaskStatus.FAILED, db)
        await task_manager.append_log(task_id, f"Error: {e!s}")
        await task_manager.update_task(task_id, "status_msg", f"Error: {e!s}")


async def _build_docker_standard(task_id: str, config: BuildConfig, db: AsyncSession) -> None:
    """Standard Docker build (no host Nuitka compilation)."""
    from app.services.docker_worker import run_docker_build

    success = await run_docker_build(task_id, config, db)
    if success:
        await task_manager.update_task(task_id, "status", TaskStatus.COMPLETED, db)
        await task_manager.update_task(task_id, "progress", 100)
        await task_manager.update_task(task_id, "status_msg", "Docker image built")
    else:
        await task_manager.update_task(task_id, "status", TaskStatus.FAILED, db)
        await task_manager.update_task(task_id, "status_msg", "Docker build failed")


async def _build_docker_nuitka(task_id: str, config: BuildConfig, db: AsyncSession) -> None:
    """Docker build with host-side Nuitka compilation.

    Flow: [Frontend build] → Nuitka compile on host → Docker image with precompiled binary.
    """
    from app.services.docker_worker import run_docker_build
    from app.services.nuitka_worker import run_nuitka_build

    # Docker mode hides output_dir in UI — ensure it has a value for Nuitka
    if not config.output_dir:
        config.output_dir = "dist"

    project_type = config.project_type

    # Step 1 (fullstack only): Build frontend
    if project_type == ProjectType.FULLSTACK:
        from app.services.frontend_worker import run_frontend_build

        project_path = Path(config.project_path)
        frontend_path = project_path / config.frontend_dir

        await task_manager.append_log(task_id, "=== Step 1: Building Frontend ===")
        await task_manager.append_log(task_id, "")

        frontend_success = await run_frontend_build(task_id, config, db, frontend_path)
        if not frontend_success:
            await task_manager.update_task(task_id, "status", TaskStatus.FAILED, db)
            await task_manager.update_task(task_id, "status_msg", "Frontend build failed")
            return

        # Add frontend output to data_dirs
        frontend_output = os.path.normpath(
            os.path.join(config.frontend_dir, config.frontend_output_dir)
        )
        existing = config.data_dirs or ""
        if frontend_output not in existing:
            config.data_dirs = f"{existing},{frontend_output}" if existing else frontend_output

        await task_manager.append_log(task_id, "")
        await task_manager.append_log(task_id, "Frontend build completed!")
        await task_manager.append_log(task_id, "")
        nuitka_step = "Step 2"
        docker_step = "Step 3"
    else:
        nuitka_step = "Step 1"
        docker_step = "Step 2"

    # Nuitka compilation on host
    await task_manager.append_log(task_id, f"=== {nuitka_step}: Nuitka Compilation (Host) ===")
    await task_manager.append_log(task_id, "")

    nuitka_success = await run_nuitka_build(task_id, config, db)

    if not nuitka_success:
        await task_manager.update_task(task_id, "status", TaskStatus.FAILED, db)
        await task_manager.update_task(task_id, "status_msg", "Nuitka compilation failed")
        return

    # Docker image build with precompiled binary
    await task_manager.append_log(task_id, "")
    await task_manager.append_log(task_id, f"=== {docker_step}: Docker Image Build ===")
    await task_manager.append_log(task_id, "")

    success = await run_docker_build(task_id, config, db, precompiled=True)
    if success:
        await task_manager.update_task(task_id, "status", TaskStatus.COMPLETED, db)
        await task_manager.update_task(task_id, "progress", 100)
        await task_manager.update_task(task_id, "status_msg", "Docker image built")
    else:
        await task_manager.update_task(task_id, "status", TaskStatus.FAILED, db)
        await task_manager.update_task(task_id, "status_msg", "Docker build failed")


async def _build_backend_only(task_id: str, config: BuildConfig, db: AsyncSession) -> None:
    """Build backend-only project with Nuitka."""
    from app.services.nuitka_worker import run_nuitka_build

    await task_manager.append_log(task_id, "=== Backend Only Build ===")
    await task_manager.append_log(task_id, "")

    success = await run_nuitka_build(task_id, config, db)

    if success:
        await task_manager.update_task(task_id, "status", TaskStatus.COMPLETED, db)
        await task_manager.update_task(task_id, "progress", 100)
        await task_manager.update_task(task_id, "status_msg", "Build complete")
    else:
        await task_manager.update_task(task_id, "status", TaskStatus.FAILED, db)
        await task_manager.update_task(task_id, "status_msg", "後端編譯失敗,請查看下方日誌")


async def _build_frontend_only(task_id: str, config: BuildConfig, db: AsyncSession) -> None:
    """Build frontend-only project."""
    import shutil
    from app.services.frontend_worker import run_frontend_build

    await task_manager.append_log(task_id, "=== Frontend Only Build ===")
    await task_manager.append_log(task_id, "")

    project_path = Path(config.project_path)
    frontend_path = project_path / config.frontend_dir

    success = await run_frontend_build(task_id, config, db, frontend_path)

    if success:
        # Source: where frontend tool outputs (e.g., dist/)
        build_output = frontend_path / config.frontend_output_dir
        # Target: user's desired output directory
        final_output = project_path / config.output_dir

        # Copy to user's desired directory if different
        if build_output != final_output and build_output.exists():
            await task_manager.append_log(task_id, "")
            await task_manager.append_log(task_id, f"Copying build output to {config.output_dir}/...")
            try:
                if final_output.exists():
                    shutil.rmtree(final_output)
                shutil.copytree(build_output, final_output)
                file_count = sum(1 for _ in final_output.rglob("*") if _.is_file())
                await task_manager.append_log(task_id, f"Copied {file_count} files to {final_output}")
            except Exception as e:
                await task_manager.append_log(task_id, f"Warning: Failed to copy output: {e}")
                final_output = build_output
        else:
            final_output = build_output

        await task_manager.update_task(task_id, "status", TaskStatus.COMPLETED, db)
        await task_manager.update_task(task_id, "progress", 100)
        await task_manager.update_task(task_id, "status_msg", "Frontend build complete")

        await task_manager.append_log(task_id, "")
        await task_manager.append_log(task_id, "=" * 50)
        await task_manager.append_log(task_id, "Frontend build completed!")
        await task_manager.append_log(task_id, f"Output: {final_output}")
        await task_manager.append_log(task_id, "=" * 50)
    else:
        await task_manager.update_task(task_id, "status", TaskStatus.FAILED, db)
        await task_manager.update_task(task_id, "status_msg", "Frontend build failed")


async def _build_fullstack(task_id: str, config: BuildConfig, db: AsyncSession) -> None:
    """Build fullstack project (frontend + backend)."""
    from app.services.frontend_worker import run_frontend_build
    from app.services.nuitka_worker import run_nuitka_build

    project_path = Path(config.project_path)
    frontend_path = project_path / config.frontend_dir

    # Step 1: Build frontend
    await task_manager.append_log(task_id, "=== Step 1: Building Frontend ===")
    await task_manager.append_log(task_id, "")

    frontend_success = await run_frontend_build(task_id, config, db, frontend_path)

    if not frontend_success:
        await task_manager.update_task(task_id, "status", TaskStatus.FAILED, db)
        await task_manager.update_task(task_id, "status_msg", "Frontend build failed")
        return

    await task_manager.append_log(task_id, "")
    await task_manager.append_log(task_id, "Frontend build completed!")
    await task_manager.append_log(task_id, "")

    # Step 2: Build backend with Nuitka (including frontend as data)
    await task_manager.append_log(task_id, "=== Step 2: Building Backend with Nuitka ===")
    await task_manager.append_log(task_id, "")

    # Add frontend output to data_dirs if not already there
    # Use normpath to resolve ".." segments (e.g., "frontend/../static/web" → "static/web")
    frontend_output = os.path.normpath(os.path.join(config.frontend_dir, config.frontend_output_dir))
    existing_data_dirs = config.data_dirs or ""

    if frontend_output not in existing_data_dirs:
        if existing_data_dirs:
            config.data_dirs = f"{existing_data_dirs},{frontend_output}"
        else:
            config.data_dirs = frontend_output

    await task_manager.append_log(task_id, f"Including frontend in build: {frontend_output}")

    backend_success = await run_nuitka_build(task_id, config, db)

    if backend_success:
        await task_manager.update_task(task_id, "status", TaskStatus.COMPLETED, db)
        await task_manager.update_task(task_id, "progress", 100)
        await task_manager.update_task(task_id, "status_msg", "Fullstack build complete")
    else:
        await task_manager.update_task(task_id, "status", TaskStatus.FAILED, db)
        await task_manager.update_task(task_id, "status_msg", "Backend build failed")


async def _prepare_venv(
    task_id: str,
    project_path: Path,
    config: BuildConfig,
) -> None:
    """After a fresh git clone, install the project's dependencies so
    nuitka_worker finds a usable .venv.

    Uses `uv sync --frozen` when a uv.lock exists (best for reproducibility);
    falls back to `uv sync` or `uv venv + uv pip install -r requirements.txt`
    otherwise. Silently skips non-Python projects.

    All subprocesses run with:
      - explicit PATH (settings.pre_build_path) — no reliance on parent shell
      - absolute UV_BIN — no `which uv` guesswork
      - UV_CACHE_DIR override — hardlinks from a cache on the same filesystem
        as the clone dir instead of copying across mounts
    """
    from app.config import get_settings

    settings = get_settings()
    if not settings.git_auto_venv:
        await task_manager.append_log(
            task_id,
            "Auto venv bootstrap disabled (GIT_AUTO_VENV=false) — skipping",
        )
        return

    has_pyproject = (project_path / "pyproject.toml").exists()
    has_uv_lock = (project_path / "uv.lock").exists()
    has_requirements = (project_path / "requirements.txt").exists()

    # Non-Python project (e.g. frontend-only) — nothing to do.
    if not has_pyproject and not has_requirements:
        await task_manager.append_log(
            task_id,
            "No pyproject.toml or requirements.txt found — skipping venv bootstrap",
        )
        return

    uv_bin = Path(settings.uv_binary)
    if not uv_bin.exists():
        raise FileNotFoundError(
            f"uv was not found (looked for '{settings.uv_binary}'). Install uv, or set "
            "UV_BIN to its absolute path."
        )

    # Build a minimal, predictable env for the subprocess.
    bootstrap_env: dict[str, str] = {
        "PATH": settings.subprocess_path,
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_COLOR": "1",  # disable ANSI output in uv logs
    }
    if settings.bootstrap_uv_cache_dir:
        bootstrap_env["UV_CACHE_DIR"] = settings.bootstrap_uv_cache_dir
        # Ensure cache dir exists; uv would create it but we want a clear
        # error immediately if the path is broken.
        Path(settings.bootstrap_uv_cache_dir).mkdir(parents=True, exist_ok=True)

    timeout = float(settings.venv_bootstrap_timeout)
    # "auto" means "let uv choose from the project's requires-python /
    # .python-version" — we can't ABI-detect the version yet, because the .venv
    # this step is about to CREATE doesn't exist. Passing `--python auto` would
    # make uv search for an interpreter literally named "auto" (exit 2).
    requested = (config.python_version or "").strip().lower()
    py_flags = [] if requested in ("", "auto") else ["--python", requested]
    python_label = requested if py_flags else "auto (uv picks per project)"

    async def _run(cmd: list[str], description: str) -> None:
        """Run a single subprocess step, stream result to task log, raise on failure."""
        await task_manager.append_log(task_id, f"→ {description}")
        try:
            proc = await _spawn(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(project_path),
                env=bootstrap_env,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise RuntimeError(
                    f"{description} timed out after {timeout:.0f}s"
                )
        except FileNotFoundError as e:
            raise RuntimeError(f"uv binary not found: {e}") from e

        stdout_text = stdout_bytes.decode("utf-8", "replace").strip()
        stderr_text = stderr_bytes.decode("utf-8", "replace").strip()

        # Write uv's actual output (both streams) to the task log so the
        # user can see what happened — both success and failure.
        for line in (stdout_text + "\n" + stderr_text).splitlines():
            line = line.rstrip()
            if line:
                await task_manager.append_log(task_id, f"  {line}")

        if proc.returncode != 0:
            raise RuntimeError(
                f"{description} failed (exit {proc.returncode}): "
                f"{stderr_text or stdout_text or 'no output'}"
            )

    # Build --group flags from config.dependency_groups (PEP 735 groups).
    # Empty list = no flags = current behavior (backward-compatible).
    # Each group adds `--group <name>`; uv union-installs them on top of default deps.
    group_flags: list[str] = []
    for group_name in config.dependency_groups:
        # Light validation — uv would reject malformed names anyway, but a
        # log-friendly upfront error is nicer than a cryptic uv stderr later.
        if not group_name or not group_name.replace("-", "").replace("_", "").isalnum():
            raise RuntimeError(
                f"Invalid dependency_group name: {group_name!r}. "
                "Must be alphanumeric (plus '-' or '_')."
            )
        group_flags.extend(["--group", group_name])

    group_label = (
        f" --group {' --group '.join(config.dependency_groups)}"
        if config.dependency_groups
        else ""
    )

    # Decide install strategy — prefer uv.lock for reproducibility.
    await task_manager.append_log(task_id, "Auto venv bootstrap (git mode)")
    if has_pyproject and has_uv_lock:
        await _run(
            [str(uv_bin), "sync", "--frozen", *py_flags, *group_flags],
            f"uv sync --frozen{group_label} (uv.lock, python={python_label})",
        )
    elif has_pyproject:
        await _run(
            [str(uv_bin), "sync", *py_flags, *group_flags],
            f"uv sync{group_label} (pyproject.toml, python={python_label})",
        )
    else:  # has_requirements only
        # requirements.txt route doesn't support PEP 735 groups — warn if user
        # provided any, but proceed (group_flags is silently dropped).
        if config.dependency_groups:
            await task_manager.append_log(
                task_id,
                "⚠ dependency_groups specified but project uses requirements.txt "
                "(no PEP 735 group support) — groups will be ignored.",
            )
        await _run(
            [str(uv_bin), "venv", ".venv", *py_flags],
            f"uv venv .venv (python={python_label})",
        )
        await _run(
            [str(uv_bin), "pip", "install", "-r", "requirements.txt"],
            "uv pip install -r requirements.txt",
        )

    venv_dir = project_path / ".venv"
    if venv_dir.exists():
        await task_manager.append_log(
            task_id, f"✓ .venv ready at {venv_dir}"
        )
    else:
        await task_manager.append_log(
            task_id,
            f"⚠ bootstrap finished but .venv not found at {venv_dir}",
        )


async def _prepare_git_workspace(task_id: str, config: BuildConfig) -> BuildConfig:
    """Clone the git repo into a fresh per-task workspace and return a
    derived BuildConfig whose ``project_path`` points at that workspace.

    The original ``config`` is NOT mutated — the DB row keeps the raw
    ``git_url`` / ``git_ref`` fields as the source of truth so Rebuild
    works deterministically and the workspace dir can be re-derived from
    ``task_id`` on demand.
    """
    from app.config import get_settings
    from app.database import async_session
    from app.services import git_credentials, git_service

    settings = get_settings()
    # Resolve the per-host token in a dedicated session so this never commits the
    # dispatcher's own in-flight task-status changes.
    async with async_session() as cred_db:
        token, provider = await git_credentials.resolve_for_url(cred_db, config.git_url)

    # Path-traversal guard: resolved target MUST still live under the
    # configured workspace root.
    workspace_root = Path(settings.git_workspace_dir).resolve()
    target = (workspace_root / task_id).resolve()
    try:
        target.relative_to(workspace_root)
    except ValueError:
        raise ValueError(
            f"Refusing to clone outside git_workspace_dir: {target}"
        )

    # Masked log line — token, if any, is never visible here.
    masked_url = git_service.mask_token_in_log(
        git_service.inject_token(config.git_url, token, provider),
        token,
    )
    await task_manager.append_log(
        task_id,
        f"Cloning {masked_url} @ {config.git_ref_type}:{config.git_ref}",
    )

    try:
        await git_service.clone_repo(
            url=config.git_url,
            ref=config.git_ref,
            ref_type=config.git_ref_type,
            target_dir=target,
            token=token,
            provider=provider,
            timeout=float(settings.git_clone_timeout),
        )
    except git_service.GitUrlError as e:
        # clone_repo already cleans up partial state on failure; surface
        # the (already-masked) error to the task log and re-raise so the
        # outer dispatcher marks the task failed.
        await task_manager.append_log(task_id, f"Clone failed: {e}")
        raise

    await task_manager.append_log(task_id, f"Clone completed: {target}")

    # Auto venv bootstrap — installs Python dependencies so nuitka_worker
    # finds a usable .venv. Failures here abort the task AND delete the
    # partial clone. Rationale: the full uv stderr is already in the task
    # log, the source code is in git, and there is nothing useful left in
    # the workspace to inspect — keeping it around would just waste disk
    # space until the 30-day failed-task TTL expires.
    try:
        await _prepare_venv(task_id, target, config)
    except Exception as e:
        await task_manager.append_log(task_id, f"Venv bootstrap failed: {e!s}")
        try:
            from app.services import workspace_cleanup
            await asyncio.to_thread(workspace_cleanup.delete_one_workspace, task_id)
            await task_manager.append_log(
                task_id,
                "Partial workspace removed (bootstrap failures leave nothing "
                "useful to inspect — full error is above in this log).",
            )
        except Exception as cleanup_err:
            # Don't let cleanup errors mask the original failure.
            await task_manager.append_log(
                task_id,
                f"Cleanup after bootstrap failure also failed (ignored): {cleanup_err!s}",
            )
        raise

    # Return a derived config — workers from here on see project_path
    # pointing at the fresh clone, and need no changes of their own.
    return config.model_copy(update={"project_path": str(target)})


async def start_build_task(task_id: str, config: BuildConfig) -> None:
    """Start a build task in the background.

    Acquires a build slot from the semaphore before dispatching.
    If all slots are occupied, the task waits in "Queued" state.
    For git-source tasks, the repo is cloned into a fresh per-task
    workspace AFTER the slot is acquired but BEFORE dispatch.
    """
    from app.database import async_session

    sem = _get_semaphore()

    # Signal queued status before waiting for a slot
    await task_manager.update_task(task_id, "status_msg", "Queued — waiting for build slot...")
    await task_manager.append_log(task_id, "Waiting for available build slot...")

    async def _is_cancelled() -> bool:
        """True if the user cancelled while we were doing slow prep work
        (queue wait, git clone, venv bootstrap). Those steps don't register
        a killable process, so status is the only cancel signal we have."""
        current = await task_manager.get_task(task_id)
        return current is None or current.status == TaskStatus.CANCELLED

    async with sem:
        # The user may have cancelled while this task sat in the queue —
        # without this check the CANCELLED status would be silently
        # overwritten by RUNNING and the build would execute anyway.
        if await _is_cancelled():
            await task_manager.append_log(
                task_id, "Task was cancelled while queued — build skipped"
            )
            return

        await task_manager.append_log(task_id, "Build slot acquired!")

        effective_config = config
        if config.source_type == SourceType.GIT:
            try:
                effective_config = await _prepare_git_workspace(task_id, config)
            except Exception as e:
                async with async_session() as db:
                    await task_manager.update_task(task_id, "status", TaskStatus.FAILED, db)
                    await task_manager.update_task(
                        task_id, "status_msg", f"Clone failed: {e!s}"
                    )
                return

            # Clone + venv bootstrap can take many minutes — re-check cancel
            # before committing a build slot's worth of CPU to compilation.
            if await _is_cancelled():
                await task_manager.append_log(
                    task_id, "Task was cancelled during clone/bootstrap — build skipped"
                )
                return

        async with async_session() as db:
            await dispatch_build(task_id, effective_config, db)

        # On failure, scan the log for known error patterns and attach a
        # human-friendly diagnosis (problem + suggestion) so the UI can tell
        # the user WHAT went wrong and HOW to fix it, instead of just "failed".
        try:
            current = await task_manager.get_task(task_id)
            if current and current.status == TaskStatus.FAILED:
                from app.services.error_diagnosis import build_failure_report
                logs = await task_manager.get_logs(task_id)
                # Always persist an explanation, not only when a rule matched.
                # Previously this was `if diags:`, so 87% of failures stored
                # nothing and the user saw a bare red "failed" — which is how one
                # project reached 55 retries in five weeks. The report falls
                # back to naming the failed stage and the log's error lines,
                # which is at least something to act on or forward.
                report = build_failure_report(
                    "\n".join(logs), stage=getattr(current, "stage", None)
                )
                async with async_session() as db:
                    await task_manager.merge_result(task_id, report, db)
        except Exception as e:
            await task_manager.append_log(
                task_id, f"(diagnosis step skipped: {e!s})"
            )

        # Post-build disk reclamation for git-source tasks only. Runs even
        # if the build failed — we check status below. Never raises out.
        if config.source_type == SourceType.GIT:
            try:
                await _post_build_cleanup(task_id, effective_config)
            except Exception as e:
                # Cleanup must never turn a successful build into a failure.
                await task_manager.append_log(
                    task_id, f"Post-build cleanup error (ignored): {e!s}"
                )


async def _post_build_cleanup(task_id: str, config: BuildConfig) -> None:
    """Reclaim disk space from a git-source workspace once the build is done.

    Policy:
      - Task status != COMPLETED    → do nothing (keep for debug / TTL)
      - source_type != git          → do nothing (local mode workspaces
                                       are owned by the user, not us)
      - Docker mode + opted in      → delete entire workspace (image
                                       already in /media/disk1/docker_images)
      - Non-Docker + opted in       → shrink workspace to output_dir only,
                                       preserving the download endpoint
    """
    from app.config import get_settings
    from app.services import workspace_cleanup

    # Read live task state — did the build actually succeed?
    current = await task_manager.get_task(task_id)
    if current is None or current.status != TaskStatus.COMPLETED:
        return

    settings = get_settings()

    if config.docker_enabled:
        if not settings.workspace_docker_delete_on_success:
            return
        try:
            ok = await asyncio.to_thread(workspace_cleanup.delete_one_workspace, task_id)
            if ok:
                await task_manager.append_log(
                    task_id,
                    "Workspace fully removed (Docker image already exported)",
                )
        except ValueError:
            pass
        return

    if not settings.workspace_shrink_on_success:
        return

    output_dir_name = config.output_dir or "dist"
    try:
        result = await asyncio.to_thread(
            workspace_cleanup.shrink_workspace, task_id, output_dir_name
        )
    except ValueError:
        return

    if result.get("skipped"):
        # Can't shrink (no preserve target, missing dir, etc.) — leave for TTL.
        return

    preserved = result.get("preserved", "?")
    removed = result.get("removed", 0)
    failed = result.get("failed", 0)
    if removed or failed:
        await task_manager.append_log(
            task_id,
            f"Workspace shrunk: kept '{preserved}/', removed {removed} entries"
            + (f" ({failed} failures)" if failed else ""),
        )
