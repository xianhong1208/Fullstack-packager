"""
Unit tests for audit recording on the user- and role-administration endpoints.
Source: app/api/routes/users

Seven AuditAction members existed and nothing ever emitted them: promoting an
account, approving a registration, resetting someone's password and rewriting a
role's permissions all completed with no trace beyond the row's current value.
None of those columns carry history — users.updated_at is not touched by a role
change — so after the fact there was simply no way to answer who did it.

These tests assert the call reaches log_action with the before/after values,
and that a request rejected by the escalation guard records nothing (the guard
raises before the commit, so an entry there would be a lie).
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pytest_mock import MockerFixture

from app.api.routes import users as users_route
from app.schemas.role import RoleCreate, RoleUpdate
from app.schemas.user import AdminPasswordReset, UserRoleUpdate, UserStatusUpdate
from app.services.audit import AuditAction, AuditStatus, ResourceType
from app.models.permission import Permission
from app.services.permission import CurrentUser

ADMIN = "admin"


class _Result:
    """Stands in for a SQLAlchemy Result — every accessor yields the same value."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def scalar(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value


class FakeSession:
    """Replays a queued list of results, one per execute() call."""

    def __init__(self, *results):
        self._results = list(results)
        self.commits = 0
        self.deleted: list = []

    async def execute(self, *_args, **_kwargs):
        return _Result(self._results.pop(0))

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        return None

    def add(self, _obj):
        return None

    async def delete(self, obj):
        self.deleted.append(obj)


def _current(*permissions: str) -> CurrentUser:
    return CurrentUser(id=1, username=ADMIN, permissions=list(permissions), role="admin")


def _permission(code: str) -> Permission:
    """A real ORM instance — create_role assigns these to Role.permissions.

    A stand-in object is rejected by the relationship's backref event, which
    reaches for _sa_instance_state on whatever it is handed.
    """
    return Permission(id=1, code=code, name=code, description=None, category="x")


def _role(role_id: int = 5, *, name="viewer", permissions=(), parent=None, **kwargs):
    return SimpleNamespace(
        id=role_id,
        name=name,
        display_name=name.title(),
        description=None,
        created_at=datetime(2026, 1, 1),
        permissions=[_permission(c) for c in permissions],
        parent_role_id=parent,
        is_system=kwargs.get("is_system", False),
        is_active=kwargs.get("is_active", True),
    )


@pytest.fixture
def audit(mocker: MockerFixture):
    """Intercept log_action and hand back the kwargs it was called with."""
    return mocker.patch.object(users_route, "log_action", autospec=True)


def _kwargs(audit) -> dict:
    assert audit.await_count == 1, "expected exactly one audit entry"
    return audit.await_args.kwargs


class TestUserRoleChange:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_aud_001_role_assignment_records_both_ends(self, audit):
        """TC-AUD-001: update_user_role — the entry names the old and the new role.

        "role changed" alone cannot answer whether someone was promoted or
        demoted, which is the entire reason to look.
        """
        user = SimpleNamespace(id=7, username="bob", role_id=3)
        db = FakeSession(user, _role(9, permissions=("task:view",)))

        await users_route.update_user_role(
            user_id=7,
            data=UserRoleUpdate(role_id=9),
            current_user=_current("role:manage"),
            db=db,
        )

        kw = _kwargs(audit)
        assert kw["action"] is AuditAction.ROLE_ASSIGN
        assert kw["resource_type"] is ResourceType.USER
        assert kw["status"] is AuditStatus.SUCCESS
        assert kw["resource_id"] == 7
        assert kw["actor_name"] == ADMIN
        assert kw["details"]["from_role_id"] == 3
        assert kw["details"]["to_role_id"] == 9
        assert kw["details"]["target_user"] == "bob"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_aud_002_blocked_escalation_records_nothing(self, audit):
        """TC-AUD-002: update_user_role — a refused assignment writes no entry.

        The escalation guard raises before the commit, so the role was never
        changed. Logging it as SUCCESS would put a change in the audit trail
        that never happened; logging it at all here would need FAILURE status,
        and the caller already gets a 403.
        """
        user = SimpleNamespace(id=7, username="bob", role_id=3)
        db = FakeSession(user, _role(9, name="admin", permissions=("user:manage",)))

        with pytest.raises(HTTPException) as exc:
            await users_route.update_user_role(
                user_id=7,
                data=UserRoleUpdate(role_id=9),
                current_user=_current("task:view"),
                db=db,
            )

        assert exc.value.status_code == 403
        audit.assert_not_awaited()
        assert db.commits == 0
        assert user.role_id == 3


