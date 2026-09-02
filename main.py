"""Entry point for running the server directly with: uv run python main.py"""

import uvicorn
from app.config import get_settings

settings = get_settings()

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
    print(f"\033[32m  Starting server at http://{settings.host}:{settings.port}\033[0m")
    print()

    uvicorn.run("app.main:app", **UVICORN_KWARGS)
