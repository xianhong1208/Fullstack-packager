"""
Unit tests for the dashboard's build-environment check.
Source: app/api/routes/monitoring._collect_build_environment

This check exists because of a failure mode that produces no error anywhere:
a per-version venv is a symlink to a uv-managed interpreter, and changing the
service user (or moving the machine, or cleaning uv's cache) leaves it
dangling. find_nuitka_python() then correctly refuses it and silently falls
back to the service's own interpreter, so a user who picked 3.14 receives a
3.13 binary that only fails at their customer's runtime.

Nothing reported that state until someone read the history table. These tests
pin the detection so the dashboard keeps saying it out loud.
"""

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from app.api.routes.monitoring import _collect_build_environment

PATCH_ROOT = "app.services.nuitka_worker._BUILD_CENTER_ROOT"


def _make_venv(root: Path, version_compact: str, *, target: Path | None) -> Path:
    """Create .venv-pyXYZ/bin/python as a symlink to `target`.

    Passing a target that does not exist reproduces the real-world breakage:
    the venv directory looks completely normal, only the link is dead.
    """
    bin_dir = root / f".venv-py{version_compact}" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").symlink_to(target or (root / "missing" / "python"))
    return bin_dir / "python"


class TestCollectBuildEnvironment:
    """Tests for the compile-target availability report."""

    @pytest.mark.unit
    def test_tc_env_001_reports_usable_target(self, mocker: MockerFixture, tmp_path: Path):
        """TC-ENV-001: environment — a venv whose interpreter resolves is reported usable."""
        real = tmp_path / "python3.13"
        real.write_text("", encoding="utf-8")
        _make_venv(tmp_path, "313", target=real)
        mocker.patch(PATCH_ROOT, tmp_path)

        result = _collect_build_environment()

        assert result["targets"] == [
            {
                "version": "3.13",
                "venv": ".venv-py313",
                "usable": True,
                "target": str(real),
            }
        ]
        assert result["degraded"] == []

    @pytest.mark.unit
    def test_tc_env_002_dangling_symlink_is_degraded(
        self, mocker: MockerFixture, tmp_path: Path
    ):
        """TC-ENV-002: environment — a dangling interpreter link is reported unusable.

        The exact incident this endpoint was added for: the directory exists,
        `ls` looks fine, and only following the link reveals the problem.
        """
        _make_venv(tmp_path, "314", target=None)
        mocker.patch(PATCH_ROOT, tmp_path)

        result = _collect_build_environment()

        assert result["targets"][0]["usable"] is False
        assert result["degraded"] == ["3.14"]

    @pytest.mark.unit
    def test_tc_env_003_reports_where_a_broken_link_points(
        self, mocker: MockerFixture, tmp_path: Path
    ):
        """TC-ENV-003: environment — the dead target is included, not just a boolean.

        Knowing it points into another user's home is what turns "3.14 is
        broken" into "the venv was built by a different user".
        """
        stale = tmp_path / "home" / "someone-else" / "python3.12"
        bin_python = _make_venv(tmp_path, "312", target=stale)
        mocker.patch(PATCH_ROOT, tmp_path)

        result = _collect_build_environment()

        assert result["targets"][0]["target"] == str(stale)
        assert bin_python.is_symlink()

    @pytest.mark.unit
    def test_tc_env_004_mixed_health_is_partitioned(
        self, mocker: MockerFixture, tmp_path: Path
    ):
        """TC-ENV-004: environment — healthy and broken targets are separated correctly."""
        good = tmp_path / "python3.13"
        good.write_text("", encoding="utf-8")
        _make_venv(tmp_path, "313", target=good)
        _make_venv(tmp_path, "314", target=None)
        mocker.patch(PATCH_ROOT, tmp_path)

        result = _collect_build_environment()

        by_version = {t["version"]: t["usable"] for t in result["targets"]}
        assert by_version == {"3.13": True, "3.14": False}
        assert result["degraded"] == ["3.14"]

    @pytest.mark.unit
    def test_tc_env_005_ignores_unrelated_directories(
        self, mocker: MockerFixture, tmp_path: Path
    ):
        """TC-ENV-005: environment — only .venv-py<digits> directories are considered."""
        (tmp_path / ".venv-pyold" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        mocker.patch(PATCH_ROOT, tmp_path)

        assert _collect_build_environment()["targets"] == []

    @pytest.mark.unit
    def test_tc_env_006_no_venvs_reports_empty_not_error(
        self, mocker: MockerFixture, tmp_path: Path
    ):
        """TC-ENV-006: environment — a bare install reports no targets rather than failing."""
        mocker.patch(PATCH_ROOT, tmp_path)

        result = _collect_build_environment()

        assert result["targets"] == []
        assert result["degraded"] == []
        assert result["service_python"].count(".") == 1
