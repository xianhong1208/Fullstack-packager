"""Entry point for running the server directly with: uv run python main.py"""

import sys
from pathlib import Path

import uvicorn
from app.config import get_settings

settings = get_settings()


def run_migrations() -> None:
    """Bring the database schema up to date (alembic upgrade head) on startup.

    Runs here, before the server starts, so `uv run python main.py` just works on a
    fresh database — no separate `alembic upgrade head` step. Alembic's env.py reads
    the connection from the same settings, so nothing extra is configured here.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent / "alembic.ini"))
    command.upgrade(cfg, "head")

LOGO = r"""
[36m
  ____        _ _     _    ____           _
 | __ ) _   _(_) | __| |  / ___|___ _ __ | |_ ___ _ __
 |  _ \| | | | | |/ _` | | |   / _ \ '_ \| __/ _ \ '__|
 | |_) | |_| | | | (_| | | |__|  __/ | | | ||  __/ |
 |____/ \__,_|_|_|\__,_|  \____\___|_| |_|\__\___|_|
[0m
[90m  Build Center Service v1.0.0[0m
"""

# Uvicorn's own ProxyHeadersMiddleware is ON by default and trusts 127.0.0.1,
# which means it REWRITES scope["client"] from X-Forwarded-For before any of our
# code runs. That silently defeated the rate limiter's own trust check: the
# limiter was handed an already-spoofed request.client and had no way to tell.
#
# Disabling it makes request.client always the real peer, so
# app/middleware/client_ip.py is the single place deciding whether a forwarded
# header may override it — configured via TRUSTED_PROXY_IPS. Put the proxy's
# address there when running behind nginx.
UVICORN_KWARGS: dict = {
    "host": settings.host,
    "port": settings.port,
    "reload": settings.debug,
    "proxy_headers": False,
}


if __name__ == "__main__":
    print(LOGO.replace("[36m", "\033[36m").replace("[0m", "\033[0m").replace("[90m", "\033[90m"))

    try:
        print("\033[90m  Applying database migrations...\033[0m")
        run_migrations()
    except Exception as exc:  # noqa: BLE001 - surface any DB/migration error clearly and stop
        print(f"\033[31m  Migration failed: {exc}\033[0m")
        print("\033[31m  Check the database connection (DB_* in .env) and try again.\033[0m")
        sys.exit(1)

    print(f"\033[32m  Starting server at http://{settings.host}:{settings.port}\033[0m")
    print()

    uvicorn.run("app.main:app", **UVICORN_KWARGS)