class TestUserStatusChange:
    """Activation is the approval gate the whole trust model rests on."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_aud_010_activation_uses_the_activate_action(self, audit):
        """TC-AUD-010: update_user_status — approving an account logs USER_ACTIVATE."""
        user = SimpleNamespace(id=7, username="bob", is_active=False)
        db = FakeSession(user)

        await users_route.update_user_status(
            user_id=7,
            data=UserStatusUpdate(is_active=True),
            current_user=_current("user:manage"),
            db=db,
        )

        kw = _kwargs(audit)
        assert kw["action"] is AuditAction.USER_ACTIVATE
        assert kw["details"]["from_active"] is False
        assert kw["details"]["to_active"] is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_aud_011_deactivation_uses_the_deactivate_action(self, audit):
        """TC-AUD-011: update_user_status — the two directions are distinguishable.

        A single USER_UPDATE action would force every reader to open details to
        learn whether access was granted or revoked.
        """
        user = SimpleNamespace(id=7, username="bob", is_active=True)
        db = FakeSession(user)

        await users_route.update_user_status(
            user_id=7,
            data=UserStatusUpdate(is_active=False),
            current_user=_current("user:manage"),
            db=db,
        )

        assert _kwargs(audit)["action"] is AuditAction.USER_DEACTIVATE


class TestAdminPasswordReset:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_aud_020_reset_is_recorded(self, audit, mocker: MockerFixture):
        """TC-AUD-020: reset_user_password — an admin-set credential leaves a trace.

        This is the one action that lets an operator take over another account
        outright, and it also kills that user's live sessions.
        """
        mocker.patch.object(users_route, "admin_reset_password", return_value=True)

        await users_route.reset_user_password(
            user_id=7,
            data=AdminPasswordReset(new_password="a-long-enough-password"),
            current_user=_current("user:reset_password"),
            db=FakeSession(),
        )

        kw = _kwargs(audit)
        assert kw["action"] is AuditAction.ADMIN_PASSWORD_RESET
        assert kw["resource_id"] == 7
        assert kw["actor_id"] == 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_aud_021_missing_user_records_nothing(self, audit, mocker: MockerFixture):
        """TC-AUD-021: reset_user_password — a 404 is not a password reset."""
        mocker.patch.object(users_route, "admin_reset_password", return_value=False)

        with pytest.raises(HTTPException):
            await users_route.reset_user_password(
                user_id=999,
                data=AdminPasswordReset(new_password="a-long-enough-password"),
                current_user=_current("user:reset_password"),
                db=FakeSession(),
            )

        audit.assert_not_awaited()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_aud_022_the_new_password_is_never_in_the_entry(
        self, audit, mocker: MockerFixture
    ):
        """TC-AUD-022: reset_user_password — details carries no credential.

        audit_logs is readable by anyone with audit:view and is exported to CSV;
        a plaintext password there would be worse than the gap this closes.
        """
        mocker.patch.object(users_route, "admin_reset_password", return_value=True)
        secret = "correct-horse-battery-staple"

        await users_route.reset_user_password(
            user_id=7,
            data=AdminPasswordReset(new_password=secret),
            current_user=_current("user:reset_password"),
            db=FakeSession(),
        )

        assert secret not in str(_kwargs(audit))


class TestRoleAdministration:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_aud_030_creation_records_the_granted_permissions(self, audit):
        """TC-AUD-030: create_role — the entry lists what the new role can do.

        Without it, a role created and then edited leaves no record of what it
        was originally granted.
        """
        created = _role(11, name="deployer", permissions=("task:create", "task:view"))
        db = FakeSession(None, [_permission("task:create")], created)

        await users_route.create_role(
            data=RoleCreate(
                name="deployer",
                display_name="Deployer",
                permission_codes=["task:create", "task:view"],
            ),
            current_user=_current("role:manage"),
            db=db,
        )

        kw = _kwargs(audit)
        assert kw["action"] is AuditAction.ROLE_CREATE
        assert kw["resource_type"] is ResourceType.ROLE
        assert kw["details"]["permissions"] == ["task:create", "task:view"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_aud_031_update_records_the_permission_diff(self, audit):
        """TC-AUD-031: update_role — added and removed permissions are both named.

        role.permissions is replaced wholesale, so the previous set has to be
        captured before the assignment; a plain "permissions changed" entry
        cannot answer the only question anyone asks it afterwards.
        """
        before = _role(11, name="deployer", permissions=("task:view", "task:delete"))
        after = _role(11, name="deployer", permissions=("task:view", "user:manage"))
        db = FakeSession(before, [_permission("task:view"), _permission("user:manage")], after)

        await users_route.update_role(
            role_id=11,
            data=RoleUpdate(permission_codes=["task:view", "user:manage"]),
            current_user=_current("role:manage"),
            db=db,
        )

        kw = _kwargs(audit)
        assert kw["action"] is AuditAction.ROLE_UPDATE
        assert kw["details"]["permissions_added"] == ["user:manage"]
        assert kw["details"]["permissions_removed"] == ["task:delete"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_aud_032_unchanged_permissions_produce_an_empty_diff(self, audit):
        """TC-AUD-032: update_role — renaming a role does not fabricate a grant."""
        role = _role(11, name="deployer", permissions=("task:view",))
        db = FakeSession(role, role)

        await users_route.update_role(
            role_id=11,
            data=RoleUpdate(display_name="Deploy Team"),
            current_user=_current("role:manage"),
            db=db,
        )

        kw = _kwargs(audit)
        assert kw["details"]["permissions_added"] == []
        assert kw["details"]["permissions_removed"] == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_aud_033_deletion_keeps_the_name(self, audit):
        """TC-AUD-033: delete_role — the row is gone, so the entry has to carry the name.

        resource_id alone points at a primary key that no longer resolves.
        """
        role = _role(11, name="deployer")
        db = FakeSession(role, 0)

        await users_route.delete_role(
            role_id=11,
            current_user=_current("role:manage"),
            db=db,
        )

        kw = _kwargs(audit)
        assert kw["action"] is AuditAction.ROLE_DELETE
        assert kw["details"]["role_name"] == "deployer"
        assert db.deleted == [role]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_aud_034_a_role_in_use_is_not_recorded_as_deleted(self, audit):
        """TC-AUD-034: delete_role — the in-use guard blocks the entry too."""
        db = FakeSession(_role(11, name="deployer"), 3)

        with pytest.raises(HTTPException):
            await users_route.delete_role(
                role_id=11,
                current_user=_current("role:manage"),
                db=db,
            )

        audit.assert_not_awaited()
        assert db.deleted == []
