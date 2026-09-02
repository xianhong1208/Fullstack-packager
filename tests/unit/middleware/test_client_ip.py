"""
Unit tests for the shared client-IP resolver and the server config that makes
it authoritative.
Source: app/middleware/client_ip.py, main.py

Background — why the config assertion below is a real test and not ceremony:
uvicorn's ProxyHeadersMiddleware is enabled by default and trusts 127.0.0.1, so
it rewrites scope["client"] from X-Forwarded-For BEFORE application code runs.
With it on, resolve_client_ip receives an already-spoofed request.client and
every unit test here still passes while the deployed service is bypassable.
The only defence against that regression is asserting the launch config.
"""

import pytest
from starlette.datastructures import Headers

from app.middleware.client_ip import resolve_client_ip

TRUSTED_PROXY = "10.0.0.1"
REAL_CLIENT = "203.0.113.9"
SPOOFED = "1.2.3.4"


def _request(mocker, *, peer: str | None, xff: str | None = None):
    request = mocker.MagicMock()
    request.headers = Headers({"x-forwarded-for": xff} if xff is not None else {})
    request.client = mocker.MagicMock(host=peer) if peer is not None else None
    return request


class TestResolveClientIp:
    """Tests for resolve_client_ip — used by the limiter AND the audit trail."""

    @pytest.mark.unit
    def test_tc_cip_001_no_trusted_proxies_ignores_xff(self, mocker):
        """TC-CIP-001: resolve_client_ip — empty trust set means the header is ignored."""
        assert resolve_client_ip(_request(mocker, peer=REAL_CLIENT, xff=SPOOFED), set()) == REAL_CLIENT

    @pytest.mark.unit
    def test_tc_cip_002_untrusted_peer_ignores_xff(self, mocker):
        """TC-CIP-002: resolve_client_ip — a peer outside the trust set cannot forward."""
        request = _request(mocker, peer="198.51.100.7", xff=SPOOFED)
        assert resolve_client_ip(request, {TRUSTED_PROXY}) == "198.51.100.7"

    @pytest.mark.unit
    def test_tc_cip_003_trusted_proxy_forwards_client(self, mocker):
        """TC-CIP-003: resolve_client_ip — a trusted proxy's forwarded address is used."""
        request = _request(mocker, peer=TRUSTED_PROXY, xff=REAL_CLIENT)
        assert resolve_client_ip(request, {TRUSTED_PROXY}) == REAL_CLIENT

    @pytest.mark.unit
    def test_tc_cip_004_takes_rightmost_entry(self, mocker):
        """TC-CIP-004: resolve_client_ip — a client-injected prefix cannot displace the proxy entry."""
        request = _request(mocker, peer=TRUSTED_PROXY, xff=f"{SPOOFED}, {REAL_CLIENT}")
        assert resolve_client_ip(request, {TRUSTED_PROXY}) == REAL_CLIENT

    @pytest.mark.unit
    @pytest.mark.parametrize("xff", [None, "", "  ", ",", " , "])
    def test_tc_cip_005_unusable_header_falls_back_to_peer(self, mocker, xff):
        """TC-CIP-005: resolve_client_ip — empty/blank header falls back to the peer."""
        request = _request(mocker, peer=TRUSTED_PROXY, xff=xff)
        assert resolve_client_ip(request, {TRUSTED_PROXY}) == TRUSTED_PROXY

    @pytest.mark.unit
    def test_tc_cip_006_missing_client_yields_unknown(self, mocker):
        """TC-CIP-006: resolve_client_ip — no client info resolves to 'unknown'."""
        assert resolve_client_ip(_request(mocker, peer=None), set()) == "unknown"


class TestUvicornLaunchConfig:
    """Guards the server setting that makes resolve_client_ip authoritative."""

    @pytest.mark.unit
    def test_tc_cip_010_proxy_headers_disabled(self):
        """TC-CIP-010: main.UVICORN_KWARGS — uvicorn must not rewrite client from XFF.

        If this flips back to True, uvicorn resolves the forwarded header with
        its own policy (trusting 127.0.0.1) and hands the application a client
        address it cannot verify — re-opening the bypass this module exists to
        close.
        """
        import main

        assert main.UVICORN_KWARGS["proxy_headers"] is False

    @pytest.mark.unit
    def test_tc_cip_011_no_second_uvicorn_entry_point(self):
        """TC-CIP-011: app.main — must not define its own uvicorn launcher.

        app/main.py used to carry a `main()` calling uvicorn.run(app, ...)
        without proxy_headers=False. Anything started through it got uvicorn's
        ProxyHeadersMiddleware back, which rewrites scope["client"] from
        X-Forwarded-For before application code runs — the exact rewriting
        app/middleware/client_ip.py states is disabled.

        TC-CIP-010 above cannot catch that: it inspects the root main.py's
        UVICORN_KWARGS and never looks at app.main. A second launcher is a
        second place for the flags to drift, so the check is "there is only
        one", not "both are configured correctly".
        """
        import app.main

        assert not hasattr(app.main, "main"), (
            "app/main.py must not define a uvicorn entry point — "
            "the repository-root main.py is the only launcher"
        )
