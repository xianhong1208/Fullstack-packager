"""
Unit tests keeping the submitted build config out of the workers' hands.
Source: app/services/build_dispatcher.dispatch_build

The dispatcher fills in values the user never supplied: output_dir defaults to
"dist" for Docker builds, the frontend's output directory is appended to
data_dirs, and frontend_worker overwrites frontend_output_dir with whatever it
detected. In local mode the object it was handed is the same instance
task_manager keeps for the API response, so those derived values were reported
back as if the user had typed them — and TaskDetail's rebuild button prefills
the next form from exactly that response.

History is written from a model_dump at creation, so it kept the submitted
values; the same task showed two different configs depending on which page you
opened. Git mode was never affected, because _prepare_git_workspace already
returns a copy.
"""

import pytest
from pytest_mock import MockerFixture

from app.schemas.task import BuildConfig, PackMode, ProjectType
from app.services import build_dispatcher


@pytest.fixture
def quiet(mocker: MockerFixture):
    """Silence the task manager and the project lock; keep dispatch_build real."""
    mocker.patch.object(build_dispatcher.task_manager, "update_task")
    mocker.patch.object(build_dispatcher.task_manager, "append_log")
    mocker.patch.object(build_dispatcher, "release_project_lock")


@pytest.fixture
def dispatched(mocker: MockerFixture, quiet):
    """Replace the worker layer and hand back the config it actually received."""
    return mocker.patch.object(build_dispatcher, "_dispatch_locked", autospec=True)


def _received(dispatched) -> BuildConfig:
    assert dispatched.await_count == 1
    return dispatched.await_args.args[1]


class TestSubmittedConfigIsNotMutated:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_cfg_001_the_worker_gets_a_different_object(self, dispatched):
        """TC-CFG-001: dispatch_build — the instance below is not the one passed in.

        Identity is the whole point: every mutation downstream is in-place, so
        an equal-but-shared object would fix nothing.
        """
        submitted = BuildConfig(project_path="/media/disk0/x")

        await build_dispatcher.dispatch_build("t1", submitted, db=None)

        assert _received(dispatched) is not submitted

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_cfg_002_a_worker_writing_to_the_copy_leaves_the_original_alone(
        self, dispatched
    ):
        """TC-CFG-002: dispatch_build — the three fields the workers actually rewrite.

        Reproduces what _build_docker_nuitka, _build_fullstack and
        frontend_worker do, rather than asserting on a copy in the abstract.
        """
        submitted = BuildConfig(
            project_path="/media/disk0/x",
            project_type=ProjectType.FULLSTACK,
            output_dir="",
            data_dirs="app",
            frontend_dir="frontend",
            frontend_output_dir="dist",
        )

        await build_dispatcher.dispatch_build("t1", submitted, db=None)

        worker_config = _received(dispatched)
        worker_config.output_dir = "dist"
        worker_config.data_dirs = "app,frontend/dist"
        worker_config.frontend_output_dir = "build"

        assert submitted.output_dir == ""
        assert submitted.data_dirs == "app"
        assert submitted.frontend_output_dir == "dist"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_cfg_003_the_copy_carries_every_submitted_value(self, dispatched):
        """TC-CFG-003: dispatch_build — copying must not drop or default a field.

        A shallow rebuild from a subset of fields would silently reset anything
        it forgot, which is a worse bug than the one being fixed.
        """
        submitted = BuildConfig(
            project_path="/media/disk0/x",
            project_type=ProjectType.FULLSTACK,
            pack_mode=PackMode.EXTERNAL,
            python_version="3.13",
            output_name="app",
            data_dirs="app,alembic",
            extra_dirs="src",
            frontend_dir="frontend",
            frontend_build_command="build:prod",
            docker_enabled=True,
            docker_expose_port=9000,
        )

        await build_dispatcher.dispatch_build("t1", submitted, db=None)

        assert _received(dispatched).model_dump() == submitted.model_dump()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_cfg_004_nested_values_are_copied_too(self, dispatched):
        """TC-CFG-004: dispatch_build — the copy is deep.

        dependency_groups is a list; a shallow copy would share the same one,
        so appending to it downstream would still reach the submitted config.
        """
        submitted = BuildConfig(
            project_path="/media/disk0/x",
            dependency_groups=["dev"],
        )

        await build_dispatcher.dispatch_build("t1", submitted, db=None)

        received = _received(dispatched)
        assert received.dependency_groups is not submitted.dependency_groups
        received.dependency_groups.append("test")
        assert submitted.dependency_groups == ["dev"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tc_cfg_005_the_lock_still_keys_on_the_project_path(
        self, dispatched, mocker: MockerFixture
    ):
        """TC-CFG-005: dispatch_build — copying does not change what is serialised.

        The lock is keyed by resolved path, not by object identity, so two
        builds of one directory must still collide after the copy.
        """
        spy = mocker.spy(build_dispatcher, "get_project_lock")
        submitted = BuildConfig(project_path="/media/disk0/x")

        await build_dispatcher.dispatch_build("t1", submitted, db=None)

        assert spy.call_args.args[0] == "/media/disk0/x"
