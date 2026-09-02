"""Authentication API routes."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.middleware.client_ip import resolve_client_ip
from app.models.user import User
from app.models.role import Role
from app.schemas.user import (
    UserCreate,
    UserLogin,
    UserResponse,
    TokenResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    SecurityQuestionSet,
    SecurityQuestionVerify,
    PasswordChange,
    PasswordReset,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginHistoryResponse,
    LoginHistoryList,
    SessionResponse,
    SessionList,
)
from app.services.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    authenticate_user,
    create_tokens,
    create_user,
    refresh_access_token,
    revoke_refresh_token,
    revoke_all_user_tokens,
    record_login_attempt,
    update_last_login,
    get_login_history,
    get_active_sessions,
    revoke_session,
    set_security_question,
    verify_security_answer,
    get_security_question,
    create_password_reset_token,
    reset_password_with_token,
    change_password,
    get_user_by_username,
    get_user_count,
    get_user_permissions,
    hash_password,
    hash_token,
)
from app.services import mcp_oauth
from app.services.permission import get_current_user, CurrentUser
from app.services.audit import log_action, AuditAction, AuditStatus, ResourceType

router = APIRouter(prefix="/auth", tags=["auth"])


def get_client_ip(request: Request) -> str | None:
    """Extract the client IP recorded in login history and audit logs.

    Uses the same trust policy as the rate limiter — X-Forwarded-For is only
    honoured from a configured proxy. Reading the header directly (as this did
    before) let anyone write an arbitrary IP into the audit trail and the login
    history that admins review, which is exactly the record that must not be
    attacker-controlled.
    """
    ip = resolve_client_ip(request, get_settings().trusted_proxies)
    return None if ip == "unknown" else ip


@router.get("/check-first-user")
async def check_first_user(db: AsyncSession = Depends(get_db)):
    """Check if this is the first user (no users exist)."""
    count = await get_user_count(db)
    return {"is_first_user": count == 0}


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    data: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user (first user becomes admin, others require admin approval)."""
    # Check if username already exists
    existing = await get_user_by_username(db, data.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    # Serialize the first-user check: two concurrent registrations on a fresh
    # install could both read count==0 and both become admin. On PostgreSQL a
    # transaction-scoped advisory lock (released at commit/rollback) does this;
    # SQLite serializes writes itself (single writer), so the lock is skipped.
    if db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(823941)"))

    # Check if this is the first user
    count = await get_user_count(db)
    is_first_user = count == 0

    # Get appropriate role (admin for first user, user for others)
    role_name = "admin" if is_first_user else "user"
    result = await db.execute(select(Role).where(Role.name == role_name))
    role = result.scalar_one_or_none()

    # Create user (first user is active, others need admin approval)
    user = await create_user(
        db, data.username, data.password,
        role_id=role.id if role else None,
        is_active=is_first_user,
    )

    # Non-first users: return pending approval response (no tokens)
    if not is_first_user:
        return {
            "message": "Account created successfully. Please wait for admin approval.",
            "pending_approval": True,
        }

    # First user: auto-login with tokens
    ip_address = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    access_token, refresh_token = await create_tokens(
        db, user, remember_me=False, device_info=user_agent, ip_address=ip_address
    )

    # Record login
    await record_login_attempt(
        db, data.username, success=True, user_id=user.id,
        ip_address=ip_address, user_agent=user_agent
    )
    await update_last_login(db, user)

    # Get permissions
    permissions = await get_user_permissions(db, user.id)

    # Build response
    user_response = UserResponse(
        id=user.id,
        username=user.username,
        is_active=user.is_active,
        created_at=user.created_at,
        has_security_question=bool(user.security_question),
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=user_response,
        permissions=permissions,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    data: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Login with username and password."""
    ip_address = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    user = await authenticate_user(db, data.username, data.password)
    if not user:
        # Record failed attempt
        await record_login_attempt(
            db, data.username, success=False,
            ip_address=ip_address, user_agent=user_agent,
            failure_reason="Invalid username or password"
        )
        # Audit log for failed login
        await log_action(
            db,
            action=AuditAction.LOGIN_FAILED,
            resource_type=ResourceType.AUTH,
            status=AuditStatus.FAILED,
            ip_address=ip_address,
            user_agent=user_agent,
            error_message="Invalid username or password",
            details={"username": data.username},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Check if account is active (pending admin approval)
    if not user.is_active:
        await record_login_attempt(
            db, data.username, success=False, user_id=user.id,
            ip_address=ip_address, user_agent=user_agent,
            failure_reason="Account pending approval"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="帳號尚未啟用，請等待管理員審核",
        )

    # Create tokens
    access_token, refresh_token = await create_tokens(
        db, user, remember_me=data.remember_me,
        device_info=user_agent, ip_address=ip_address
    )

    # Record successful login
    await record_login_attempt(
        db, data.username, success=True, user_id=user.id,
        ip_address=ip_address, user_agent=user_agent
    )
    await update_last_login(db, user)

    # Audit log for successful login
    await log_action(
        db,
        action=AuditAction.LOGIN_SUCCESS,
        resource_type=ResourceType.AUTH,
        status=AuditStatus.SUCCESS,
        actor_id=user.id,
        actor_name=user.username,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    # Get permissions
    permissions = await get_user_permissions(db, user.id)

    # Load role for response
    if user.role_id:
        result = await db.execute(
            select(User)
            .where(User.id == user.id)
            .options(selectinload(User.role))
        )
        user = result.scalar_one()

    user_response = UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        role=user.role,
        has_security_question=bool(user.security_question),
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=user_response,
        permissions=permissions,
    )


@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_token(data: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    """Refresh access token using refresh token."""
    result = await refresh_access_token(db, data.refresh_token)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    new_access_token, new_refresh_token = result
    return RefreshTokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout")
async def logout(
    data: RefreshTokenRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """Logout current session by revoking refresh token."""
    await revoke_refresh_token(db, data.refresh_token)

    # Audit log
    await log_action(
        db,
        action=AuditAction.LOGOUT,
        resource_type=ResourceType.AUTH,
        status=AuditStatus.SUCCESS,
        actor_id=current_user.id,
        actor_name=current_user.username,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )

    return {"message": "Logged out successfully"}


@router.post("/logout-all")
async def logout_all(
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """Logout from all devices by revoking all refresh tokens."""
    count = await revoke_all_user_tokens(db, current_user.id)

    # Audit log
    await log_action(
        db,
        action=AuditAction.LOGOUT_ALL,
        resource_type=ResourceType.AUTH,
        status=AuditStatus.SUCCESS,
        actor_id=current_user.id,
        actor_name=current_user.username,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        details={"sessions_revoked": count},
    )

    return {"message": f"Logged out from {count} sessions"}


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """Get current user info."""
    result = await db.execute(
        select(User)
        .where(User.id == current_user.id)
        .options(selectinload(User.role))
    )
    user = result.scalar_one()

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        role=user.role,
        has_security_question=bool(user.security_question),
    )


# ===== Security Question Endpoints =====


@router.put("/security-question")
async def set_security_question_endpoint(
    data: SecurityQuestionSet,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """Set or update security question for password recovery."""
    success = await set_security_question(db, current_user.id, data.question, data.answer)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to set security question",
        )
    return {"message": "Security question set successfully"}


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(data: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Initiate forgot password flow - returns security question if set."""
    question = await get_security_question(db, data.username)

    if not question:
        # NOTE: this branch is ambiguous — it covers both "no such user" and
        # "user exists but set no question". The OTHER branch is not: returning
        # has_security_question=true plus the question text confirms the account
        # exists and hands over the prompt. The comment that used to sit here
        # claimed the endpoint does not reveal whether a user exists, which is
        # false and would let a reviewer skip the block.
        #
        # Kept as-is rather than "fixed", because hiding the question would
        # break the only self-service recovery path this system has, and on an
        # internal tool the usernames are colleagues' addresses — already known
        # to anyone who can reach the login page.
        #
        # What actually mattered in that chain was guessing the ANSWER, which
        # used to fall through to the global 60/min bucket. That is now keyed at
        # 5/min alongside login (see app/main.py path_limits).
        return ForgotPasswordResponse(
            has_security_question=False,
            message="If this account exists and has a security question set, please answer it to reset your password.",
        )

    return ForgotPasswordResponse(
        has_security_question=True,
        security_question=question,
        message="Please answer your security question to reset your password.",
    )


@router.post("/verify-security-answer")
async def verify_security_answer_endpoint(
    data: SecurityQuestionVerify,
    db: AsyncSession = Depends(get_db),
):
    """Verify security question answer and return reset token."""
    user = await get_user_by_username(db, data.username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid answer",
        )

    is_valid = await verify_security_answer(db, user.id, data.answer)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid answer",
        )

    # Create reset token
    token = await create_password_reset_token(db, user.id)
    return {"reset_token": token, "message": "Answer verified. Use the token to reset your password."}


@router.post("/reset-password")
async def reset_password_endpoint(data: PasswordReset, db: AsyncSession = Depends(get_db)):
    """Reset password using reset token."""
    success = await reset_password_with_token(db, data.token, data.new_password)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )
    return {"message": "Password reset successfully. Please login with your new password."}


