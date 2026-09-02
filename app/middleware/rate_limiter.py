"""Rate limiting middleware for API protection."""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.middleware.client_ip import resolve_client_ip


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    # Default limits
    requests_per_minute: int = 60
    requests_per_hour: int = 1000

    # Path-specific limits (requests per minute).
    # A key is either a bare path ("/auth/login", also matched as a prefix) or
    # a method-qualified exact path ("POST /api/tasks"). Method-qualified keys
    # let one verb be limited without touching the others — the task list is
    # polled constantly by the dashboard, while task CREATION is expensive.
    path_limits: dict[str, int] = field(default_factory=dict)

    # Reverse-proxy addresses whose X-Forwarded-For header may be believed.
    # Empty (default) = never trust XFF; key on the real peer address.
    trusted_proxies: set[str] = field(default_factory=set)

    # Paths to exclude from rate limiting (exact match)
    excluded_paths: set[str] = field(default_factory=lambda: {"/health", "/docs", "/openapi.json"})

    # Path prefixes to exclude from rate limiting
    excluded_prefixes: set[str] = field(default_factory=set)

    # The surfaces the limiter applies to at all. Anything outside them is the
    # SPA shell or a static file served from disk, and counting those was
    # actively harmful: one page load pulls the bundle, the stylesheet and
    # several icons, so a browser could spend most of a minute's allowance
    # before issuing a single API call — and then /box.svg came back 429,
    # which is not a sentence anyone can act on.
    #
    # An allow-list rather than more excluded_prefixes because the set of
    # static files is open-ended: every icon added later would have to be
    # remembered here, and forgetting one costs a user their session rather
    # than raising an error anyone would notice.
    limited_prefixes: set[str] = field(default_factory=lambda: {"/api", "/auth"})

    # Whether to include rate limit headers in response
    include_headers: bool = True


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for rate limiting requests by IP address.

    Uses a fixed window algorithm with per-minute and per-hour limits.
    Supports path-specific rate limits for sensitive endpoints.
    """

    def __init__(self, app, config: RateLimitConfig | None = None):
        super().__init__(app)
        self.config = config or RateLimitConfig()

        # Storage for request counts: {ip: {window_key: count}}
        self._minute_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self._hour_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))

        # Path-specific counts: {(ip, path): {window_key: count}}
        self._path_counts: dict[tuple[str, str], dict[int, int]] = defaultdict(lambda: defaultdict(int))

        # Last cleanup time
        self._last_cleanup = time.time()

    def _get_client_ip(self, request: Request) -> str:
        """Return the address to key rate limits on.

        Delegates to the shared resolver so the limiter, the audit log and the
        login history can never disagree about who a request came from.
        """
        return resolve_client_ip(request, self.config.trusted_proxies)

    def _get_path_key(self, method: str, path: str) -> str | None:
        """Get the matching path_limits key for this request, if any.

        Resolution order — most specific first:
          1. method-qualified exact path  ("POST /api/tasks")
          2. bare exact path              ("/auth/login")
          3. bare prefix                  ("/auth" matches "/auth/login")

        Method-qualified keys are exact-match only; they exist to single out
        one expensive verb on a path whose other verbs must stay unthrottled.
        """
        method_key = f"{method} {path}"
        if method_key in self.config.path_limits:
            return method_key

        if path in self.config.path_limits:
            return path

        for configured_path in self.config.path_limits:
            if " " in configured_path:
                continue  # method-qualified keys never prefix-match
            if path.startswith(configured_path):
                return configured_path

        return None

    def _cleanup_old_entries(self, current_time: float) -> None:
        """Periodically clean up old entries to prevent memory growth."""
        # Run cleanup every 5 minutes
        if current_time - self._last_cleanup < 300:
            return

        self._last_cleanup = current_time
        current_minute = int(current_time // 60)
        current_hour = int(current_time // 3600)

        # Clean minute counts (keep last 2 minutes)
        for ip in list(self._minute_counts.keys()):
            old_windows = [w for w in self._minute_counts[ip] if w < current_minute - 1]
            for w in old_windows:
                del self._minute_counts[ip][w]
            if not self._minute_counts[ip]:
                del self._minute_counts[ip]

        # Clean hour counts (keep last 2 hours)
        for ip in list(self._hour_counts.keys()):
            old_windows = [w for w in self._hour_counts[ip] if w < current_hour - 1]
            for w in old_windows:
                del self._hour_counts[ip][w]
            if not self._hour_counts[ip]:
                del self._hour_counts[ip]

        # Clean path counts
        for key in list(self._path_counts.keys()):
            old_windows = [w for w in self._path_counts[key] if w < current_minute - 1]
            for w in old_windows:
                del self._path_counts[key][w]
            if not self._path_counts[key]:
                del self._path_counts[key]

    def _check_rate_limit(
        self, client_ip: str, path_key: str | None, current_time: float
    ) -> tuple[bool, int, int, int]:
        """Check if request is within rate limits.

        ``path_key`` is the already-resolved key from _get_path_key(); None
        means no path-specific limit applies and the global limits are used.

        Returns:
            Tuple of (allowed, limit, remaining, retry_after)
        """
        current_minute = int(current_time // 60)
        current_hour = int(current_time // 3600)

        # Check path-specific limit first
        if path_key:
            path_limit = self.config.path_limits[path_key]
            cache_key = (client_ip, path_key)
            path_count = self._path_counts[cache_key][current_minute]

            if path_count >= path_limit:
                retry_after = 60 - int(current_time % 60)
                return False, path_limit, 0, retry_after

            self._path_counts[cache_key][current_minute] += 1
            remaining = path_limit - path_count - 1
            return True, path_limit, remaining, 0

        # Check per-minute limit
        minute_count = self._minute_counts[client_ip][current_minute]
        if minute_count >= self.config.requests_per_minute:
            retry_after = 60 - int(current_time % 60)
            return False, self.config.requests_per_minute, 0, retry_after

        # Check per-hour limit
        hour_count = self._hour_counts[client_ip][current_hour]
        if hour_count >= self.config.requests_per_hour:
            retry_after = 3600 - int(current_time % 3600)
            return False, self.config.requests_per_hour, 0, retry_after

        # Increment counters
        self._minute_counts[client_ip][current_minute] += 1
        self._hour_counts[client_ip][current_hour] += 1

        remaining = self.config.requests_per_minute - minute_count - 1
        return True, self.config.requests_per_minute, remaining, 0

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with rate limiting."""
        path = request.url.path

        # Resolve the path-specific limit BEFORE consulting the exclusions: an
        # explicitly configured limit always wins. Without this ordering,
        # excluding "/api/tasks" (so the dashboard can poll freely) would also
        # exempt "POST /api/tasks", which is the expensive one.
        path_key = self._get_path_key(request.method, path)

        if path_key is None:
            # Anything that is not an API surface — the SPA shell, its bundle,
            # icons — is not subject to the limiter at all.
            # Anything that is not an API surface — the SPA shell, its bundle,
            # icons — is not subject to the limiter at all.
            if self.config.limited_prefixes and not any(
                path.startswith(prefix) for prefix in self.config.limited_prefixes
            ):
                return await call_next(request)
            # Skip excluded paths (exact match and prefix match)
            if path in self.config.excluded_paths:
                return await call_next(request)
            if any(path.startswith(prefix) for prefix in self.config.excluded_prefixes):
                return await call_next(request)

        current_time = time.time()
        client_ip = self._get_client_ip(request)

        # Periodic cleanup
        self._cleanup_old_entries(current_time)

        # Check rate limit
        allowed, limit, remaining, retry_after = self._check_rate_limit(
            client_ip, path_key, current_time
        )

        if not allowed:
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests",
                    "retry_after": retry_after,
                },
            )
            if self.config.include_headers:
                response.headers["X-RateLimit-Limit"] = str(limit)
                response.headers["X-RateLimit-Remaining"] = "0"
                response.headers["X-RateLimit-Reset"] = str(int(current_time) + retry_after)
                response.headers["Retry-After"] = str(retry_after)
            return response

        # Process request
        response = await call_next(request)

        # Add rate limit headers
        if self.config.include_headers:
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response
