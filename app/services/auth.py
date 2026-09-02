"""Authentication service with JWT tokens and refresh token support."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User
from app.models.refresh_token import RefreshToken
from app.models.login_history import LoginHistory
from app.models.password_reset import PasswordResetToken
from app.config import get_settings
from app.models.role import Role

settings = get_settings()

# Bcrypt settings
BCRYPT_ROUNDS = 12

# JWT settings - use persistent secret from config, or generate and persist one
def _load_or_create_jwt_secret() -> str:
    """Load JWT secret from config, or generate and persist to .env."""
    key = getattr(settings, "jwt_secret_key", None)
    if key:
        return key

    import os
    from pathlib import Path
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    new_key = secrets.token_hex(32)

    # Append to .env so the secret persists across restarts. This file holds a
    # signing key, so it must be owner-only (0600): create it with restrictive
    # perms via os.open, and tighten an already-existing .env with chmod.
    try:
        fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(f"\n# Auto-generated JWT secret key (do not remove)\nJWT_SECRET_KEY={new_key}\n")
        os.chmod(env_path, 0o600)
    except OSError:
        pass  # Fallback: key lives only in memory this session

    return new_key

SECRET_KEY = _load_or_create_jwt_secret()
ALGORITHM = "HS256"

# Token expiration settings
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7
REFRESH_TOKEN_REMEMBER_ME_DAYS = 30
PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = 30


def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode(), salt).decode()


def is_bcrypt_hash(password_hash: str) -> bool:
    """Check if a hash is bcrypt format (starts with $2a$, $2b$, or $2y$)."""
    return password_hash.startswith(("$2a$", "$2b$", "$2y$"))


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against hash (supports bcrypt and legacy SHA-256)."""
    if is_bcrypt_hash(password_hash):
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    # Legacy SHA-256 format for gradual migration
    return hashlib.sha256(password.encode()).hexdigest() == password_hash


def needs_password_rehash(password_hash: str) -> bool:
    """Check if password needs to be rehashed (migrated from SHA-256 to bcrypt)."""
    return not is_bcrypt_hash(password_hash)


def hash_token(token: str) -> str:
    """Hash a token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(
    user_id: int,
    username: str,
    permissions: list[str] | None = None,
    role_name: str | None = None,
) -> str:
    """Create JWT access token with permissions."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "username": username,
        "permissions": permissions or [],
        "role": role_name,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token_value() -> str:
    """Generate a random refresh token value."""
    return secrets.token_urlsafe(64)


