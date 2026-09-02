"""
Unit tests for the authorization layer.
Source: app/services/permission

Every protected endpoint in the application routes through require_permission
or require_any_permission, and both end up in has_permission — a hand-written
wildcard matcher with no test coverage at all. A mistake here does not raise:
it either denies something that should work (visible, someone reports it) or
grants something that should not (invisible, nobody reports it).

The inheritance walk matters for the same reason. Permissions are resolved from
the database on every request rather than read from the token, which is what
makes deactivating a role take effect immediately instead of at token expiry;
these tests pin that too.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pytest_mock import MockerFixture

from app.services import permission as perm
from app.services.permission import (
    CurrentUser,
    PermissionCode,
    has_permission,
    require_any_permission,
    require_permission,
)


def _role(role_id: int, *codes: str, parent_id: int | None = None, is_active: bool = True):
    return SimpleNamespace(
        id=role_id,
        name=f"role{role_id}",
        permissions=[SimpleNamespace(code=c) for c in codes],
        parent_role_id=parent_id,
        is_active=is_active,
    )


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class RoleSession:
    """Answers Role-by-primary-key lookups, the way the parent walk asks.

    Reading the bound parameter rather than replaying a fixed queue is what
    makes the test able to fail: a walk that asked for the wrong id, or asked
    twice for the same one, would come back with the wrong role instead of
    quietly getting the next item in line.
    """

    def __init__(self, *roles, user=None):
        self.by_id = {r.id: r for r in roles}
        self.user = user
        self.queries: list[int] = []

    async def execute(self, statement, *_args, **_kwargs):
        key = next(iter(statement.compile().params.values()), None)
        if "FROM users" in str(statement):
            return _Result(self.user)
        # Only role lookups are recorded — the count is what proves the walk
        # stops instead of querying its way around a cycle.
        self.queries.append(key)
        return _Result(self.by_id.get(key))


class TestWildcardMatching:
    """has_permission — the matcher every authorization decision passes through."""

    @pytest.mark.unit
    def test_tc_prm_001_exact_match_grants(self):
        """TC-PRM-001: has_permission — the ordinary case."""
        assert has_permission({"task:create"}, "task:create")

    @pytest.mark.unit
    def test_tc_prm_002_absent_permission_denies(self):
        """TC-PRM-002: has_permission — an unrelated permission grants nothing."""
        assert not has_permission({"task:view"}, "task:create")

    @pytest.mark.unit
    def test_tc_prm_003_empty_set_denies_everything(self):
        """TC-PRM-003: has_permission — no permissions means no access.

        get_user_permissions returns an empty set for a deactivated role, so
        this is the code path a disabled account actually takes.
        """
        for code in ("task:create", "user:manage", "*:*", "anything"):
            assert not has_permission(set(), code)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "required", ["task:create", "user:manage", "audit:view", "role:manage"]
    )
    def test_tc_prm_004_superadmin_wildcard_grants_all(self, required):
        """TC-PRM-004: has_permission — "*:*" is the superadmin grant."""
        assert has_permission({"*:*"}, required)

    @pytest.mark.unit
    def test_tc_prm_005_resource_wildcard_is_scoped_to_its_resource(self):
        """TC-PRM-005: has_permission — "task:*" does not leak into other resources.

        This is the one that would be silent: a role meant to grant full task
        control would also be handing out user administration.
        """
        held = {"task:*"}
        assert has_permission(held, "task:create")
        assert has_permission(held, "task:delete")
        assert not has_permission(held, "user:manage")
        assert not has_permission(held, "role:manage")

    @pytest.mark.unit
    def test_tc_prm_006_action_wildcard_is_scoped_to_its_action(self):
        """TC-PRM-006: has_permission — "*:view" grants reading, not writing."""
        held = {"*:view"}
        assert has_permission(held, "task:view")
        assert has_permission(held, "audit:view")
        assert not has_permission(held, "task:create")
        assert not has_permission(held, "user:manage")

    @pytest.mark.unit
    def test_tc_prm_007_a_required_wildcard_is_not_satisfied_by_a_concrete_grant(self):
        """TC-PRM-007: has_permission — requiring "task:*" needs the wildcard itself.

        The matcher is deliberately asymmetric. If holding task:view satisfied
        a requirement of task:*, any single task permission would unlock every
        endpoint guarded by the wildcard.
        """
        assert not has_permission({"task:view", "task:create"}, "task:*")
        assert not has_permission({"task:view"}, "*:*")

    @pytest.mark.unit
    def test_tc_prm_008_a_code_without_a_colon_only_matches_exactly(self):
        """TC-PRM-008: has_permission — an unparseable code cannot match a wildcard.

        Splitting on ":" is what produces resource and action; without one there
        is nothing to widen, and falling through to a wildcard would grant on a
        typo.
        """
        assert has_permission({"admin"}, "admin")
        assert not has_permission({"task:*"}, "admin")
        assert not has_permission({"*:view"}, "view")

    @pytest.mark.unit
    def test_tc_prm_009_only_the_first_colon_splits(self):
        """TC-PRM-009: has_permission — a multi-colon code keeps its tail in the action."""
        assert has_permission({"task:*"}, "task:view:own")
        assert not has_permission({"task:view"}, "task:view:own")


class TestRoleInheritance:
    """resolve_role_permissions — the parent chain walk."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_020_direct_permissions_are_returned(self):
        """TC-PRM-020: inheritance — a role with no parent yields its own set.

        The default setup has no role hierarchy at all, so this path must cost
        no extra query.
        """
        role = _role(1, "task:view")
        db = RoleSession(role)
        assert await perm.resolve_role_permissions(db, role) == {"task:view"}
        assert db.queries == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_021_parent_permissions_are_inherited(self):
        """TC-PRM-021: inheritance — a child holds the union with its parent."""
        parent = _role(1, "task:view")
        child = _role(2, "task:create", parent_id=1)
        db = RoleSession(parent, child)
        assert await perm.resolve_role_permissions(db, child) == {"task:view", "task:create"}
        assert db.queries == [1]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_022_inheritance_is_one_directional(self):
        """TC-PRM-022: inheritance — the parent does not gain the child's permissions."""
        parent = _role(1, "task:view")
        child = _role(2, "user:manage", parent_id=1)
        db = RoleSession(parent, child)
        assert await perm.resolve_role_permissions(db, parent) == {"task:view"}

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_023_a_deactivated_parent_contributes_nothing(self):
        """TC-PRM-023: inheritance — deactivating a role revokes what it lends out.

        Otherwise disabling a parent role would leave every child still holding
        everything it granted, with the admin UI showing the parent as off.
        """
        parent = _role(1, "user:manage", is_active=False)
        child = _role(2, "task:view", parent_id=1)
        db = RoleSession(parent, child)
        assert await perm.resolve_role_permissions(db, child) == {"task:view"}

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_024_a_deactivated_ancestor_cuts_the_chain_below_it(self):
        """TC-PRM-024: inheritance — the walk stops at the first inactive ancestor."""
        grandparent = _role(1, "audit:view", is_active=False)
        parent = _role(2, "user:manage", parent_id=1)
        child = _role(3, "task:view", parent_id=2)
        db = RoleSession(grandparent, parent, child)
        assert await perm.resolve_role_permissions(db, child) == {"task:view", "user:manage"}

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_025_a_deactivated_role_grants_nothing_at_all(self):
        """TC-PRM-025: inheritance — an inactive role is a full stop, not a skip."""
        parent = _role(1, "audit:view")
        role = _role(2, "task:view", parent_id=1, is_active=False)
        db = RoleSession(parent, role)
        assert await perm.resolve_role_permissions(db, role) == set()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_026_a_missing_parent_ends_the_walk(self):
        """TC-PRM-026: inheritance — a dangling parent_role_id does not raise.

        The FK is ON DELETE SET NULL, but a row read between the delete and the
        cascade still carries the old id.
        """
        child = _role(2, "task:view", parent_id=99)
        db = RoleSession(child)
        assert await perm.resolve_role_permissions(db, child) == {"task:view"}

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_027_no_role_holds_nothing(self):
        """TC-PRM-027: inheritance — role_id is nullable."""
        assert await perm.resolve_role_permissions(RoleSession(), None) == set()


