"""Pydantic schemas for Git credential management.

The token is write-only: it is accepted on create/update but never returned.
Responses expose only a masked hint (last 4 chars).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

GitProvider = Literal["gitlab", "github", "generic"]


class GitCredentialCreate(BaseModel):
    """Create a credential. `host` may be omitted for gitlab/github (a default is used)."""

    provider: GitProvider
    host: str | None = Field(default=None, max_length=255)
    label: str | None = Field(default=None, max_length=128)
    token: str = Field(min_length=1, max_length=1024)

    @field_validator("token")
    @classmethod
    def _strip_token(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("token must not be blank")
        return v


class GitCredentialUpdate(BaseModel):
    """Update a credential; supply only the fields to change."""

    label: str | None = Field(default=None, max_length=128)
    token: str | None = Field(default=None, min_length=1, max_length=1024)


class GitCredentialResponse(BaseModel):
    """Credential as returned by the API — never includes the token itself."""

    id: int
    provider: GitProvider
    host: str
    label: str | None
    token_hint: str | None
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None

    model_config = {"from_attributes": True}
