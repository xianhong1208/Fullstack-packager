"""
Unit tests for where a frontend build is allowed to write.
Source: app/schemas/task.BuildConfig.validate_frontend_paths
        app/schemas/task.BuildConfig.validate_frontend_output_containment

A frontend that builds into the backend's static directory is configured as
frontend_output_dir="../static/web", and build_dispatcher resolves it with
normpath on purpose. It is a supported layout and a common one here — 73 of
698 stored build records use it.

An earlier version of the field validator rejected ".." outright to keep the
generated Dockerfile safe. BuildConfig is also what every *stored* config is
read back through, so those 73 records stopped deserialising and the history
list failed for everyone who owned one — reported as a Dockerfile-injection
error naming a directory the user had configured years of builds around.

The property that actually matters is where the path resolves to, which a
single segment cannot express. These tests pin both halves: the injection
characters stay rejected, and containment is judged on the resolved path.
"""

import pytest
from pydantic import ValidationError

from app.schemas.task import BuildConfig


def _config(frontend_dir: str = "frontend", output: str = "dist") -> BuildConfig:
    return BuildConfig(frontend_dir=frontend_dir, frontend_output_dir=output)


class TestSupportedLayouts:
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("frontend_dir", "output"),
        [
            ("frontend", "dist"),                  # the default
            ("frontend", "build"),
            ("frontend", "../static/web"),         # the layout this broke
            ("frontend", "../backend/static"),
            ("apps/web", "../../shared/static"),   # monorepo, still inside
            ("frontend", "./dist"),
            ("frontend", "dist/client"),
        ],
    )
    def test_tc_fop_001_paths_that_stay_in_the_project_are_accepted(
        self, frontend_dir: str, output: str
    ):
        """TC-FOP-001: BuildConfig — a resolved path inside the project is fine.

        Including the ".." forms: they are how a frontend delivers its bundle
        to a backend that serves it.
        """
        assert _config(frontend_dir, output).frontend_output_dir == output

    @pytest.mark.unit
    def test_tc_fop_002_stored_records_round_trip(self):
        """TC-FOP-002: BuildConfig — a config that ran can always be read back.

        This is the regression itself: model_validate is the history read
        path, so a rule that rejects a past build hides the record of it.
        """
        stored = _config("frontend", "../static/web").model_dump()

        assert BuildConfig.model_validate(stored).frontend_output_dir == "../static/web"


class TestEscapingTheProject:
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("frontend_dir", "output"),
        [
            ("frontend", "../../etc"),
            ("frontend", "../.."),
            ("apps/web", "../../../outside"),
            ("frontend", "/etc"),
            ("frontend", "/"),
        ],
    )
    def test_tc_fop_010_paths_that_leave_the_project_are_refused(
        self, frontend_dir: str, output: str
    ):
        """TC-FOP-010: BuildConfig — the resolved directory must stay inside.

        It becomes a COPY --from=builder source and, in frontend-only builds,
        an rmtree target.
        """
        with pytest.raises(ValidationError):
            _config(frontend_dir, output)

    @pytest.mark.unit
    def test_tc_fop_011_depth_is_judged_against_frontend_dir(self):
        """TC-FOP-011: BuildConfig — the same value can be legal or not.

        "../.." escapes from "frontend" but lands on the project root from
        "apps/web", which is why this cannot be a field-level rule.
        """
        with pytest.raises(ValidationError):
            _config("frontend", "../..")

        assert _config("apps/web", "../..").frontend_output_dir == "../.."

    @pytest.mark.unit
    def test_tc_fop_012_the_error_says_where_it_resolved_to(self):
        """TC-FOP-012: BuildConfig — the message names the computed path.

        "'..' is not allowed" gives someone with a working layout nothing to
        act on; the resolved path shows them what the platform concluded.
        """
        with pytest.raises(ValidationError, match=r"\.\./etc"):
            _config("frontend", "../../etc")


class TestDockerfileInjectionStillBlocked:
    """The reason the original rule existed. It has not been relaxed."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "value",
        ["dist\nRUN echo pwned", "dist\rUSER root", "dist\tx", "../static\nRUN evil"],
    )
    def test_tc_fop_020_newlines_are_refused(self, value: str):
        """TC-FOP-020: BuildConfig — a newline would start a new instruction."""
        with pytest.raises(ValidationError):
            _config("frontend", value)

    @pytest.mark.unit
    @pytest.mark.parametrize("value", ["frontend\nUSER root", "/abs/path"])
    def test_tc_fop_021_frontend_dir_is_checked_the_same_way(self, value: str):
        """TC-FOP-021: BuildConfig — frontend_dir lands in COPY too."""
        with pytest.raises(ValidationError):
            BuildConfig(frontend_dir=value)