def decode_token(token: str) -> dict | None:
    """Decode and validate JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# === Download tickets ===
# A build artifact can be several GB. Fetching it through XHR means buffering
# the whole thing in browser memory — no resume, no progress, and a dead tab on
# a multi-GB image — which throws away the Content-Length/Range support the
# download endpoint provides. Handing the URL to the browser's native
# downloader fixes all of that, but a plain navigation cannot carry an
# Authorization header.
#
# So: the client exchanges its access token (over an authenticated POST) for a
# ticket that travels in the URL. Unlike the access token this is safe to log,
# because it is useless for anything except one artifact, for two minutes:
#   * bound to a single (user, task) pair
#   * typ claim keeps access tokens and tickets from being used for each other
#   * redemption re-checks that the user is still active and still permitted,
#     so revocation takes effect immediately
# Must outlive the download it authorises, not just the click that starts it.
#
# This was 120s, chosen to keep the blast radius small, and that broke the very
# feature the ticket exists for. Native downloads resume: Chrome re-requests the
# same URL after a network blip or a pause, and a 2 GB artifact takes minutes to
# transfer in the first place. A retry after the ticket expired returns 401,
# which Chrome's download manager reports as "Needs authorization" — with no
# indication that a timer was the cause.
#
# 30 minutes still leaves the ticket narrowly scoped: one artifact, one user,
# no other API reachable with it, and account state re-checked on every
# redemption. The exposure that buys resumability is small; the exposure that
# 120s avoided was already small.
DOWNLOAD_TICKET_EXPIRE_SECONDS = 1800
_DOWNLOAD_TICKET_TYP = "download"


def create_download_ticket(user_id: int, task_id: str) -> str:
    """Mint a short-lived, single-artifact download ticket."""
    payload = {
        "sub": str(user_id),
        "tid": task_id,
        "typ": _DOWNLOAD_TICKET_TYP,
        "exp": datetime.now(timezone.utc)
        + timedelta(seconds=DOWNLOAD_TICKET_EXPIRE_SECONDS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_download_ticket(ticket: str) -> tuple[int, str] | None:
    """Return (user_id, task_id) from a valid ticket, or None.

    Rejects anything that is not specifically a download ticket — passing an
    access token here must not work, or the narrow scope would be pointless.
    """
    payload = decode_token(ticket)
    if not payload or payload.get("typ") != _DOWNLOAD_TICKET_TYP:
        return None
    task_id = payload.get("tid")
    if not task_id:
        return None
    try:
        return int(payload.get("sub", "")), task_id
    except (TypeError, ValueError):
        return None


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    """Get user by username."""
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    """Get user by ID."""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_with_role(db: AsyncSession, user_id: int) -> User | None:
    """Get user by ID with role and permissions eagerly loaded."""
    result = await db.execute(
        select(User)
        .where(User.id == user_id)
        .options(
            selectinload(User.role).selectinload(Role.permissions)
        )
    )
    return result.scalar_one_or_none()


async def get_user_permissions(db: AsyncSession, user_id: int) -> list[str]:
    """Get all permission codes for a user."""
    user = await get_user_with_role(db, user_id)
    if not user or not user.role:
        return []
    return [p.code for p in user.role.permissions]


async def create_user(
    db: AsyncSession,
    username: str,
    password: str,
    role_id: int | None = None,
    is_active: bool = True,
) -> User:
    """Create a new user."""
    user = User(
        username=username,
        password_hash=hash_password(password),
        role_id=role_id,
        is_active=is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User | None:
    """Authenticate user with username and password.

    Returns the user if credentials are valid (regardless of is_active status).
    The caller is responsible for checking is_active and returning appropriate errors.
    Automatically migrates SHA-256 passwords to bcrypt on successful login.
    """
    user = await get_user_by_username(db, username)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None

    # Auto-migrate legacy SHA-256 passwords to bcrypt
    if needs_password_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        await db.commit()

    return user


async def get_user_count(db: AsyncSession) -> int:
    """Get total user count."""
    result = await db.execute(select(func.count()).select_from(User))
    return result.scalar() or 0


# ===== Refresh Token Management =====

async def create_tokens(
    db: AsyncSession,
    user: User,
    remember_me: bool = False,
    device_info: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, str]:
    """Create access token and refresh token pair."""
    # Get user permissions
    permissions = await get_user_permissions(db, user.id)
    role_name = user.role.name if user.role else None

    # Create access token
    access_token = create_access_token(user.id, user.username, permissions, role_name)

    # Create refresh token
    refresh_token_value = create_refresh_token_value()
    expire_days = REFRESH_TOKEN_REMEMBER_ME_DAYS if remember_me else REFRESH_TOKEN_EXPIRE_DAYS
    expires_at = datetime.now(timezone.utc) + timedelta(days=expire_days)

    refresh_token = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh_token_value),
        expires_at=expires_at,
        is_remember_me=remember_me,
        device_info=device_info,
        ip_address=ip_address,
    )
    db.add(refresh_token)
    await db.commit()

    return access_token, refresh_token_value


async def refresh_access_token(
    db: AsyncSession,
    refresh_token_value: str,
) -> tuple[str, str] | None:
    """
    Refresh access token using a valid refresh token.
    Returns new access token and new refresh token (token rotation).
    """
    token_hash = hash_token(refresh_token_value)

    # Find the refresh token
    result = await db.execute(
        select(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .options(selectinload(RefreshToken.user).selectinload(User.role).selectinload(Role.permissions))
    )
    refresh_token = result.scalar_one_or_none()

    if not refresh_token or not refresh_token.is_valid:
        return None

    user = refresh_token.user
    if not user or not user.is_active:
        return None

    # Revoke the old refresh token (rotation)
    refresh_token.revoked_at = datetime.now(timezone.utc)

    # Create new tokens
    permissions = [p.code for p in user.role.permissions] if user.role else []
    role_name = user.role.name if user.role else None
    new_access_token = create_access_token(user.id, user.username, permissions, role_name)

    # Create new refresh token
    new_refresh_token_value = create_refresh_token_value()
    expire_days = (
        REFRESH_TOKEN_REMEMBER_ME_DAYS if refresh_token.is_remember_me else REFRESH_TOKEN_EXPIRE_DAYS
    )
    expires_at = datetime.now(timezone.utc) + timedelta(days=expire_days)

    new_refresh_token = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(new_refresh_token_value),
        expires_at=expires_at,
        is_remember_me=refresh_token.is_remember_me,
        device_info=refresh_token.device_info,
        ip_address=refresh_token.ip_address,
    )
    db.add(new_refresh_token)
    await db.commit()

    return new_access_token, new_refresh_token_value


async def revoke_refresh_token(db: AsyncSession, refresh_token_value: str) -> bool:
    """Revoke a specific refresh token."""
    token_hash = hash_token(refresh_token_value)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    refresh_token = result.scalar_one_or_none()

    if not refresh_token:
        return False

    refresh_token.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    return True


async def revoke_all_user_tokens(db: AsyncSession, user_id: int) -> int:
    """Revoke all refresh tokens for a user. Returns count of revoked tokens."""
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    tokens = result.scalars().all()
    now = datetime.now(timezone.utc)
    for token in tokens:
        token.revoked_at = now
    await db.commit()
    return len(tokens)


async def get_active_sessions(db: AsyncSession, user_id: int) -> list[RefreshToken]:
    """Get all active refresh tokens (sessions) for a user."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        ).order_by(RefreshToken.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_session(db: AsyncSession, user_id: int, session_id: int) -> bool:
    """Revoke a specific session (refresh token) by ID."""
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.id == session_id,
            RefreshToken.user_id == user_id,
        )
    )
    token = result.scalar_one_or_none()
    if not token:
        return False

    token.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    return True


