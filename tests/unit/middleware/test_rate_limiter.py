"""
Unit tests for the rate limiting middleware.
Source: app/middleware/rate_limiter.py

Two properties matter here and both are easy to regress:
  1. the limiter must key on an address the client cannot choose
  2. an explicitly configured path limit must survive the exclusion list
"""

import pytest
from starlette.datastructures import Headers
from starlette.responses import Response

from app.middleware.rate_limiter import RateLimitConfig, RateLimitMiddleware

TRUSTED_PROXY = "10.0.0.1"
REAL_CLIENT = "203.0.113.9"
SPOOFED = "1.2.3.4"


def _make_request(mocker, *, peer: str | None = REAL_CLIENT, xff: str | None = None,
                  method: str = "GET", path: str = "/"):
    """Build a request double carrying only what the middleware reads.

    Uses a real starlette Headers object so header lookup stays
    case-insensitive exactly as it is in production.
    """
    headers = Headers({"x-forwarded-for": xff} if xff is not None else {})
    request = mocker.MagicMock()
    request.headers = headers
    request.method = method
    request.url.path = path
    request.client = mocker.MagicMock(host=peer) if peer is not None else None
    return request


def _middleware(**config_kwargs) -> RateLimitMiddleware:
    return RateLimitMiddleware(app=None, config=RateLimitConfig(**config_kwargs))


class TestGetClientIp:
    """Tests for _get_client_ip — the limiter's identity source.

    If a client can influence this value, every per-IP limit becomes advisory.
    """

    @pytest.mark.unit
    def test_tc_rl_001_xff_ignored_when_no_trusted_proxies(self, mocker):
        """TC-RL-001: _get_client_ip — with no trusted proxies configured, XFF is ignored."""
        mw = _middleware()
        request = _make_request(mocker, peer=REAL_CLIENT, xff=SPOOFED)

        assert mw._get_client_ip(request) == REAL_CLIENT

    @pytest.mark.unit
    def test_tc_rl_002_xff_ignored_from_untrusted_peer(self, mocker):
        """TC-RL-002: _get_client_ip — XFF from a peer outside the trust list is ignored."""
        mw = _middleware(trusted_proxies={TRUSTED_PROXY})
        request = _make_request(mocker, peer="198.51.100.7", xff=SPOOFED)

        assert mw._get_client_ip(request) == "198.51.100.7"

    @pytest.mark.unit
    def test_tc_rl_003_xff_honoured_from_trusted_proxy(self, mocker):
        """TC-RL-003: _get_client_ip — a trusted proxy's XFF is used."""
        mw = _middleware(trusted_proxies={TRUSTED_PROXY})
        request = _make_request(mocker, peer=TRUSTED_PROXY, xff=REAL_CLIENT)

        assert mw._get_client_ip(request) == REAL_CLIENT

    @pytest.mark.unit
    def test_tc_rl_004_rightmost_entry_wins_over_client_injected_prefix(self, mocker):
        """TC-RL-004: _get_client_ip — client-injected XFF prefix cannot displace the proxy's entry.

        nginx APPENDS the observed address, so "spoof, real" is what arrives when
        the client pre-seeds the header. Taking the leftmost value would hand the
        attacker a fresh identity per request even behind a trusted proxy.
        """
        mw = _middleware(trusted_proxies={TRUSTED_PROXY})
        request = _make_request(mocker, peer=TRUSTED_PROXY, xff=f"{SPOOFED}, {REAL_CLIENT}")

        assert mw._get_client_ip(request) == REAL_CLIENT

    @pytest.mark.unit
    @pytest.mark.parametrize("xff", [None, "", "   ", ",", " , "])
    def test_tc_rl_005_falls_back_to_peer_when_xff_unusable(self, mocker, xff):
        """TC-RL-005: _get_client_ip — missing or empty XFF falls back to the peer address."""
        mw = _middleware(trusted_proxies={TRUSTED_PROXY})
        request = _make_request(mocker, peer=TRUSTED_PROXY, xff=xff)

        assert mw._get_client_ip(request) == TRUSTED_PROXY

    @pytest.mark.unit
    def test_tc_rl_006_missing_client_yields_unknown(self, mocker):
        """TC-RL-006: _get_client_ip — a request with no client info keys on 'unknown'."""
        mw = _middleware()
        request = _make_request(mocker, peer=None)

        assert mw._get_client_ip(request) == "unknown"


