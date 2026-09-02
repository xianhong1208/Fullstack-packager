"""MCP Center single sign-on: OAuth 2.1 authorization-code + PKCE client.

Build Center keeps its own accounts; this adds an optional "Sign in with MCP
Center" path. The flow authenticates the user against MCP Center, then Build
Center issues its own session. Only the subject (and email, if MCP Center
provides one) is taken from the identity token — authorisation stays local.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time

import httpx
import jwt

from app.config import get_settings
from app.services.auth import ALGORITHM, SECRET_KEY

_STATE_TTL_SECONDS = 600  # the login redirect must complete within 10 minutes


class McpOAuthError(RuntimeError):
    """Raised when the SSO flow cannot be completed."""


def is_enabled() -> bool:
    s = get_settings()
    return bool(s.mcp_oauth_enabled and s.mcp_center_url and s.mcp_oauth_client_id)


def _issuer() -> str:
    return get_settings().mcp_center_url.rstrip("/")


def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def build_authorize_url(state: str, code_challenge: str) -> str:
    """Build the MCP Center /oauth/authorize URL for the redirect."""
    from urllib.parse import urlencode

    s = get_settings()
    params = {
        "response_type": "code",
        "client_id": s.mcp_oauth_client_id,
        "redirect_uri": s.mcp_oauth_redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": s.mcp_oauth_scopes,
    }
    return f"{_issuer()}/oauth/authorize?{urlencode(params)}"


def sign_flow_state(state: str, code_verifier: str) -> str:
    """Sign a short-lived cookie carrying the PKCE verifier and state across the redirect."""
    now = int(time.time())
    payload = {"state": state, "cv": code_verifier, "iat": now, "exp": now + _STATE_TTL_SECONDS}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def read_flow_state(cookie: str | None, returned_state: str) -> str:
    """Validate the state cookie against the returned state; return the code_verifier."""
    if not cookie:
        raise McpOAuthError("missing SSO state cookie")
    try:
        payload = jwt.decode(cookie, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise McpOAuthError("invalid or expired SSO state") from exc
    if not secrets.compare_digest(payload.get("state", ""), returned_state):
        raise McpOAuthError("SSO state mismatch")
    return payload["cv"]


async def exchange_code(code: str, code_verifier: str) -> dict:
    """Exchange the authorization code for tokens at MCP Center's token endpoint."""
    s = get_settings()
    data = {
        "grant_type": "authorization_code",
        "client_id": s.mcp_oauth_client_id,
        "code": code,
        "redirect_uri": s.mcp_oauth_redirect_uri,
        "code_verifier": code_verifier,
    }
    if s.mcp_oauth_client_secret:
        data["client_secret"] = s.mcp_oauth_client_secret
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{_issuer()}/oauth/token", data=data)
    if resp.status_code != 200:
        raise McpOAuthError(f"token exchange failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


def identity_from_token(access_token: str) -> dict:
    """Read the subject (and email if present) from the access token.

    The token was just received directly from MCP Center's token endpoint over TLS,
    so it is trusted; the signature is still verified against MCP Center's JWKS when
    reachable, falling back to an unverified decode if JWKS cannot be fetched.
    """
    # Verify the token's RS256 signature against MCP Center's JWKS, unconditionally.
    # The backend just reached the same MCP Center host for the token exchange, so its
    # JWKS is reachable; any fetch or verification failure fails the login rather than
    # falling back to an unverified read (which would accept a forged token).
    issuer = _issuer()
    try:
        jwk_client = jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json")
        signing_key = jwk_client.get_signing_key_from_jwt(access_token)
        claims = jwt.decode(
            access_token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"verify_aud": False},
        )
    except (jwt.PyJWKClientError, jwt.InvalidTokenError, httpx.HTTPError, OSError) as exc:
        raise McpOAuthError(f"could not verify identity token: {exc}") from exc
    subject = claims.get("sub")
    if not subject:
        raise McpOAuthError("identity token has no subject")
    return {"subject": str(subject), "email": claims.get("email"), "claims": claims}
