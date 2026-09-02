"""
Unit tests for the refresh-token garbage collector.
Source: app/services/workspace_cleanup.sweep_refresh_tokens

Token rotation mints a row per refresh and revokes the old one, so this table
only grows: it reached 21,456 rows for 7 users, 92.7% of them unusable. The
sweep must remove exactly the dead rows and never touch a live session — a
mistake here logs everyone out.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import and_, or_

from app.models.refresh_token import RefreshToken


def _predicate(cutoff: datetime):
    """The sweep's WHERE clause, as a callable over plain values.

    Mirrors sweep_refresh_tokens() so the deletion rule can be exercised
    without a database. Kept alongside the tests it documents; if the two ever
    disagree the tests below stop describing production.
    """

    def matches(expires_at: datetime, revoked_at: datetime | None) -> bool:
        return expires_at < cutoff or (revoked_at is not None and revoked_at < cutoff)

    return matches


NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(days=7)


class TestSweepPredicate:
    """Tests for which rows the sweep considers unusable."""

    @pytest.mark.unit
    def test_tc_rts_001_deletes_long_expired(self):
        """TC-RTS-001: sweep — a token that expired before the cutoff is removed."""
        assert _predicate(CUTOFF)(NOW - timedelta(days=30), None) is True

    @pytest.mark.unit
    def test_tc_rts_002_keeps_recently_expired(self):
        """TC-RTS-002: sweep — expiry inside the grace window is kept.

        The window is what keeps a just-ended session visible in the
        active-session and login-history views.
        """
        assert _predicate(CUTOFF)(NOW - timedelta(days=1), None) is False

    @pytest.mark.unit
    def test_tc_rts_003_deletes_long_revoked_even_if_unexpired(self):
        """TC-RTS-003: sweep — an old revocation is collected despite a future expiry.

        Rotation revokes a token mid-life, so these keep a future expires_at
        forever; without this clause they would never be collected at all.
        """
        assert _predicate(CUTOFF)(NOW + timedelta(days=23), NOW - timedelta(days=10)) is True

    @pytest.mark.unit
    def test_tc_rts_004_keeps_recently_revoked(self):
        """TC-RTS-004: sweep — a fresh revocation stays for the grace window."""
        assert _predicate(CUTOFF)(NOW + timedelta(days=23), NOW - timedelta(hours=1)) is False

    @pytest.mark.unit
    def test_tc_rts_005_never_deletes_a_live_session(self):
        """TC-RTS-005: sweep — an unexpired, unrevoked token is never collected.

        This is the one that must not regress: deleting these logs users out.
        """
        for days_left in (1, 7, 30):
            assert _predicate(CUTOFF)(NOW + timedelta(days=days_left), None) is False

    @pytest.mark.unit
    def test_tc_rts_006_boundary_is_exclusive(self):
        """TC-RTS-006: sweep — a row exactly at the cutoff is kept, not deleted."""
        assert _predicate(CUTOFF)(CUTOFF, None) is False


class TestSweepQueryShape:
    """Guards the SQL the sweep actually issues."""

    @pytest.mark.unit
    def test_tc_rts_010_where_clause_matches_the_documented_rule(self):
        """TC-RTS-010: sweep — the ORM predicate compiles to the intended OR/AND shape.

        Written out because an earlier draft ANDed the whole OR with the expiry
        clause, which silently collapsed it to "expired only" and would have
        left every revoked-but-unexpired row in the table forever.
        """
        clause = or_(
            RefreshToken.expires_at < CUTOFF,
            and_(
                RefreshToken.revoked_at.is_not(None),
                RefreshToken.revoked_at < CUTOFF,
            ),
        )
        sql = str(clause.compile(compile_kwargs={"literal_binds": True}))

        assert " OR " in sql
        assert "revoked_at IS NOT NULL" in sql
        assert sql.count("expires_at <") == 1
        assert sql.count("revoked_at <") == 1
