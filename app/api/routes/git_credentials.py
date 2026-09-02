"""Console API for managing per-host Git credentials (admin only).

Tokens are write-only: accepted on create/update, never returned. Responses
expose a masked hint (last 4 chars) so an operator can recognise which token is
stored without revealing it.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.routes.tasks import DB, AuthUser
from app.schemas.git_credential import (
    GitCredentialCreate,
    GitCredentialResponse,
    GitCredentialUpdate,
)
from app.services import git_credentials
from app.services.permission import PermissionCode, require_permission

router = APIRouter(prefix="/api/settings/git-credentials", tags=["git-credentials"])

# Managing shared server credentials is an administrative action.
_ADMIN = Depends(require_permission(PermissionCode.USER_MANAGE))


@router.get("", response_model=list[GitCredentialResponse])
async def list_git_credentials(db: DB, current_user: AuthUser, _: None = _ADMIN):
    """List stored Git credentials (without their tokens)."""
    return await git_credentials.list_credentials(db)


@router.post("", response_model=GitCredentialResponse, status_code=status.HTTP_201_CREATED)
async def create_git_credential(
    data: GitCredentialCreate, db: DB, current_user: AuthUser, _: None = _ADMIN
):
    """Add a Git credential for a host. GitLab/GitHub default their host when omitted."""
    try:
        return await git_credentials.create_credential(db, data, created_by=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.put("/{credential_id}", response_model=GitCredentialResponse)
async def update_git_credential(
    credential_id: int, data: GitCredentialUpdate, db: DB, current_user: AuthUser, _: None = _ADMIN
):
    """Update a credential's label and/or token."""
    cred = await _get_or_404(db, credential_id)
    return await git_credentials.update_credential(db, cred, data)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_git_credential(
    credential_id: int, db: DB, current_user: AuthUser, _: None = _ADMIN
):
    """Delete a credential."""
    cred = await _get_or_404(db, credential_id)
    await git_credentials.delete_credential(db, cred)


async def _get_or_404(db, credential_id: int):
    from sqlalchemy import select

    from app.models.git_credential import GitCredential

    result = await db.execute(select(GitCredential).where(GitCredential.id == credential_id))
    cred = result.scalar_one_or_none()
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="credential not found")
    return cred