# ===== Login History =====

def parse_user_agent(user_agent: str | None) -> dict:
    """Parse user agent string to extract device info."""
    if not user_agent:
        return {"device_type": None, "browser": None, "os": None}

    # Simple parsing - can be enhanced with user-agents library
    device_type = "desktop"
    if "Mobile" in user_agent or "Android" in user_agent:
        device_type = "mobile"
    elif "Tablet" in user_agent or "iPad" in user_agent:
        device_type = "tablet"

    browser = "Unknown"
    if "Firefox" in user_agent:
        browser = "Firefox"
    elif "Chrome" in user_agent:
        browser = "Chrome"
    elif "Safari" in user_agent:
        browser = "Safari"
    elif "Edge" in user_agent:
        browser = "Edge"

    os_name = "Unknown"
    if "Windows" in user_agent:
        os_name = "Windows"
    elif "Mac OS" in user_agent:
        os_name = "macOS"
    elif "Linux" in user_agent:
        os_name = "Linux"
    elif "Android" in user_agent:
        os_name = "Android"
    elif "iOS" in user_agent or "iPhone" in user_agent:
        os_name = "iOS"

    return {"device_type": device_type, "browser": browser, "os": os_name}


async def record_login_attempt(
    db: AsyncSession,
    username: str,
    success: bool,
    user_id: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    failure_reason: str | None = None,
) -> LoginHistory:
    """Record a login attempt."""
    ua_info = parse_user_agent(user_agent)

    history = LoginHistory(
        user_id=user_id,
        username_attempted=username,
        ip_address=ip_address,
        user_agent=user_agent,
        device_type=ua_info["device_type"],
        browser=ua_info["browser"],
        os=ua_info["os"],
        success=success,
        failure_reason=failure_reason,
    )
    db.add(history)
    await db.commit()
    return history