class TestGetPathKey:
    """Tests for _get_path_key — resolves which configured limit applies."""

    @pytest.mark.unit
    def test_tc_rl_010_method_qualified_exact_match(self):
        """TC-RL-010: _get_path_key — 'POST /api/tasks' matches a POST to that path."""
        mw = _middleware(path_limits={"POST /api/tasks": 20})
        assert mw._get_path_key("POST", "/api/tasks") == "POST /api/tasks"

    @pytest.mark.unit
    def test_tc_rl_011_method_qualified_key_ignores_other_verbs(self):
        """TC-RL-011: _get_path_key — GET on the same path is NOT limited by the POST key."""
        mw = _middleware(path_limits={"POST /api/tasks": 20})
        assert mw._get_path_key("GET", "/api/tasks") is None

    @pytest.mark.unit
    def test_tc_rl_012_bare_path_exact_match(self):
        """TC-RL-012: _get_path_key — bare path key matches any method."""
        mw = _middleware(path_limits={"/auth/login": 10})
        assert mw._get_path_key("POST", "/auth/login") == "/auth/login"
        assert mw._get_path_key("GET", "/auth/login") == "/auth/login"

    @pytest.mark.unit
    def test_tc_rl_013_bare_path_prefix_match(self):
        """TC-RL-013: _get_path_key — bare key also matches as a prefix."""
        mw = _middleware(path_limits={"/auth": 10})
        assert mw._get_path_key("POST", "/auth/login") == "/auth"

    @pytest.mark.unit
    def test_tc_rl_014_method_qualified_key_never_prefix_matches(self):
        """TC-RL-014: _get_path_key — method-qualified keys are exact-match only.

        Otherwise 'POST /api/tasks' would also swallow POST /api/tasks/{id}/…
        endpoints that were never meant to share the build-submission budget.
        """
        mw = _middleware(path_limits={"POST /api/tasks": 20})
        assert mw._get_path_key("POST", "/api/tasks/abc/cancel") is None

    @pytest.mark.unit
    def test_tc_rl_015_method_qualified_wins_over_bare_path(self):
        """TC-RL-015: _get_path_key — the more specific key is chosen."""
        mw = _middleware(path_limits={"POST /api/tasks": 20, "/api/tasks": 60})
        assert mw._get_path_key("POST", "/api/tasks") == "POST /api/tasks"

    @pytest.mark.unit
    def test_tc_rl_016_no_configured_limit_returns_none(self):
        """TC-RL-016: _get_path_key — unconfigured path yields None (global limits apply)."""
        mw = _middleware(path_limits={"/auth/login": 10})
        assert mw._get_path_key("GET", "/api/history") is None


