"""
Unit tests for the environment handed to a smoke-tested binary.
Source: app/services/nuitka_worker._build_smoke_env

The binary is code the user supplied, and it used to be launched with the
service's entire os.environ. That is not the platform's configuration — it is
whatever happened to be in the shell that ran start.sh, which is unbounded:
an operator's API tokens, agent credentials, another project's settings. On
the machine this was found on, the running service carried an agent messaging
token inherited from the terminal that started it.

The application's own secrets were never in there, because pydantic-settings
reads .env directly. That is precisely why it looked harmless — and moving the
service to a systemd EnvironmentFile= would have put them there without
touching this file.

Nothing here fails loudly if it regresses: the binary starts either way.
"""

import pytest
from pytest_mock import MockerFixture

from app.services.nuitka_worker import _SMOKE_ENV_PASSTHROUGH, _build_smoke_env


@pytest.fixture
def host_env(mocker: MockerFixture):
    """Replace os.environ wholesale so the real one cannot influence a result."""

    def _set(**values: str):
        mocker.patch.dict("app.services.nuitka_worker.os.environ", values, clear=True)

    return _set


class TestSecretsAreNotForwarded:
    @pytest.mark.unit
    def test_tc_smk_001_unlisted_host_variables_are_dropped(self, host_env):
        """TC-SMK-001: _build_smoke_env — the operator's shell does not reach the binary."""
        host_env(
            PATH="/usr/bin",
            CLAUDE_CODE_MESSAGING_TOKEN="agent-secret",
            AWS_SECRET_ACCESS_KEY="aws-secret",
            GITLAB_TOKEN="glpat-xxxx",
            JWT_SECRET_KEY="signing-key",
        )

        env = _build_smoke_env(None)

        assert env == {"PATH": "/usr/bin", "BUILD_CENTER_SMOKE_TEST": "1"}

    @pytest.mark.unit
    def test_tc_smk_002_the_allowlist_carries_nothing_secret_shaped(self):
        """TC-SMK-002: _build_smoke_env — the list itself stays free of credentials.

        Guards the list against growing a plausible-sounding entry later.
        """
        joined = " ".join(_SMOKE_ENV_PASSTHROUGH).upper()
        for word in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH"):
            assert word not in joined

    @pytest.mark.unit
    def test_tc_smk_003_build_host_library_paths_are_not_inherited(self, host_env):
        """TC-SMK-003: _build_smoke_env — the binary cannot resolve against this host.

        LD_LIBRARY_PATH or PYTHONPATH from the build server would let a binary
        find libraries here that will not exist on the machine it ships to —
        it passes the smoke test and fails at the customer, which is the exact
        failure this test is meant to catch.
        """
        host_env(
            PATH="/usr/bin",
            LD_LIBRARY_PATH="/opt/buildhost/lib",
            PYTHONPATH="/media/disk0/other-project",
            PYTHONHOME="/media/disk0/venv",
            VIRTUAL_ENV="/media/disk0/venv",
        )

        env = _build_smoke_env(None)

        assert "LD_LIBRARY_PATH" not in env
        assert "PYTHONPATH" not in env
        assert "PYTHONHOME" not in env
        assert "VIRTUAL_ENV" not in env


class TestTheBinaryCanStillStart:
    """A withheld variable produces a false failure, which is its own bug."""

    @pytest.mark.unit
    def test_tc_smk_010_process_basics_are_passed_through(self, host_env):
        """TC-SMK-010: _build_smoke_env — what a process needs to run at all."""
        host_env(
            PATH="/usr/bin",
            HOME="/home/svc",
            LANG="en_US.UTF-8",
            TZ="Asia/Taipei",
            TMPDIR="/var/tmp",
        )

        env = _build_smoke_env(None)

        assert env["PATH"] == "/usr/bin"
        assert env["HOME"] == "/home/svc"
        assert env["LANG"] == "en_US.UTF-8"
        assert env["TZ"] == "Asia/Taipei"
        assert env["TMPDIR"] == "/var/tmp"

    @pytest.mark.unit
    def test_tc_smk_011_absent_variables_are_omitted_not_blanked(self, host_env):
        """TC-SMK-011: _build_smoke_env — an unset var stays unset.

        Passing HOME="" is worse than not passing it: libraries that test for
        the key find one and build paths from an empty string.
        """
        host_env(PATH="/usr/bin")

        env = _build_smoke_env(None)

        assert "HOME" not in env
        assert "LANG" not in env

    @pytest.mark.unit
    def test_tc_smk_012_the_projects_runtime_config_is_layered_on(self, host_env):
        """TC-SMK-012: _build_smoke_env — Docker ENV and .env still arrive."""
        host_env(PATH="/usr/bin")

        env = _build_smoke_env({"DATABASE_URL": "postgres://x", "API_KEY": "k"})

        assert env["DATABASE_URL"] == "postgres://x"
        assert env["API_KEY"] == "k"

    @pytest.mark.unit
    def test_tc_smk_013_the_project_wins_over_the_host(self, host_env):
        """TC-SMK-013: _build_smoke_env — a project setting PATH is not overridden.

        The host values are a floor, not a policy; a project that ships its
        own PATH or TMPDIR in .env means it.
        """
        host_env(PATH="/usr/bin", TMPDIR="/var/tmp")

        env = _build_smoke_env({"PATH": "/opt/app/bin", "TMPDIR": "/opt/app/tmp"})

        assert env["PATH"] == "/opt/app/bin"
        assert env["TMPDIR"] == "/opt/app/tmp"

    @pytest.mark.unit
    def test_tc_smk_014_the_marker_cannot_be_overridden(self, host_env):
        """TC-SMK-014: _build_smoke_env — a project cannot claim it is not a smoke test.

        Applications branch on this to skip work they shouldn't do during a
        ten-second verification; letting .env unset it would hang the build
        slot instead.
        """
        host_env(PATH="/usr/bin")

        env = _build_smoke_env({"BUILD_CENTER_SMOKE_TEST": "0"})

        assert env["BUILD_CENTER_SMOKE_TEST"] == "1"
