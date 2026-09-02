"""Unit tests for MCP Center SSO helpers (PKCE, authorize URL, signed flow state)."""

from urllib.parse import parse_qs, urlparse

import pytest

from app.services import mcp_oauth


@pytest.fixture(autouse=True)
def _enable_sso(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("MCP_OAUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_CENTER_URL", "http://center.test:4568")
    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", "mcpc_build")
    monkeypatch.setenv("MCP_OAUTH_REDIRECT_URI", "http://localhost:5018/auth/oauth/mcp/callback")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestPkce:
    @pytest.mark.unit
    def test_verifier_and_challenge_differ(self):
        v, c = mcp_oauth.generate_pkce()
        assert v and c and v != c

    @pytest.mark.unit
    def test_is_enabled_true_when_configured(self):
        assert mcp_oauth.is_enabled() is True


class TestAuthorizeUrl:
    @pytest.mark.unit
    def test_contains_pkce_and_client(self):
        url = mcp_oauth.build_authorize_url("st8", "chal")
        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        assert parsed.netloc == "center.test:4568"
        assert parsed.path == "/oauth/authorize"
        assert q["response_type"] == ["code"]
        assert q["client_id"] == ["mcpc_build"]
        assert q["code_challenge_method"] == ["S256"]
        assert q["code_challenge"] == ["chal"]
        assert q["state"] == ["st8"]


class TestFlowState:
    @pytest.mark.unit
    def test_roundtrip_returns_verifier(self):
        cookie = mcp_oauth.sign_flow_state("state-1", "verifier-1")
        assert mcp_oauth.read_flow_state(cookie, "state-1") == "verifier-1"

    @pytest.mark.unit
    def test_state_mismatch_rejected(self):
        cookie = mcp_oauth.sign_flow_state("state-1", "verifier-1")
        with pytest.raises(mcp_oauth.McpOAuthError):
            mcp_oauth.read_flow_state(cookie, "other-state")

    @pytest.mark.unit
    def test_missing_cookie_rejected(self):
        with pytest.raises(mcp_oauth.McpOAuthError):
            mcp_oauth.read_flow_state(None, "state-1")

    @pytest.mark.unit
    def test_tampered_cookie_rejected(self):
        cookie = mcp_oauth.sign_flow_state("state-1", "verifier-1")
        with pytest.raises(mcp_oauth.McpOAuthError):
            mcp_oauth.read_flow_state(cookie + "x", "state-1")