async def get_login_history(
    db: AsyncSession,
    user_id: int,
    limit: int = 50,
    offset: int = 0,
) -> list[LoginHistory]:
    """Get login history for a user."""
    result = await db.execute(
        select(LoginHistory)
        .where(LoginHistory.user_id == user_id)
        .order_by(LoginHistory.login_time.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def update_last_login(db: AsyncSession, user: User) -> None:
    """Update user's last login timestamp."""
    user.last_login = datetime.now(timezone.utc)
    await db.commit()


# ===== Security Questions =====

async def set_security_question(
    db: AsyncSession,
    user_id: int,
    question: str,
    answer: str,
) -> bool:
    """Set security question and answer for a user."""
    user = await get_user_by_id(db, user_id)
    if not user:
        return False

    user.security_question = question
    user.security_answer_hash = hash_password(answer.lower().strip())
    await db.commit()
    return True


async def verify_security_answer(
    db: AsyncSession,
    user_id: int,
    answer: str,
) -> bool:
    """Verify the security question answer."""
    user = await get_user_by_id(db, user_id)
    if not user or not user.security_answer_hash:
        return False

    return verify_password(answer.lower().strip(), user.security_answer_hash)


async def get_security_question(db: AsyncSession, username: str) -> str | None:
    """Get the security question for a user (for password reset)."""
    user = await get_user_by_username(db, username)
    if not user:
        return None
    return user.security_question


# ===== Password Reset =====

async def create_password_reset_token(
    db: AsyncSession,
    user_id: int,
    reset_method: str = "security_question",
) -> str:
    """Create a password reset token."""
    # Invalidate any existing reset tokens for this user
    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
        )
    )
    existing_tokens = result.scalars().all()
    for token in existing_tokens:
        token.used_at = datetime.now(timezone.utc)

    # Create new token
    token_value = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)

    reset_token = PasswordResetToken(
        user_id=user_id,
        token_hash=hash_token(token_value),
        expires_at=expires_at,
        reset_method=reset_method,
    )
    db.add(reset_token)
    await db.commit()

    return token_value


async def reset_password_with_token(
    db: AsyncSession,
    token_value: str,
    new_password: str,
) -> bool:
    """Reset password using a valid reset token."""
    token_hash = hash_token(token_value)

    result = await db.execute(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == token_hash)
        .options(selectinload(PasswordResetToken.user))
    )
    reset_token = result.scalar_one_or_none()

    if not reset_token or not reset_token.is_valid:
        return False

    user = reset_token.user
    if not user:
        return False

    # Update password
    user.password_hash = hash_password(new_password)
    reset_token.used_at = datetime.now(timezone.utc)

    # Revoke all refresh tokens for security
    await revoke_all_user_tokens(db, user.id)

    await db.commit()
    return True


async def change_password(
    db: AsyncSession,
    user_id: int,
    current_password: str,
    new_password: str,
) -> bool:
    """Change user password (requires current password)."""
    user = await get_user_by_id(db, user_id)
    if not user:
        return False

    if not verify_password(current_password, user.password_hash):
        return False

    user.password_hash = hash_password(new_password)
    await db.commit()
    return True


async def admin_reset_password(
    db: AsyncSession,
    user_id: int,
    new_password: str,
) -> bool:
    """Admin-initiated password reset (no current password required)."""
    user = await get_user_by_id(db, user_id)
    if not user:
        return False

    user.password_hash = hash_password(new_password)

    # Revoke all refresh tokens for security
    await revoke_all_user_tokens(db, user.id)

    await db.commit()
    return True
