"""Unit tests for Git credential handling: encryption, provider inference, token injection.

DB-free: the resolver is exercised against a lightweight fake session so these stay in the
unit tier (no Postgres). Integration with a real DB is covered separately.
"""

import pytest

from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.git_credentials import infer_provider, resolve_for_url
from app.services.git_service import inject_token, mask_token_in_log


class TestEncryption:
    @pytest.mark.unit
    def test_roundtrip(self):
        c = encrypt_secret("glpat-abc123")
        assert c != "glpat-abc123"
        assert decrypt_secret(c) == "glpat-abc123"

    @pytest.mark.unit
    def test_ciphertext_differs_each_time(self):
        assert encrypt_secret("same") != encrypt_secret("same")


class TestProviderInference:
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "host,expected",
        [
            ("github.com", "github"),
            ("api.github.com", "github"),
            ("gitlab.com", "gitlab"),
            ("gitlab.example.com", "gitlab"),
            ("git.corp.internal", "generic"),
            ("bitbucket.org", "generic"),
        ],
    )
    def test_infer(self, host, expected):
        assert infer_provider(host) == expected


class TestInjection:
    @pytest.mark.unit
    def test_github_uses_x_access_token(self):
        out = inject_token("https://github.com/o/r.git", "TOK", "github")
        assert out == "https://x-access-token:TOK@github.com/o/r.git"

    @pytest.mark.unit
    def test_gitlab_uses_oauth2(self):
        out = inject_token("https://gitlab.com/o/r.git", "TOK", "gitlab")
        assert out == "https://oauth2:TOK@gitlab.com/o/r.git"

    @pytest.mark.unit
    def test_none_token_is_passthrough(self):
        assert inject_token("https://github.com/o/r.git", None, "github") == "https://github.com/o/r.git"

    @pytest.mark.unit
    def test_masking_hides_both_userinfo_forms(self):
        for prov in ("github", "gitlab"):
            url = inject_token("https://h/o/r.git", "SECRETTOKEN", prov)
            masked = mask_token_in_log(url, "SECRETTOKEN")
            assert "SECRETTOKEN" not in masked


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeSession:
    """Minimal AsyncSession stand-in: returns a preset credential, records commits."""

    def __init__(self, cred=None):
        self._cred = cred
        self.commits = 0

    async def execute(self, _stmt):
        return _FakeResult(self._cred)

    async def commit(self):
        self.commits += 1


class _Cred:
    def __init__(self, provider, token_ciphertext):
        self.provider = provider
        self.token_encrypted = token_ciphertext
        self.last_used_at = None


class TestResolve:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_stored_credential_wins_and_is_decrypted(self):
        cred = _Cred("github", encrypt_secret("ghp-xyz"))
        db = _FakeSession(cred)
        token, provider = await resolve_for_url(db, "https://github.com/o/r.git")
        assert token == "ghp-xyz"
        assert provider == "github"
        assert db.commits == 1  # last_used_at updated

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_no_credential_no_fallback_returns_none(self, monkeypatch):
        from app.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("GITLAB_TOKEN", "")
        db = _FakeSession(None)
        token, provider = await resolve_for_url(db, "https://github.com/o/r.git")
        assert token is None
        assert provider == "github"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_gitlab_falls_back_to_env_token(self, monkeypatch):
        from app.config import get_settings

        monkeypatch.setenv("GITLAB_TOKEN", "glpat-fallback")
        get_settings.cache_clear()
        try:
            db = _FakeSession(None)
            token, provider = await resolve_for_url(db, "https://gitlab.com/o/r.git")
            assert token == "glpat-fallback"
            assert provider == "gitlab"
        finally:
            get_settings.cache_clear()
