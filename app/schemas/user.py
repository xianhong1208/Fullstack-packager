"""User schemas for authentication."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.role import RoleSummary


class UserBase(BaseModel):
    """Base user schema."""

    username: str


# Shared so the four places that set a password cannot drift apart. Registration
# previously had no limit at all while change/reset/admin-reset all required 6,
# which meant the very first account — auto-promoted to admin, activated and
# logged in immediately — could be created with a one-character password and was
# never prompted to change it.
MIN_PASSWORD_LENGTH = 6


class UserCreate(BaseModel):
    """Schema for user registration."""

    # username is the ownership key compared against task.user_name throughout
    # the task routes, so blank or whitespace-only values must not be creatable.
    username: str = Field(min_length=1, max_length=64, pattern=r"^\S(.*\S)?$")
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class UserLogin(BaseModel):
    """Schema for user login."""

    username: str
    password: str
    remember_me: bool = False


class UserResponse(BaseModel):
    """Schema for user response."""

    id: int
    username: str
    email: str | None = None
    is_active: bool = True
    created_at: datetime | None = None
    last_login: datetime | None = None
    role: RoleSummary | None = None
    has_security_question: bool = False

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    """Schema for user list item."""

    id: int
    username: str
    email: str | None = None
    is_active: bool
    created_at: datetime
    last_login: datetime | None = None
    role: RoleSummary | None = None

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    """Schema for JWT token response."""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token expiration in seconds")
    user: UserResponse
    permissions: list[str] = []


class RefreshTokenRequest(BaseModel):
    """Schema for token refresh request."""

    refresh_token: str


class RefreshTokenResponse(BaseModel):
    """Schema for token refresh response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


# ===== Security Settings =====


class SecurityQuestionSet(BaseModel):
    """Schema for setting security question."""

    question: str = Field(min_length=10, max_length=500)
    answer: str = Field(min_length=1, max_length=255)


class SecurityQuestionVerify(BaseModel):
    """Schema for verifying security question answer."""

    username: str
    answer: str


class PasswordChange(BaseModel):
    """Schema for changing password."""

    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class PasswordReset(BaseModel):
    """Schema for resetting password with token."""

    token: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class ForgotPasswordRequest(BaseModel):
    """Schema for initiating forgot password flow."""

    username: str


class ForgotPasswordResponse(BaseModel):
    """Response for forgot password request."""

    has_security_question: bool
    security_question: str | None = None
    message: str


# ===== Login History =====


class LoginHistoryResponse(BaseModel):
    """Schema for login history entry."""

    id: int
    login_time: datetime
    ip_address: str | None
    device_type: str | None
    browser: str | None
    os: str | None
    success: bool
    failure_reason: str | None = None

    model_config = {"from_attributes": True}


class LoginHistoryList(BaseModel):
    """Schema for paginated login history."""

    items: list[LoginHistoryResponse]
    total: int
    limit: int
    offset: int


# ===== Active Sessions =====


class SessionResponse(BaseModel):
    """Schema for active session."""

    id: int
    created_at: datetime
    expires_at: datetime
    is_remember_me: bool
    device_info: str | None
    ip_address: str | None
    is_current: bool = False

    model_config = {"from_attributes": True}


class SessionList(BaseModel):
    """Schema for list of active sessions."""

    sessions: list[SessionResponse]
    total: int


# ===== User Management (Admin) =====


class UserRoleUpdate(BaseModel):
    """Schema for updating user role."""

    role_id: int


class UserStatusUpdate(BaseModel):
    """Schema for updating user status."""

    is_active: bool


class AdminPasswordReset(BaseModel):
    """Schema for admin password reset."""

    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)