class TestInheritanceDepth:
    """The depth limit that used to exist, and the two ways it showed up.

    Permissions used to be gathered by recursing through Role.parent, which
    needed the whole ancestry eager-loaded in advance. get_user_permissions
    loaded three levels and get_current_user loaded two, so a role chain deeper
    than that reached an unloaded relationship and the lazy load raised
    MissingGreenlet inside the async session — an unconditional 500 for every
    request from anyone holding that role, traced back to nothing in
    particular. Verified against the real database before this was rewritten.

    Nothing in the role API caps the depth, so an admin could build one from
    the UI without ever being warned.
    """

    @staticmethod
    def _chain(depth: int):
        """Build role 1 → 2 → ... → depth, each granting one distinct permission."""
        roles = [
            _role(n, f"res{n}:view", parent_id=(n - 1) if n > 1 else None)
            for n in range(1, depth + 1)
        ]
        return roles, roles[-1]

    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.parametrize("depth", [3, 4, 6, 10])
    async def test_tc_prm_030_any_depth_resolves(self, depth):
        """TC-PRM-030: inheritance — depth is no longer bounded by an eager load."""
        roles, deepest = self._chain(depth)
        db = RoleSession(*roles)

        got = await perm.resolve_role_permissions(db, deepest)

        assert got == {f"res{n}:view" for n in range(1, depth + 1)}
        assert db.queries == list(range(depth - 1, 0, -1))

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_031_a_cycle_terminates(self):
        """TC-PRM-031: inheritance — a parent loop returns instead of looping forever.

        Nothing rejects setting A's parent to B while B's parent is A, and this
        runs on every authenticated request — a loop here holds the worker, not
        just one endpoint.
        """
        a = _role(1, "task:view", parent_id=2)
        b = _role(2, "user:manage", parent_id=1)
        db = RoleSession(a, b)

        assert await perm.resolve_role_permissions(db, a) == {"task:view", "user:manage"}
        assert db.queries == [2]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_032_self_parent_terminates(self):
        """TC-PRM-032: inheritance — a role that is its own parent issues no query."""
        a = _role(1, "task:view", parent_id=1)
        db = RoleSession(a)
        assert await perm.resolve_role_permissions(db, a) == {"task:view"}
        assert db.queries == []