class TestCheckRateLimit:
    """Tests for _check_rate_limit — fixed-window counting."""

    @pytest.mark.unit
    def test_tc_rl_020_allows_and_decrements_remaining(self):
        """TC-RL-020: _check_rate_limit — successive requests report shrinking headroom."""
        mw = _middleware(path_limits={"/auth/login": 3})

        first = mw._check_rate_limit("1.1.1.1", "/auth/login", 1000.0)
        second = mw._check_rate_limit("1.1.1.1", "/auth/login", 1000.0)

        assert first[0] is True and first[2] == 2
        assert second[0] is True and second[2] == 1

    @pytest.mark.unit
    def test_tc_rl_021_blocks_once_path_limit_reached(self):
        """TC-RL-021: _check_rate_limit — the (limit+1)-th request in the window is refused."""
        mw = _middleware(path_limits={"/auth/login": 2})
        for _ in range(2):
            mw._check_rate_limit("1.1.1.1", "/auth/login", 1000.0)

        allowed, limit, remaining, retry_after = mw._check_rate_limit(
            "1.1.1.1", "/auth/login", 1000.0
        )

        assert allowed is False
        assert limit == 2
        assert remaining == 0
        assert 0 < retry_after <= 60

    @pytest.mark.unit
    def test_tc_rl_022_counts_are_per_ip(self):
        """TC-RL-022: _check_rate_limit — one IP exhausting its budget does not block another."""
        mw = _middleware(path_limits={"/auth/login": 1})
        mw._check_rate_limit("1.1.1.1", "/auth/login", 1000.0)

        assert mw._check_rate_limit("1.1.1.1", "/auth/login", 1000.0)[0] is False
        assert mw._check_rate_limit("2.2.2.2", "/auth/login", 1000.0)[0] is True

    @pytest.mark.unit
    def test_tc_rl_023_window_rollover_resets_budget(self):
        """TC-RL-023: _check_rate_limit — a new minute window starts fresh."""
        mw = _middleware(path_limits={"/auth/login": 1})
        mw._check_rate_limit("1.1.1.1", "/auth/login", 1000.0)

        assert mw._check_rate_limit("1.1.1.1", "/auth/login", 1000.0)[0] is False
        assert mw._check_rate_limit("1.1.1.1", "/auth/login", 1060.0)[0] is True

    @pytest.mark.unit
    def test_tc_rl_024_global_per_minute_limit_applies_without_path_key(self):
        """TC-RL-024: _check_rate_limit — path_key None falls through to the global limit."""
        mw = _middleware(requests_per_minute=2, requests_per_hour=1000)
        for _ in range(2):
            mw._check_rate_limit("1.1.1.1", None, 1000.0)

        allowed, limit, _, _ = mw._check_rate_limit("1.1.1.1", None, 1000.0)

        assert allowed is False
        assert limit == 2

    @pytest.mark.unit
    def test_tc_rl_025_global_per_hour_limit_applies(self):
        """TC-RL-025: _check_rate_limit — the hourly ceiling blocks even across minute windows."""
        mw = _middleware(requests_per_minute=100, requests_per_hour=3)
        for i in range(3):
            mw._check_rate_limit("1.1.1.1", None, 1000.0 + i * 60)

        allowed, limit, _, retry_after = mw._check_rate_limit("1.1.1.1", None, 1200.0)

        assert allowed is False
        assert limit == 3
        assert retry_after > 60


class TestDispatchExclusionOrdering:
    """Tests for dispatch — an explicit path limit must outrank the exclusion list.

    This is the ordering that makes 'exclude /api/tasks so the dashboard can
    poll, but still limit build submission' work. Reversing it silently
    un-limits the most expensive endpoint in the system.
    """

    @staticmethod
    async def _call_next(_request):
        return Response("ok")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_rl_030_excluded_prefix_skips_limiting(self, mocker):
        """TC-RL-030: dispatch — GET on an excluded prefix is never throttled."""
        mw = _middleware(
            requests_per_minute=1,
            excluded_prefixes={"/api/tasks"},
            path_limits={"POST /api/tasks": 2},
        )

        for _ in range(5):
            response = await mw.dispatch(
                _make_request(mocker, method="GET", path="/api/tasks"), self._call_next
            )
            assert response.status_code == 200

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_rl_031_path_limit_overrides_excluded_prefix(self, mocker):
        """TC-RL-031: dispatch — POST on the same excluded prefix IS throttled."""
        mw = _middleware(
            excluded_prefixes={"/api/tasks"},
            path_limits={"POST /api/tasks": 2},
        )

        statuses = []
        for _ in range(3):
            response = await mw.dispatch(
                _make_request(mocker, method="POST", path="/api/tasks"), self._call_next
            )
            statuses.append(response.status_code)

        assert statuses == [200, 200, 429]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_rl_032_spoofed_xff_cannot_reset_the_budget(self, mocker):
        """TC-RL-032: dispatch — rotating X-Forwarded-For does not buy extra requests.

        This is the end-to-end statement of the fix: without a trusted proxy
        configured, a fresh fake header per request must still hit the same
        counter.
        """
        mw = _middleware(path_limits={"/auth/login": 2})

        statuses = []
        for i in range(3):
            request = _make_request(
                mocker, peer=REAL_CLIENT, xff=f"10.9.9.{i}", method="POST", path="/auth/login"
            )
            statuses.append((await mw.dispatch(request, self._call_next)).status_code)

        assert statuses == [200, 200, 429]


