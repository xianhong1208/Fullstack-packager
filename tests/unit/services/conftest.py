"""
Shared fixtures and constants for app.services unit tests.

Scope: pure functions only — no DB, no network, no subprocess. Anything that
needs a real filesystem uses pytest's ``tmp_path`` rather than a mocked
``Path``, because the functions under test exist precisely to interpret real
directory layouts (venvs, site-packages, dist-info).
"""

from pathlib import Path

import pytest

# --- Shared constants ---

GITLAB_TOKEN = "glpat-EXAMPLE0TOKEN0VALUE"
PUBLIC_REPO_URL = "https://gitlab.example.com/group/project.git"
PRIVATE_IP_URL = "https://192.168.112.10/group/project.git"

# --- Patch target strings ---

PATCH_BUILD_CENTER_ROOT = "app.services.nuitka_worker._BUILD_CENTER_ROOT"


# --- Filesystem builders ---


@pytest.fixture
def make_site_packages(tmp_path: Path):
    """Return a builder that creates ``<project>/.venv/lib/pythonX.Y/site-packages``.

    Returns the project root Path, which is what the functions under test
    expect to be handed.
    """

    def _build(py_dir: str = "python3.13", project_name: str = "proj") -> Path:
        project = tmp_path / project_name
        site_packages = project / ".venv" / "lib" / py_dir / "site-packages"
        site_packages.mkdir(parents=True)
        return project

    return _build


@pytest.fixture
def site_packages_of():
    """Return a helper resolving a project root to its site-packages dir."""

    def _resolve(project: Path) -> Path:
        return next((project / ".venv").glob("lib/python*/site-packages"))

    return _resolve