class TestCurrentUser:
    """The token-derived view, used by handlers for their own checks."""

    @pytest.mark.unit
    def test_tc_prm_040_has_permission_honours_wildcards(self):
        """TC-PRM-040: CurrentUser — the same matcher as the dependencies.

        update_user_role's escalation guard calls this; if it were an exact
        comparison, a role:manage holder carrying "*:*" would be refused.
        """
        user = CurrentUser(id=1, username="admin", permissions=["*:*"], role="admin")
        assert user.has_permission("user:manage")
        assert user.has_permission(PermissionCode.ROLE_MANAGE)

    @pytest.mark.unit
    def test_tc_prm_041_has_any_permission_needs_only_one(self):
        """TC-PRM-041: CurrentUser — any-of semantics."""
        user = CurrentUser(id=1, username="bob", permissions=["task:view"], role="viewer")
        assert user.has_any_permission("task:view", "task:create")
        assert not user.has_any_permission("user:manage", "role:manage")


class TestRequirePermission:
    """The FastAPI dependencies guarding every protected route."""

    @pytest.fixture
    def granted(self, mocker: MockerFixture):
        """Patch the database lookup; the return value is what the request holds."""

        def _set(*codes: str):
            return mocker.patch.object(
                perm, "get_user_permissions", autospec=True, return_value=set(codes)
            )

        return _set

    @staticmethod
    def _user(*codes: str) -> CurrentUser:
        return CurrentUser(id=1, username="bob", permissions=list(codes), role="viewer")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_060_all_required_permissions_must_be_held(self, granted):
        """TC-PRM-060: require_permission — multiple arguments mean AND, not OR."""
        granted("user:view")
        checker = require_permission("user:view", "user:manage")

        with pytest.raises(HTTPException) as exc:
            await checker(current_user=self._user(), db=object())

        assert exc.value.status_code == 403
        assert "user:manage" in exc.value.detail
        assert "user:view" not in exc.value.detail

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_061_holding_everything_passes(self, granted):
        """TC-PRM-061: require_permission — the allow path returns None, not a value."""
        granted("user:view", "user:manage")
        checker = require_permission("user:view", "user:manage")
        assert await checker(current_user=self._user(), db=object()) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_062_enum_arguments_are_accepted(self, granted):
        """TC-PRM-062: require_permission — PermissionCode members work as strings.

        Routes mix both forms; an enum reaching the matcher unconverted would
        compare its repr against the permission code and deny everyone.
        """
        granted("task:create")
        checker = require_permission(PermissionCode.TASK_CREATE)
        assert await checker(current_user=self._user(), db=object()) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_063_wildcards_satisfy_the_requirement(self, granted):
        """TC-PRM-063: require_permission — an admin's "*:*" opens the guarded route."""
        granted("*:*")
        checker = require_permission("user:manage", "role:manage")
        assert await checker(current_user=self._user(), db=object()) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_064_the_database_decides_not_the_token(self, granted):
        """TC-PRM-064: require_permission — claims carried by the token are ignored.

        The check re-reads permissions per request, which is the only reason
        revoking a role takes effect before the access token expires. Trusting
        current_user.permissions instead would leave a demoted user fully
        privileged for the rest of the token's lifetime.
        """
        granted("task:view")
        checker = require_permission("user:manage")

        with pytest.raises(HTTPException) as exc:
            await checker(current_user=self._user("*:*", "user:manage"), db=object())

        assert exc.value.status_code == 403

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_070_any_permission_needs_one_of_them(self, granted):
        """TC-PRM-070: require_any_permission — one match is enough."""
        granted("task:view_own")
        checker = require_any_permission("task:view_own", "task:view_all")
        assert await checker(current_user=self._user(), db=object()) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_071_any_permission_denies_when_none_match(self, granted):
        """TC-PRM-071: require_any_permission — the 403 lists every acceptable code."""
        granted("audit:view")
        checker = require_any_permission("task:view_own", "task:view_all")

        with pytest.raises(HTTPException) as exc:
            await checker(current_user=self._user(), db=object())

        assert exc.value.status_code == 403
        assert "task:view_own" in exc.value.detail
        assert "task:view_all" in exc.value.detail


