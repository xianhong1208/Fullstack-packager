"""Single source of truth for "which address is this request really from".

Two layers used to answer this question independently and disagree:

  1. uvicorn's own ProxyHeadersMiddleware (on by default, trusting 127.0.0.1)
     rewrites ``scope["client"]`` from X-Forwarded-For before any application
     code runs;
  2. the rate limiter and the auth routes each read the header themselves.

That split is how a spoofable value reached the rate limiter even after the
limiter was hardened: it was handed an already-rewritten ``request.client``.
uvicorn's layer is now disabled (see main.py) so ``request.client`` is always
the real peer, and this module is the only place that decides whether the
forwarded header may override it.
"""

from starlette.requests import Request
from starlette.websockets import WebSocket


def resolve_client_ip(
    request: Request | WebSocket,
    trusted_proxies: set[str],
) -> str:
    """Return the address this request should be attributed to.

    X-Forwarded-For is written by the client, so it is honoured ONLY when the
    actual peer is a configured reverse proxy. Otherwise the peer address is
    used and the header ignored — that is what stops anyone from minting a
    fresh identity per request to walk through the login limiter.

    When the header IS trusted we take the RIGHTMOST entry, not the first.
    nginx's ``$proxy_add_x_forwarded_for`` APPENDS the observed address to
    whatever the client sent, so a client sending "X-Forwarded-For: 1.2.3.4"
    produces "1.2.3.4, <real>" — the leftmost value is attacker-written and the
    rightmost is the one our own proxy vouched for. With an overwriting proxy
    there is only one entry, so this is also correct.
    """
    peer = request.client.host if request.client else "unknown"

    if not trusted_proxies or peer not in trusted_proxies:
        return peer

    forwarded = request.headers.get("X-Forwarded-For")
    if not forwarded:
        return peer
    candidates = [part.strip() for part in forwarded.split(",") if part.strip()]
    return candidates[-1] if candidates else peer
