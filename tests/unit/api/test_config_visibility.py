"""
Unit tests for what a stored build config returns to the caller.
Source: app/api/routes/tasks._config_for

Env values are returned as entered, on purpose. They were masked for everyone
but the owner for a while, and it cost more than it bought: reading how a
working build is configured is the main reason to open someone else's record
on a shared build platform, and key names alone do not tell you what to put in
yours. It bought little because no role can read another user's record without
also holding task:create — 'user' is limited to history:view_own and 'admin'
is *:* — so anyone who could see a masked config could equally well submit a
build that reads the database directly.

These tests pin that decision so it is not quietly reintroduced, and pin the
tolerance that keeps one unreadable record from failing a whole page.
"""

from datetime import datetime, timezone

import pytest

from app.api.routes.tasks import _config_for
from app.schemas.task import BuildConfig

OWNER = "tonyhuang"
SECRET_ENV = 'DATABASE_URL=postgres://u:pw@db/app\nAPI_KEY="sk-live-1234"'


def _raw_config(**overrides) -> dict:
    return BuildConfig(
        project_path="/media/disk0/x",
        docker_env_vars=SECRET_ENV,
        frontend_env_content="VITE_TOKEN=abc123",
        **overrides,
    ).model_dump()


class TestConfigsAreReturnedAsEntered:
    @pytest.mark.unit
    def test_tc_cfv_001_env_values_are_not_masked(self):
        """TC-CFV-001: _config_for — the values come back exactly as submitted.

        An engineer copying a working setup needs the values; "DB_URL=<hidden>"
        tells them a variable exists, which they could already guess.
        """
        config = _config_for(_raw_config())

        assert config.docker_env_vars == SECRET_ENV
        assert config.frontend_env_content == "VITE_TOKEN=abc123"

    @pytest.mark.unit
    def test_tc_cfv_002_nothing_in_the_response_is_a_placeholder(self):
        """TC-CFV-002: _config_for — no field carries a mask marker.

        Broader than the two env fields: a mask reintroduced anywhere in this
        path would fail here.
        """
        assert "<hidden>" not in _config_for(_raw_config()).model_dump_json()

    @pytest.mark.unit
    def test_tc_cfv_003_the_result_is_submittable_again(self):
        """TC-CFV-003: _config_for — what comes back can be rebuilt from.

        TaskDetail's rebuild button prefills the next form from this response.
        A masked value there became the literal string "<hidden>" in the next
        build's config — a build that compiles and dies on its first
        connection attempt. One history record on this installation is exactly
        that.
        """
        returned = _config_for(_raw_config())

        resubmitted = BuildConfig.model_validate(returned.model_dump())

        assert resubmitted.docker_env_vars == SECRET_ENV

    @pytest.mark.unit
    def test_tc_cfv_004_the_caller_does_not_affect_the_result(self):
        """TC-CFV-004: _config_for — visibility is not a function of who asks.

        The signature no longer takes a viewer at all, which is the point:
        access is decided by the permission check on the endpoint, not by
        rewriting the payload afterwards.
        """
        import inspect

        params = inspect.signature(_config_for).parameters

        assert list(params) == ["raw_config"]


class TestAnUnreadableRecordDoesNotHideTheRest:
    """A stored config records what already ran; the schema moves on without it.

    _config_for validates every row the history list returns, so a config the
    current schema rejects used to fail the whole request. Tightening
    frontend_output_dir made 73 of 698 records undeserialisable and the history
    page went blank for everyone who owned one.
    """

    @pytest.mark.unit
    def test_tc_cfv_010_a_rejected_config_yields_none_not_an_exception(self):
        """TC-CFV-010: _config_for — the record still lists, without its config."""
        stored = _raw_config()
        stored["frontend_output_dir"] = "../../etc"

        assert _config_for(stored) is None

    @pytest.mark.unit
    def test_tc_cfv_011_a_readable_record_is_unaffected(self):
        """TC-CFV-011: _config_for — tolerance does not swallow good rows."""
        assert _config_for(_raw_config()) is not None

    @pytest.mark.unit
    def test_tc_cfv_012_a_wholly_malformed_config_is_survivable(self):
        """TC-CFV-012: _config_for — anything unparseable degrades the same way."""
        assert _config_for({"project_type": "not-a-real-type"}) is None

    @pytest.mark.unit
    def test_tc_cfv_013_a_missing_config_stays_none(self):
        """TC-CFV-013: _config_for — an old row without a config is not an error."""
        assert _config_for(None) is None

    @pytest.mark.unit
    def test_tc_cfv_014_the_failure_is_logged(self, mocker):
        """TC-CFV-014: _config_for — silently dropping a config would hide the cause.

        The record listing without its config is the visible symptom; the log
        line is the only thing that says which row and why.
        """
        from app.api.routes import tasks as tasks_route

        warn = mocker.patch.object(tasks_route.logger, "warning")
        stored = _raw_config()
        stored["frontend_output_dir"] = "../../etc"

        _config_for(stored)

        warn.assert_called_once()
