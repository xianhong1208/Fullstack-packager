"""FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.auth import router as auth_router
from app.api.routes.tasks import router as tasks_router
from app.api.routes.users import router as users_router
from app.api.routes.git_credentials import router as git_credentials_router
from app.api.routes.monitoring import router as monitoring_router
from app.api.websocket import router as websocket_router
from app.config import get_settings
from app.database import close_db, init_db
from app.middleware.rate_limiter import RateLimitMiddleware, RateLimitConfig
from app.services import workspace_cleanup

settings = get_settings()


def _configure_logging() -> None:
    """Give the application's loggers somewhere to go.

    Nothing did this before. uvicorn configures only its own "uvicorn.*"
    loggers, so the root logger had no handler and sat at WARNING: every
    logger.info() in the codebase was discarded, and warnings surfaced through
    Python's lastResort handler as bare text with no timestamp or module name.
    Anything logged about a background sweep or a failed cleanup was therefore
    invisible in practice.

    Uses force=True because uvicorn may already have touched the root logger;
    without it a pre-existing handler would leave the format untouched.
    """
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
    # These two are noisy at DEBUG and say nothing useful about our own code.
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger(__name__)

# Frontend build directory
FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Startup
    await init_db()
    # Seed RBAC (permissions + roles) so a fresh DB is usable immediately: the
    # first user to register is made admin and must have a role that exists.
    from app.database import async_session as _seed_session
    from app.services.seed import seed_rbac
    async with _seed_session() as _sdb:
        await seed_rbac(_sdb)
    # Reconcile orphans: tasks left RUNNING/PENDING by a previous crash/restart
    # have no live process anymore — mark them FAILED so they don't spin forever.
    from app.database import async_session
    from app.services.task_manager import task_manager
    async with async_session() as _db:
        _n = await task_manager.fail_orphaned_tasks(_db)
        if _n:
            logger.info("Reconciled %d orphaned task(s) left by a previous run → failed", _n)
    # Background task: periodic workspace cleanup (git mode only).
    # Safe to start unconditionally — the loop itself honors
    # settings.workspace_cleanup_enabled and no-ops if disabled.
    cleanup_task = asyncio.create_task(
        workspace_cleanup.cleanup_loop(),
        name="workspace_cleanup_loop",
    )
    try:
        yield
    finally:
        # Shutdown
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass  # expected: we just cancelled it
        except Exception:
            # A crash inside the sweep loop surfaces only here. Swallowing it
            # silently means workspaces quietly stop being reclaimed and the
            # disk fills weeks later with nothing to point at the cause.
            logger.exception("Workspace cleanup loop exited with an error")
        await close_db()


app = FastAPI(
    title="Nuitka Docker API",
    description="REST API for Nuitka compilation tasks with real-time WebSocket updates",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration (for development)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting configuration
rate_limit_config = RateLimitConfig(
    requests_per_minute=60,
    requests_per_hour=1000,
    path_limits={
        "/auth/login": 10,       # Stricter limit for login
        "/auth/register": 5,    # Stricter limit for registration
        "/auth/forgot-password": 5,
        # The account-recovery chain is forgot-password -> verify-security-answer
        # -> reset-password, so this endpoint's answer is what stands between an
        # attacker and a password change. Without its own key it fell through to
        # the global 60/min — six times looser than the login it bypasses, for a
        # secret with far less entropy than a password (a surname, a school
        # name). It also records nothing: only /auth/login writes to
        # login_history, so guessing here leaves no trace for an admin to find.
        "/auth/verify-security-answer": 5,
        "/auth/reset-password": 5,
        # Submitting a build costs minutes of saturated CPU. The /api/tasks
        # prefix is excluded below so the dashboard can poll status freely —
        # this method-qualified key claws back the one verb that must not be
        # free, and takes precedence over that exclusion.
        "POST /api/tasks": settings.build_submit_per_minute,
    },
    excluded_paths={"/health", "/docs", "/openapi.json", "/redoc", "/ws"},
    excluded_prefixes={"/api/tasks", "/api/monitoring"},
    # Empty unless TRUSTED_PROXY_IPS is set — X-Forwarded-For is ignored by
    # default so a directly-exposed service cannot be spoofed past the limiter.
    trusted_proxies=settings.trusted_proxies,
)
app.add_middleware(RateLimitMiddleware, config=rate_limit_config)

# Include API routers
app.include_router(auth_router)
app.include_router(tasks_router)
app.include_router(users_router)
app.include_router(git_credentials_router)
app.include_router(monitoring_router)
app.include_router(websocket_router)


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy"}


# Serve frontend static files if built
if FRONTEND_DIR.exists():
    # Mount static assets (js, css, images)
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIR / "assets"),
        name="assets",
    )

    @app.get("/")
    async def serve_spa_root() -> FileResponse:
        """Serve the SPA index.html for root."""
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str) -> FileResponse:
        """Serve SPA - return index.html for client-side routing."""
        # Containment check: resolve the joined path and require it to stay
        # inside FRONTEND_DIR, otherwise ".."-segments could read arbitrary
        # files (this catch-all bypasses StaticFiles' built-in guard).
        frontend_root = FRONTEND_DIR.resolve()
        try:
            file_path = (FRONTEND_DIR / full_path).resolve()
        except (OSError, ValueError):
            return FileResponse(FRONTEND_DIR / "index.html")
        if (
            file_path.is_relative_to(frontend_root)
            and file_path.exists()
            and file_path.is_file()
        ):
            return FileResponse(file_path)
        # Return index.html for SPA routing
        return FileResponse(FRONTEND_DIR / "index.html")

else:
    @app.get("/")
    async def root() -> dict:
        """Root endpoint when frontend is not built."""
        return {
            "name": "Nuitka Docker API",
            "version": "1.0.0",
            "docs": "/docs",
            "message": "Frontend not built. Run 'cd frontend && npm run build' first.",
            "endpoints": {
                "tasks": "/api/tasks",
                "history": "/api/history",
                "websocket": "/ws/tasks",
            },
        }


# NOTE: there is deliberately no `main()` / uvicorn.run() here.
#
# There used to be, and it silently reopened a hole that the rest of this
# codebase works hard to close. uvicorn defaults to proxy_headers=True with
# forwarded_allow_ips="127.0.0.1", so its ProxyHeadersMiddleware rewrites
# scope["client"] from X-Forwarded-For before any application code runs —
# exactly what app/middleware/client_ip.py states is disabled. Anything started
# through this function got that rewriting back, and the test that pins the
# setting only inspects the root main.py's UVICORN_KWARGS, so nothing would
# have caught it.
#
# The server has one entry point: the repository-root main.py. Adding a second
# one here means adding a second place for the launch flags to drift.
