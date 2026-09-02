"""
Unit tests for surfacing the detected Python version at form time.
Source: app/api/routes/tasks.analyze_project (detected_python block)

The detection itself already existed and ran during preflight — after the user
had filled in four steps, queued, and waited for the build to start. One
project submitted a version its own .so files could not load 37 times. Moving
the same answer to form time turns a failed build into a corrected dropdown.

What matters here is the shape of the response the form reasons about, and
especially the cases where it must stay SILENT: a warning that fires on
healthy projects is worse than none, because people learn to dismiss it.
"""

from pathlib import Path

import pytest

from app.services.nuitka_worker import detect_project_python_version


def _site_packages(root: Path, py_dir: str = "python3.12") -> Path:
    sp = root / ".venv" / "lib" / py_dir / "site-packages"
    sp.mkdir(parents=True)
    return sp


def _detected_payload(project: Path) -> dict:
    """Mirror of the block analyze_project builds, so the contract the form
    consumes is asserted without standing up the whole route."""
    version, detail = detect_project_python_version(project)
    return {
        "version": version,
        "detail": detail,
        "inconsistent": version is None and "mixed" in (detail or "").lower(),
    }


class TestDetectedPythonPayload:
    """The three states the form has to tell apart."""

    @pytest.mark.unit
    def test_tc_dpy_001_confident_detection(self, tmp_path: Path):
        """TC-DPY-001: detected_python — a single ABI tag yields a version to compare against."""
        sp = _site_packages(tmp_path)
        (sp / "_cffi_backend.cpython-312-x86_64-linux-gnu.so").write_bytes(b"")

        payload = _detected_payload(tmp_path)

        assert payload["version"] == "3.12"
        assert payload["inconsistent"] is False

    @pytest.mark.unit
    def test_tc_dpy_002_pure_python_stays_silent(self, tmp_path: Path):
        """TC-DPY-002: detected_python — no compiled extensions means no opinion.

        A pure-Python project runs on any version, so there is nothing to
        contradict. Warning here would be crying wolf.
        """
        sp = _site_packages(tmp_path)
        (sp / "purelib.py").write_text("", encoding="utf-8")

        payload = _detected_payload(tmp_path)

        assert payload["version"] is None
        assert payload["inconsistent"] is False

    @pytest.mark.unit
    def test_tc_dpy_003_no_venv_stays_silent(self, tmp_path: Path):
        """TC-DPY-003: detected_python — a project without a .venv gets no warning."""
        payload = _detected_payload(tmp_path)

        assert payload["version"] is None
        assert payload["inconsistent"] is False

    @pytest.mark.unit
    def test_tc_dpy_004_mixed_venv_is_flagged_as_broken(self, tmp_path: Path):
        """TC-DPY-004: detected_python — evenly split ABI tags are reported as inconsistent.

        This is a different problem with a different fix: no dropdown choice
        can compile it, the .venv has to be rebuilt. The form says so instead
        of suggesting a version.
        """
        sp = _site_packages(tmp_path)
        for i in range(2):
            (sp / f"a{i}.cpython-312-x86_64-linux-gnu.so").write_bytes(b"")
            (sp / f"b{i}.cpython-314-x86_64-linux-gnu.so").write_bytes(b"")

        payload = _detected_payload(tmp_path)

        assert payload["version"] is None
        assert payload["inconsistent"] is True

    @pytest.mark.unit
    def test_tc_dpy_005_vendored_minority_does_not_flag_inconsistent(self, tmp_path: Path):
        """TC-DPY-005: detected_python — a few odd .so files do not make a venv 'broken'.

        Healthy venvs routinely carry a handful of extensions for other
        versions because some wheels ship multi-version binaries. Treating that
        as inconsistent would flag most real projects.
        """
        sp = _site_packages(tmp_path)
        for i in range(9):
            (sp / f"mod{i}.cpython-312-x86_64-linux-gnu.so").write_bytes(b"")
        (sp / "vendored.cpython-310-x86_64-linux-gnu.so").write_bytes(b"")

        payload = _detected_payload(tmp_path)

        assert payload["version"] == "3.12"
        assert payload["inconsistent"] is False

    @pytest.mark.unit
    def test_tc_dpy_006_detail_names_the_evidence(self, tmp_path: Path):
        """TC-DPY-006: detected_python — the detail cites the file it read.

        The warning is asking someone to override their own choice, so it has
        to show why rather than assert authority.
        """
        sp = _site_packages(tmp_path)
        (sp / "_pydantic_core.cpython-313-x86_64-linux-gnu.so").write_bytes(b"")

        payload = _detected_payload(tmp_path)

        assert "_pydantic_core" in payload["detail"]