@router.post("/change-password")
async def change_password_endpoint(
    data: PasswordChange,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """Change password (requires current password)."""
    ip_address = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    success = await change_password(db, current_user.id, data.current_password, data.new_password)
    if not success:
        await log_action(
            db,
            action=AuditAction.PASSWORD_CHANGE,
            resource_type=ResourceType.USER,
            resource_id=current_user.id,
            status=AuditStatus.FAILED,
            actor_id=current_user.id,
            actor_name=current_user.username,
            ip_address=ip_address,
            user_agent=user_agent,
            error_message="Invalid current password",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid current password",
        )

    await log_action(
        db,
        action=AuditAction.PASSWORD_CHANGE,
        resource_type=ResourceType.USER,
        resource_id=current_user.id,
        status=AuditStatus.SUCCESS,
        actor_id=current_user.id,
        actor_name=current_user.username,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return {"message": "Password changed successfully"}


# ===== Login History Endpoints =====


@router.get("/login-history", response_model=LoginHistoryList)
async def get_login_history_endpoint(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
):
    """Get current user's login history."""
    from app.models.login_history import LoginHistory

    history = await get_login_history(db, current_user.id, limit=limit, offset=offset)

    # Get total count
    result = await db.execute(
        select(func.count()).select_from(LoginHistory).where(LoginHistory.user_id == current_user.id)
    )
    total = result.scalar() or 0

    return LoginHistoryList(
        items=[LoginHistoryResponse.model_validate(h) for h in history],
        total=total,
        limit=limit,
        offset=offset,
    )


# ===== Active Sessions Endpoints =====


@router.get("/active-sessions", response_model=SessionList)
async def get_active_sessions_endpoint(
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """Get current user's active sessions."""
    sessions = await get_active_sessions(db, current_user.id)

    # Try to identify current session by IP
    current_ip = get_client_ip(request)

    session_responses = []
    for s in sessions:
        response = SessionResponse(
            id=s.id,
            created_at=s.created_at,
            expires_at=s.expires_at,
            is_remember_me=s.is_remember_me,
            device_info=s.device_info,
            ip_address=s.ip_address,
            is_current=(s.ip_address == current_ip),
        )
        session_responses.append(response)

    return SessionList(sessions=session_responses, total=len(session_responses))


@router.delete("/sessions/{session_id}")
async def revoke_session_endpoint(
    session_id: int,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
):
    """Revoke a specific session."""
    success = await revoke_session(db, current_user.id, session_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return {"message": "Session revoked successfully"}


# ---------------------------------------------------------------------------
# MCP Center single sign-on (optional; local email/password stays available)
# ---------------------------------------------------------------------------

_SSO_STATE_COOKIE = "mcp_sso_state"


@router.get("/oauth/mcp/status")
async def mcp_oauth_status() -> dict:
    """Whether "Sign in with MCP Center" should be offered on the login page."""
    return {"enabled": mcp_oauth.is_enabled()}


@router.get("/oauth/mcp/login")
async def mcp_oauth_login() -> RedirectResponse:
    """Start the SSO flow: redirect the browser to MCP Center's consent screen."""
    if not mcp_oauth.is_enabled():
        raise HTTPException(status_code=404, detail="MCP Center sign-in is not enabled")
    state = secrets.token_urlsafe(24)
    verifier, challenge = mcp_oauth.generate_pkce()
    redirect = RedirectResponse(mcp_oauth.build_authorize_url(state, challenge), status_code=302)
    # The PKCE verifier + state travel in a short-lived, signed, HttpOnly cookie.
    # Marked Secure when the deployment redirect URI is https (kept off for the
    # http localhost dev default so the cookie is not dropped over plain http).
    secure = get_settings().mcp_oauth_redirect_uri.lower().startswith("https")
    redirect.set_cookie(
        _SSO_STATE_COOKIE,
        mcp_oauth.sign_flow_state(state, verifier),
        max_age=600,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/auth/oauth/mcp",
    )
    return redirect


@router.get("/oauth/mcp/callback")
async def mcp_oauth_callback(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Finish the SSO flow: exchange the code, map the identity to a local account,
    issue Build Center's own session, and hand the tokens back to the SPA."""
    settings = get_settings()
    frontend = settings.mcp_oauth_redirect_uri.rsplit("/auth/", 1)[0]  # app origin

    def _fail(reason: str) -> RedirectResponse:
        r = RedirectResponse(f"{frontend}/login?sso_error={reason}", status_code=302)
        r.delete_cookie(_SSO_STATE_COOKIE, path="/auth/oauth/mcp")
        return r

    if error:
        return _fail(error)
    if not code or not state:
        return _fail("missing_code")

    try:
        verifier = mcp_oauth.read_flow_state(request.cookies.get(_SSO_STATE_COOKIE), state)
        tokens = await mcp_oauth.exchange_code(code, verifier)
        identity = mcp_oauth.identity_from_token(tokens["access_token"])
    except (mcp_oauth.McpOAuthError, KeyError) as e:
        return _fail(str(e).replace(" ", "_")[:60] or "sso_failed")

    user = await _find_or_create_sso_user(db, identity)
    if not user.is_active:
        return _fail("account_pending_approval")

    ip = resolve_client_ip(request)
    access_token, refresh_token = await create_tokens(
        db, user, remember_me=False, device_info="MCP Center SSO", ip_address=ip
    )
    await update_last_login(db, user)
    expires_in = ACCESS_TOKEN_EXPIRE_MINUTES * 60

    # Hand the tokens to the SPA in the URL fragment (never sent to a server, so it
    # does not land in access logs), where the login page stores them and continues.
    r = RedirectResponse(
        f"{frontend}/login#access_token={access_token}"
        f"&refresh_token={refresh_token}&expires_in={expires_in}",
        status_code=302,
    )
    r.delete_cookie(_SSO_STATE_COOKIE, path="/auth/oauth/mcp")
    return r


async def _find_or_create_sso_user(db: AsyncSession, identity: dict) -> User:
    """Find the local account linked to this MCP Center identity, or create one.

    New SSO accounts are created inactive with the 'user' role, exactly like a
    self-registration — an administrator approves them before first sign-in.
    """
    subject = identity["subject"]
    result = await db.execute(
        select(User)
        .where(User.oauth_provider == "mcp", User.oauth_subject == subject)
        .options(selectinload(User.role))
    )
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    count = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
    is_first_user = count == 0
    role_name = "admin" if is_first_user else "user"
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()

    # The identity is keyed only by (oauth_provider, oauth_subject). We deliberately
    # DO NOT link to an existing local account by matching email: MCP Center emails
    # are not proven here, so silently binding a foreign OAuth subject to a local
    # account would be an account-takeover path. If the email is already taken, the
    # SSO account is created without one (linking must be an explicit, authenticated
    # action, not automatic).
    email = identity.get("email")
    if email:
        email_taken = (
            await db.execute(select(User.id).where(User.email == email))
        ).scalar_one_or_none()
        if email_taken is not None:
            email = None

    # Build a unique local username from the email local-part or the subject.
    base = (email.split("@")[0] if email else f"mcp-{subject[:8]}")[:80]
    username = base
    while (await db.execute(select(User).where(User.username == username))).scalar_one_or_none():
        username = f"{base}-{secrets.token_hex(2)}"

    user = User(
        username=username,
        # SSO users never sign in with a password; store an unusable random hash.
        password_hash=hash_password(secrets.token_urlsafe(32)),
        email=email,
        role_id=role.id if role else None,
        is_active=is_first_user,
        oauth_provider="mcp",
        oauth_subject=subject,
    )
    db.add(user)
    await db.commit()
    result = await db.execute(
        select(User).where(User.id == user.id).options(selectinload(User.role))
    )
    return result.scalar_one()