class TestOnlyApiSurfacesAreLimited:
    """The limiter counts API calls, not the web app it is serving.

    Every static file went through the same per-minute budget as the API. One
    page load pulls the bundle, the stylesheet and several icons, so a browser
    could spend most of a minute's allowance before making a single API call —
    and then a request for /box.svg came back 429, which is not something a
    user can act on. Rate limiting a file read off disk protects nothing.
    """

    @staticmethod
    async def _call_next(_request):
        return Response(status_code=200)

    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path",
        [
            "/box.svg",                      # the one that was reported
            "/",                             # the SPA shell
            "/history",                      # a client-side route, also index.html
            "/assets/index-k-Rxh4gB.js",
            "/assets/index-DLH_Yqf1.css",
            "/favicon.ico",
        ],
    )
    async def test_tc_rl_040_static_and_spa_paths_are_never_limited(self, mocker, path):
        """TC-RL-040: dispatch — non-API paths pass however many times they are asked for."""
        mw = _middleware(requests_per_minute=3)

        statuses = [
            (await mw.dispatch(_make_request(mocker, path=path), self._call_next)).status_code
            for _ in range(10)
        ]

        assert statuses == [200] * 10

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_rl_041_static_requests_do_not_consume_the_api_budget(self, mocker):
        """TC-RL-041: dispatch — loading the page leaves the API allowance intact.

        The failure this prevents: a user opens the app, the browser fetches
        the shell and its assets, and the first thing the application does is
        get a 429.
        """
        mw = _middleware(requests_per_minute=3)

        for path in ("/", "/assets/app.js", "/assets/app.css", "/box.svg", "/logo.png"):
            await mw.dispatch(_make_request(mocker, path=path), self._call_next)

        statuses = [
            (await mw.dispatch(_make_request(mocker, path="/api/history"), self._call_next)).status_code
            for _ in range(4)
        ]

        assert statuses == [200, 200, 200, 429]

    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/api/history", "/auth/login"])
    async def test_tc_rl_042_api_surfaces_are_still_limited(self, mocker, path):
        """TC-RL-042: dispatch — the allow-list did not switch the limiter off."""
        mw = _middleware(requests_per_minute=2)

        statuses = [
            (await mw.dispatch(_make_request(mocker, path=path), self._call_next)).status_code
            for _ in range(3)
        ]

        assert statuses == [200, 200, 429]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_rl_043_an_explicit_path_limit_still_wins(self, mocker):
        """TC-RL-043: dispatch — a configured limit outranks the allow-list too.

        Same ordering rule as TC-RL-031, restated against the new skip: a path
        someone deliberately limited must be limited whatever prefix it has.
        """
        mw = _middleware(
            requests_per_minute=100,
            limited_prefixes={"/api"},
            path_limits={"/special": 1},
        )

        statuses = [
            (await mw.dispatch(_make_request(mocker, path="/special"), self._call_next)).status_code
            for _ in range(2)
        ]

        assert statuses == [200, 429]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_rl_044_an_empty_allow_list_limits_everything(self, mocker):
        """TC-RL-044: dispatch — the allow-list is opt-in, not a silent exemption.

        Guards against the skip firing for a config that never asked for it.
        """
        mw = _middleware(requests_per_minute=1, limited_prefixes=set())

        statuses = [
            (await mw.dispatch(_make_request(mocker, path="/box.svg"), self._call_next)).status_code
            for _ in range(2)
        ]

        assert statuses == [200, 429]
