"""Manage per-host Git credentials and resolve the right token for a clone URL.

Replaces the single GITLAB_TOKEN env var. Tokens are stored encrypted and chosen
by the URL's host, so GitLab and GitHub (and self-hosted instances) can each have
their own token. `settings.gitlab_token` remains a fallback for GitLab-style
hosts when no stored credential matches.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.git_credential import GitCredential
from app.schemas.git_credential import GitCredentialCreate, GitCredentialUpdate
from app.services.crypto import encrypt_secret, decrypt_secret

# Default host for each provider, used when the caller does not give one.
_PROVIDER_DEFAULT_HOST = {"gitlab": "gitlab.com", "github": "github.com"}


def infer_provider(host: str) -> str:
    """Guess the provider from a host name (for injection format + fallbacks)."""
    h = host.lower()
    if "github" in h:
        return "github"
    if "gitlab" in h:
        return "gitlab"
    return "generic"


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


async def list_credentials(db: AsyncSession) -> list[GitCredential]:
    result = await db.execute(select(GitCredential).order_by(GitCredential.host))
    return list(result.scalars().all())


async def get_by_host(db: AsyncSession, host: str) -> GitCredential | None:
    result = await db.execute(select(GitCredential).where(GitCredential.host == host.lower()))
    return result.scalar_one_or_none()


async def create_credential(
    db: AsyncSession, data: GitCredentialCreate, created_by: int | None
) -> GitCredential:
    host = (data.host or _PROVIDER_DEFAULT_HOST.get(data.provider) or "").lower()
    if not host:
        raise ValueError("host is required for a generic provider")
    if await get_by_host(db, host):
        raise ValueError(f"a credential for host '{host}' already exists")
    cred = GitCredential(
        provider=data.provider,
        host=host,
        label=data.label,
        token_encrypted=encrypt_secret(data.token),
        token_hint=data.token[-4:],
        created_by=created_by,
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    return cred


async def update_credential(
    db: AsyncSession, cred: GitCredential, data: GitCredentialUpdate
) -> GitCredential:
    if data.label is not None:
        cred.label = data.label
    if data.token is not None:
        cred.token_encrypted = encrypt_secret(data.token)
        cred.token_hint = data.token[-4:]
    await db.commit()
    await db.refresh(cred)
    return cred


async def delete_credential(db: AsyncSession, cred: GitCredential) -> None:
    await db.delete(cred)
    await db.commit()


async def resolve_for_url(db: AsyncSession, url: str) -> tuple[str | None, str]:
    """Return (token, provider) for cloning `url`.

    Looks up a stored credential by the URL's host. Falls back to
    settings.gitlab_token for GitLab-style hosts when nothing is stored. The
    token may be None (public repo / no credential); provider still guides how a
    token, if any, is injected into the URL.
    """
    host = _host_of(url)
    provider = infer_provider(host)
    cred = await get_by_host(db, host) if host else None
    if cred is not None:
        cred.last_used_at = datetime.now(timezone.utc)
        await db.commit()
        return decrypt_secret(cred.token_encrypted), cred.provider
    if provider in ("gitlab", "generic"):
        fallback = get_settings().gitlab_token
        if fallback:
            return fallback, provider
    return None, provider