class TestGetUserPermissions:
    """The single database lookup behind every authorization decision."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_080_missing_user_holds_nothing(self):
        """TC-PRM-080: get_user_permissions — a deleted account resolves to empty.

        The token outlives the row it refers to, so this is reachable with a
        perfectly valid signature.
        """
        assert await perm.get_user_permissions(RoleSession(user=None), 7) == set()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_081_a_user_without_a_role_holds_nothing(self):
        """TC-PRM-081: get_user_permissions — role_id is nullable."""
        user = SimpleNamespace(id=7, role=None)
        assert await perm.get_user_permissions(RoleSession(user=user), 7) == set()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_082_a_deactivated_role_revokes_immediately(self):
        """TC-PRM-082: get_user_permissions — an inactive role grants nothing at all.

        Not merely its own permissions: the whole chain is dropped, so
        deactivating a role cannot be worked around by what it inherited.
        """
        parent = _role(1, "audit:view")
        role = _role(2, "task:view", parent_id=1, is_active=False)
        db = RoleSession(parent, role, user=SimpleNamespace(id=7, role=role))
        assert await perm.get_user_permissions(db, 7) == set()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_083_an_active_role_resolves_with_inheritance(self):
        """TC-PRM-083: get_user_permissions — the ordinary path returns the union."""
        parent = _role(1, "audit:view")
        role = _role(2, "task:view", parent_id=1)
        db = RoleSession(parent, role, user=SimpleNamespace(id=7, role=role))
        assert await perm.get_user_permissions(db, 7) == {"task:view", "audit:view"}

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_prm_084_check_permission_applies_the_wildcard_matcher(self):
        """TC-PRM-084: check_permission — the convenience wrapper is not an exact match."""
        role = _role(2, "task:*")
        db = RoleSession(role, user=SimpleNamespace(id=7, role=role))
        assert await perm.check_permission(db, 7, "task:create")
        assert await perm.check_permission(db, 7, PermissionCode.TASK_CREATE)
        assert not await perm.check_permission(db, 7, "user:manage")
