"""
Unit tests for short-lived artifact download tickets.
Source: app/services/auth.py

A ticket travels in the URL, so its whole safety argument rests on being
useless for anything but one artifact for two minutes. Each property below is
part of that argument.
"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.services.auth import (
    ALGORITHM,
    DOWNLOAD_TICKET_EXPIRE_SECONDS,
    SECRET_KEY,
    create_access_token,
    create_download_ticket,
    decode_download_ticket,
)

USER_ID = 42
TASK_ID = "7f3c1a90-0000-4000-8000-000000000001"


class TestCreateDownloadTicket:
    """Tests for create_download_ticket."""

    @pytest.mark.unit
    def test_tc_dlt_001_round_trips_user_and_task(self):
        """TC-DLT-001: create/decode — a fresh ticket returns its (user, task) pair."""
        assert decode_download_ticket(create_download_ticket(USER_ID, TASK_ID)) == (
            USER_ID,
            TASK_ID,
        )

    @pytest.mark.unit
    def test_tc_dlt_002_ticket_is_scoped_to_one_task(self):
        """TC-DLT-002: create_download_ticket — the task id is bound into the ticket."""
        _, task_id = decode_download_ticket(create_download_ticket(USER_ID, "other-task"))
        assert task_id == "other-task"

    @pytest.mark.unit
    def test_tc_dlt_003_ttl_outlives_a_large_download(self):
        """TC-DLT-003: create_download_ticket — the TTL can cover a real transfer.

        The ticket has to survive the download it authorises, not just the click
        that starts it. Native downloads resume by re-requesting the same URL, and
        a multi-GB artifact takes minutes even on a fast link — an earlier 120s
        value expired mid-transfer and the retry came back 401, surfacing in
        Chrome as "Needs authorization" with nothing pointing at a timer.

        Still bounded: this is a URL-borne credential, so it must not become a
        lasting capability sitting in an access log.
        """
        payload = jwt.decode(
            create_download_ticket(USER_ID, TASK_ID), SECRET_KEY, algorithms=[ALGORITHM]
        )
        remaining = payload["exp"] - datetime.now(timezone.utc).timestamp()
        assert 0 < remaining <= DOWNLOAD_TICKET_EXPIRE_SECONDS
        # 2 GB at 100 Mbps is ~2.7 minutes; a slow link or a paused download
        # needs considerably more headroom than that.
        assert DOWNLOAD_TICKET_EXPIRE_SECONDS >= 900
        assert DOWNLOAD_TICKET_EXPIRE_SECONDS <= 3600


class TestDecodeDownloadTicket:
    """Tests for decode_download_ticket — the redemption gate."""

    @pytest.mark.unit
    def test_tc_dlt_010_rejects_access_token(self):
        """TC-DLT-010: decode_download_ticket — a session access token is NOT a ticket.

        Both are signed with the same key, so only the typ claim separates
        them. Without this check the narrow scope would be decorative.
        """
        access = create_access_token(
            user_id=USER_ID, username="tony", permissions=[], role_name="admin"
        )
        assert decode_download_ticket(access) is None

    @pytest.mark.unit
    def test_tc_dlt_011_rejects_expired_ticket(self):
        """TC-DLT-011: decode_download_ticket — an expired ticket is refused."""
        expired = jwt.encode(
            {
                "sub": str(USER_ID),
                "tid": TASK_ID,
                "typ": "download",
                "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
            },
            SECRET_KEY,
            algorithm=ALGORITHM,
        )
        assert decode_download_ticket(expired) is None

    @pytest.mark.unit
    def test_tc_dlt_012_rejects_foreign_signature(self):
        """TC-DLT-012: decode_download_ticket — a ticket signed with another key is refused."""
        forged = jwt.encode(
            {
                "sub": str(USER_ID),
                "tid": TASK_ID,
                "typ": "download",
                "exp": datetime.now(timezone.utc) + timedelta(seconds=60),
            },
            "not-the-server-key-but-long-enough-to-avoid-a-length-warning",
            algorithm=ALGORITHM,
        )
        assert decode_download_ticket(forged) is None

    @pytest.mark.unit
    def test_tc_dlt_013_rejects_ticket_without_task(self):
        """TC-DLT-013: decode_download_ticket — a ticket with no task id is refused.

        A ticket that names no artifact would otherwise be a wildcard.
        """
        no_task = jwt.encode(
            {
                "sub": str(USER_ID),
                "typ": "download",
                "exp": datetime.now(timezone.utc) + timedelta(seconds=60),
            },
            SECRET_KEY,
            algorithm=ALGORITHM,
        )
        assert decode_download_ticket(no_task) is None

    @pytest.mark.unit
    def test_tc_dlt_014_rejects_non_numeric_subject(self):
        """TC-DLT-014: decode_download_ticket — a non-numeric subject is refused, not raised."""
        bad_sub = jwt.encode(
            {
                "sub": "admin",
                "tid": TASK_ID,
                "typ": "download",
                "exp": datetime.now(timezone.utc) + timedelta(seconds=60),
            },
            SECRET_KEY,
            algorithm=ALGORITHM,
        )
        assert decode_download_ticket(bad_sub) is None

    @pytest.mark.unit
    @pytest.mark.parametrize("ticket", ["", "not-a-jwt", "a.b.c"])
    def test_tc_dlt_015_rejects_malformed_input(self, ticket):
        """TC-DLT-015: decode_download_ticket — garbage yields None rather than an exception."""
        assert decode_download_ticket(ticket) is None
