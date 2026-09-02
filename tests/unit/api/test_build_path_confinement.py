"""
Unit tests confining a local-mode build to the allowed roots.
Source: app/api/routes/tasks._validate_build_config_paths

Seven read-only endpoints already route through _validate_allowed_path and the
README promises paths outside the configured LOCAL_SOURCE_ROOTS are refused. The one
request path that WRITES and RECURSIVELY DELETES — create_task — checked
nothing, handing the config straight to the dispatcher.

"task:create is effectively shell access" does not excuse it: that risk is
bounded by directories the user can already reach, while this reached the whole
filesystem and contradicted a control implemented in the same file.
"""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes.tasks import _validate_build_config_paths
from app.schemas.task import BuildConfig, SourceType

SAFE_ROOT = "/srv/build-center/projects/demo"


@pytest.fixture(autouse=True)
def _configure_local_roots(monkeypatch):
    """Local-source mode is enabled here by allowing SAFE_ROOT's tree."""
    monkeypatch.setattr(
        "app.api.routes.tasks._allowed_path_prefixes",
        lambda: ("/srv/build-center/",),
    )


def _check(**kwargs) -> None:
    _validate_build_config_paths(BuildConfig(**kwargs))


class TestProjectRoot:
    @pytest.mark.unit
    @pytest.mark.parametrize("path", ["/", "/etc", "/root", "/opt/x", "/home/someone"])
    def test_tc_pth_001_rejects_paths_outside_the_allowed_roots(self, path):
        """TC-PTH-001: create_task — a project path outside the roots is refused.

        _build_frontend_only rmtree's project_path/output_dir, so
        project_path="/" with output_dir="etc" deletes /etc as the service user.
        """
        with pytest.raises(HTTPException) as exc:
            _check(project_path=path)
        assert exc.value.status_code == 403

    @pytest.mark.unit
    def test_tc_pth_002_accepts_an_ordinary_project(self):
        """TC-PTH-002: create_task — a normal local build is unaffected."""
        _check(
            project_path=SAFE_ROOT,
            output_dir="dist",
            data_dirs="app, alembic",
            extra_dirs="src",
            frontend_dir="frontend",
        )

    @pytest.mark.unit
    def test_tc_pth_003_git_mode_is_exempt(self):
        """TC-PTH-003: create_task — git mode is not subject to this check.

        Its project_path is assigned by _prepare_git_workspace into a per-task
        directory, never supplied by the caller. Validating the caller's empty
        value would reject every git build.
        """
        _check(source_type=SourceType.GIT, git_url="https://gitlab/x.git", git_ref="main")


class TestSubdirectoryContainment:
    """Directory names are joined onto project_path, then deleted or written."""

    @pytest.mark.unit
    @pytest.mark.parametrize("value", ["../../../etc", "../..", "/etc", "a/../../../root"])
    def test_tc_pth_010_data_dirs_cannot_escape(self, value):
        """TC-PTH-010: create_task — data_dirs is rmtree'd per entry by nuitka_worker."""
        with pytest.raises(HTTPException) as exc:
            _check(project_path=SAFE_ROOT, data_dirs=value)
        assert exc.value.status_code == 400

    @pytest.mark.unit
    @pytest.mark.parametrize("value", ["../../../etc", "/etc"])
    def test_tc_pth_011_output_dir_cannot_escape(self, value):
        """TC-PTH-011: create_task — output_dir is the rmtree target in frontend builds."""
        with pytest.raises(HTTPException):
            _check(project_path=SAFE_ROOT, output_dir=value)

    @pytest.mark.unit
    def test_tc_pth_012_frontend_dir_cannot_escape(self):
        """TC-PTH-012: create_task — frontend_dir is where env content is written.

        Rejected by the schema validator before this function is reached, since
        the same value also lands in a Dockerfile COPY.
        """
        with pytest.raises(ValidationError):
            BuildConfig(project_path=SAFE_ROOT, frontend_dir="../../root")

    @pytest.mark.unit
    def test_tc_pth_013_only_the_escaping_entry_is_rejected(self):
        """TC-PTH-013: create_task — a comma list is checked entry by entry.

        A legitimate list must not be rejected because one entry is bad, nor a
        bad entry accepted because the others are fine.
        """
        _check(project_path=SAFE_ROOT, data_dirs="config, static, templates")
        with pytest.raises(HTTPException):
            _check(project_path=SAFE_ROOT, data_dirs="config, ../../../etc, static")

    @pytest.mark.unit
    def test_tc_pth_014_blank_entries_are_ignored(self):
        """TC-PTH-014: create_task — trailing commas and spaces are not paths."""
        _check(project_path=SAFE_ROOT, data_dirs="config,, , static,")

    @pytest.mark.unit
    def test_tc_pth_015_nested_subdirectories_are_allowed(self):
        """TC-PTH-015: create_task — depth inside the project is fine.

        The rule is containment, not a ban on slashes; rejecting these would
        break real projects.
        """
        _check(project_path=SAFE_ROOT, data_dirs="app/static, app/templates")
