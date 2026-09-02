"""Application configuration using Pydantic Settings."""

import os
import shutil
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database. Empty (the default) uses a local SQLite file — zero setup. Set
    # DATABASE_URL to a PostgreSQL DSN (postgresql+asyncpg://user:pass@host/db)
    # to use Postgres instead. The DB_* fields build that DSN when DATABASE_URL
    # is left empty but DB_HOST is pointed at a real server.
    database_url: str = ""
    db_host: str = ""
    db_port: int = 5432
    db_name: str = "build_center"
    db_user: str = "postgres"
    db_password: str = "admin"

    # Server
    host: str = "0.0.0.0"
    port: int = 5018
    debug: bool = False
    # CSV of browser origins allowed to call the API (the frontend dev server and
    # any deployed console URL). Defaults to the common Vite / CRA dev ports.
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000"

    # Task settings
    max_log_lines: int = 3000
    # Minutes to keep finished (completed/failed/cancelled) tasks in the
    # in-memory task map before eviction. History stays in the DB forever;
    # this only bounds RAM growth of the live-task dict (incl. log buffers).
    finished_task_retention_minutes: int = 60

    # Build concurrency — how many Nuitka builds can run simultaneously
    max_concurrent_builds: int = 3

    # JWT settings (optional - if not set, a random key is generated on startup)
    jwt_secret_key: str | None = None
    # Fernet key used to encrypt stored secrets (Git credentials) at rest. If not
    # set, one is generated on first use and appended to .env, like the JWT secret.
    settings_encryption_key: str | None = None

    # === MCP Center single sign-on (optional) ===
    # When enabled, the login page offers "Sign in with MCP Center". Build Center
    # runs the OAuth 2.1 authorization-code + PKCE flow against MCP Center, then
    # issues its own session — local email/password sign-in stays available.
    mcp_oauth_enabled: bool = False
    mcp_center_url: str = "http://localhost:4568"  # MCP Center issuer (OAUTH_ISSUER)
    mcp_oauth_client_id: str = ""
    mcp_oauth_client_secret: str = ""  # blank for a public client (PKCE only)
    # Where MCP Center sends the browser back — must be registered on the client.
    mcp_oauth_redirect_uri: str = "http://localhost:5018/auth/oauth/mcp/callback"
    mcp_oauth_scopes: str = "openid"

    # Application log level. Nothing configures the root logger by default —
    # uvicorn only sets up its own "uvicorn.*" loggers — so app logger.info()
    # calls were being discarded entirely and warnings arrived through Python's
    # lastResort handler with no timestamp or module name.
    log_level: str = "INFO"

    # === Rate limiting ===
    # CSV of reverse-proxy IPs whose X-Forwarded-For header may be believed.
    # EMPTY (the default) means XFF is never trusted and the limiter keys on
    # the real peer address — the safe choice for a directly-exposed service.
    # Only populate this when a proxy in front actually rewrites/appends XFF;
    # trusting it otherwise lets any client forge a new identity per request
    # and walk straight through the login limiter.
    trusted_proxy_ips: str = ""
    # Per-minute cap on build submissions. A build costs minutes of saturated
    # CPU, so this is the expensive endpoint to protect — MAX_CONCURRENT_BUILDS
    # bounds what runs at once but not how deep the queue grows.
    build_submit_per_minute: int = 20

    # === Git source mode ===
    # Legacy single-token fallback. Git credentials are now managed in the console
    # (see app/services/git_credentials.py) and stored encrypted; this is only used
    # when no matching credential exists, and may be left unset.
    gitlab_token: str | None = None
    # Where clones and build workspaces live. Override with GIT_WORKSPACE_DIR.
    git_workspace_dir: str = "./data/workspace"
    git_allowed_hosts: str = ""  # CSV; empty = allow any http/https host
    git_clone_timeout: int = 600  # seconds
    # CSV of absolute path prefixes a local-source build may read from. EMPTY (the
    # default) means local-source mode is disabled and only Git-URL sources are
    # allowed — set LOCAL_SOURCE_ROOTS to enable it for trusted directories.
    local_source_roots: str = ""

    # === Auto venv bootstrap (git mode only) ===
    # After clone, run `uv sync --frozen` (or fallback) in the clone dir
    # to create the .venv that nuitka_worker relies on.
    git_auto_venv: bool = True
    # Path to the uv binary. Empty (the default) auto-detects it on PATH; set
    # UV_BIN only when uv lives somewhere the service PATH does not include.
    uv_bin: str = ""
    # PATH used when running pre-build / venv-bootstrap subprocesses. Empty (the
    # default) inherits the service's own PATH; override only to pin one.
    pre_build_path: str = ""
    # Max seconds for the auto-venv bootstrap step.
    venv_bootstrap_timeout: int = 900
    # uv cache dir used ONLY by the bootstrap subprocess. Lives on the
    # same filesystem as git_workspace_dir so uv can hardlink instead of
    # copying package bytes. Leave empty to use the default user cache.
    bootstrap_uv_cache_dir: str = ""

    # === Workspace cleanup (git mode only) ===
    # Periodic background sweep of git_workspace_dir that deletes per-task
    # clones according to task status + age. Never touches running/pending.
    workspace_cleanup_enabled: bool = True
    # Days to keep successful task workspaces before deleting. Note: for
    # non-Docker tasks, downloading the output depends on the workspace
    # still existing — so this doubles as "how long output is downloadable".
    workspace_success_ttl_days: int = 7
    # Days to keep failed / cancelled task workspaces (kept longer so users
    # have time to debug).
    workspace_failed_ttl_days: int = 30
    # Days to keep "orphan" workspaces (dirs under git_workspace_dir that
    # have no matching row in the history table — usually crashed clones).
    workspace_orphan_ttl_days: int = 2
    # How often the background sweep runs, in hours.
    workspace_cleanup_interval_hours: int = 6
    # On successful NON-Docker build: delete everything in the workspace
    # except the output_dir. Lets the download endpoint keep working while
    # reclaiming source / .venv / .git bytes (typically 90% of workspace).
    workspace_shrink_on_success: bool = True
    # On successful DOCKER build: fully delete the workspace — the image
    # tar.gz already lives in docker_images_dir so nothing else
    # is needed from the workspace.
    workspace_docker_delete_on_success: bool = True

    # === Refresh token retention ===
    # Token rotation mints a new row on every refresh (~1 per 15 min per open
    # tab) and revokes the old one, so this table only ever grows without a
    # sweep — it reached 21k rows for 7 users, 91% already expired. Rows are
    # deleted once they have been unusable for this many days; the window keeps
    # recently ended sessions visible in the active-session view.
    refresh_token_retention_days: int = 7

    # === Docker image exports ===
    # Threads for compressing the exported image, when pigz is installed.
    # `docker save | gzip` is single-threaded, so a multi-GB image spends
    # minutes on one core of a 128-core host.
    #
    # 32 is chosen from this deployment's actual artifact sizes — 162 recorded
    # builds averaging 2 GB, the largest 16 GB — not from a small sample.
    # Measured throughput: gzip 32 MB/s, pigz -p 8 236 MB/s, pigz -p 32
    # 577 MB/s. At real scale that is:
    #
    #     16 GB image:  gzip 8.4 min  |  -p 8 1.2 min  |  -p 32 28 s
    #
    # so 8 -> 32 threads saves 26-41 s on the large builds, not the ~1 s a
    # 572 MB test suggested. Still capped rather than "all 128 cores": the
    # host runs up to MAX_CONCURRENT_BUILDS Nuitka compiles, disk write
    # throughput becomes the limit well before 128 threads, and the export is
    # a burst at the end of a build rather than a sustained load.
    #
    # Ignored entirely when pigz is absent — the export falls back to gzip
    # rather than failing an image that is already built.
    docker_export_compress_threads: int = 32

    # Where exported Docker images (.tar.gz) are written by docker_worker.
    docker_images_dir: str = "./data/docker_images"
    # Periodically delete exported images older than the TTL (by mtime).
    # Runs on the same background loop as the workspace sweep.
    docker_images_cleanup_enabled: bool = True
    docker_images_ttl_days: int = 30

    @property
    def uv_binary(self) -> str:
        """Absolute path to uv: the configured value, else the one found on PATH."""
        return self.uv_bin or shutil.which("uv") or "uv"

    @property
    def subprocess_path(self) -> str:
        """PATH for build subprocesses: the configured value, else the current PATH."""
        return self.pre_build_path or os.environ.get("PATH", "")

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse the CSV CORS allowlist into a list of origins."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def local_source_root_list(self) -> list[str]:
        """Parse the CSV local-source allowlist into normalised absolute prefixes."""
        roots = []
        for r in self.local_source_roots.split(","):
            r = r.strip()
            if r:
                roots.append(str(Path(r).resolve()).rstrip("/") + "/")
        return roots

    @property
    def allowed_git_hosts(self) -> list[str]:
        """Parse the CSV allowlist into a list of hostnames (lowercased)."""
        return [h.strip().lower() for h in self.git_allowed_hosts.split(",") if h.strip()]

    @property
    def trusted_proxies(self) -> set[str]:
        """Parse the CSV trusted-proxy list into a set of IP strings."""
        return {p.strip() for p in self.trusted_proxy_ips.split(",") if p.strip()}

    def _resolve_url(self, *, async_driver: bool) -> str:
        """Resolve the database URL. Precedence: DATABASE_URL, then DB_* (Postgres),
        then a local SQLite file. `async_driver` selects the async vs sync driver."""
        if self.database_url:
            url = self.database_url
            # Normalise the driver to match the requested (a)sync mode.
            if url.startswith("postgresql"):
                base = url.split("://", 1)[1]
                return f"postgresql+asyncpg://{base}" if async_driver else f"postgresql://{base.replace('+asyncpg', '')}"
            if url.startswith("sqlite"):
                base = url.split("://", 1)[1]
                return f"sqlite+aiosqlite://{base}" if async_driver else f"sqlite://{base}"
            return url
        if self.db_host:
            driver = "postgresql+asyncpg" if async_driver else "postgresql"
            return f"{driver}://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"
        # Default: a local SQLite file (created on first use).
        db_path = Path("./data/build_center.db").resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        driver = "sqlite+aiosqlite" if async_driver else "sqlite"
        return f"{driver}:///{db_path}"

    @property
    def async_database_url(self) -> str:
        """Async SQLAlchemy URL (used by the app engine and alembic)."""
        return self._resolve_url(async_driver=True)

    @property
    def sync_database_url(self) -> str:
        """Sync SQLAlchemy URL (for tooling that needs a sync driver)."""
        return self._resolve_url(async_driver=False)

    @property
    def is_sqlite(self) -> bool:
        return self.async_database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
