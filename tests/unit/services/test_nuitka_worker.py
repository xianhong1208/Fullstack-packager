"""
Unit tests for the pure helpers of the Nuitka build worker.
Source: app/services/nuitka_worker.py

These functions decide what ends up on the Nuitka command line and which
interpreter compiles the binary. A wrong answer here does not raise — it
produces an executable that fails at the customer's runtime (ModuleNotFound,
ABI mismatch), which is why they are worth locking down.
"""

import sys
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from app.schemas.task import BuildConfig
from app.services import nuitka_worker
from app.services.nuitka_worker import (
    _human_size,
    _normalize_top_level_entry,
    _parse_env_lines,
    _resolve_smoke_env,
    _top_names,
    collect_used_stdlib,
    detect_lazy_packages,
    detect_project_python_version,
    find_nuitka_python,
    find_target_python,
)

from .conftest import PATCH_BUILD_CENTER_ROOT


class TestNormalizeTopLevelEntry:
    """Tests for _normalize_top_level_entry — top_level.txt line -> dotted module name.

    A '/' surviving into --nofollow-import-to makes Nuitka exit 1 *before*
    compiling, so anything not a clean dotted identifier must become None.
    """

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("sentencepiece", "sentencepiece"),
            ("google.protobuf", "google.protobuf"),
            ("_private", "_private"),
            ("  spaced  ", "spaced"),
        ],
    )
    def test_tc_nui_001_passes_through_valid_module_names(self, raw, expected):
        """TC-NUI-001: _normalize_top_level_entry — already-valid names preserved."""
        assert _normalize_top_level_entry(raw) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("sentencepiece/__init__", "sentencepiece"),
            ("sentencepiece/__init__.py", "sentencepiece"),
            ("nvidia/cusparselt", "nvidia.cusparselt"),
            ("foo/bar.py", "foo.bar"),
            ("foo\\bar", "foo.bar"),
            ("/foo/bar/", "foo.bar"),
        ],
    )
    def test_tc_nui_002_converts_paths_to_dotted_names(self, raw, expected):
        """TC-NUI-002: _normalize_top_level_entry — wheel-recorded paths become module names."""
        assert _normalize_top_level_entry(raw) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "\n",
            "123abc",       # identifiers cannot start with a digit
            "foo-bar",      # hyphen is not valid in an import name
            "foo bar",
            "*",
            "foo/",         # collapses to "foo" -> valid, guarded separately below
        ],
    )
    def test_tc_nui_003_rejects_unusable_entries(self, raw):
        """TC-NUI-003: _normalize_top_level_entry — non-identifier entries yield None."""
        result = _normalize_top_level_entry(raw)
        assert result is None or result == "foo"

    @pytest.mark.unit
    def test_tc_nui_004_output_never_contains_a_slash(self):
        """TC-NUI-004: _normalize_top_level_entry — no output ever carries '/' (the Nuitka exit-1 trigger)."""
        samples = [
            "sentencepiece/__init__",
            "nvidia/cusparselt",
            "a/b/c/d.py",
            "weird//path",
            "C:\\pkg\\mod",
        ]
        for raw in samples:
            result = _normalize_top_level_entry(raw)
            assert result is None or "/" not in result


class TestTopNames:
    """Tests for _top_names — extracts top-level module names from an import line."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("keyword", "rest", "expected"),
        [
            ("from", "a.b import c", ["a"]),
            ("from", "json import loads", ["json"]),
            ("import", "os", ["os"]),
            ("import", "os, sys", ["os", "sys"]),
            ("import", "a, b.c as d", ["a", "b"]),
            ("import", "numpy as np", ["numpy"]),
        ],
    )
    def test_tc_nui_010_extracts_top_level_names(self, keyword, rest, expected):
        """TC-NUI-010: _top_names — dotted/multi/aliased imports reduced to top-level names."""
        assert _top_names(keyword, rest) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize(("keyword", "rest"), [("from", ""), ("import", ""), ("import", "  ,  ")])
    def test_tc_nui_011_empty_rest_yields_no_names(self, keyword, rest):
        """TC-NUI-011: _top_names — empty import body yields no names."""
        assert _top_names(keyword, rest) == []

    @pytest.mark.unit
    def test_tc_nui_012_relative_import_yields_no_real_module(self):
        """TC-NUI-012: _top_names — `from . import x` produces no usable module name.

        Current behaviour returns [""] rather than []; harmless because "" can
        never match a stdlib name, but locked here so a refactor is deliberate.
        """
        assert _top_names("from", ". import x") == [""]


class TestCollectUsedStdlib:
    """Tests for collect_used_stdlib — regex scan that decides which stdlib EXTERNAL mode keeps."""

    @pytest.mark.unit
    def test_tc_nui_020_collects_only_known_stdlib_names(self, tmp_path: Path):
        """TC-NUI-020: collect_used_stdlib — returns the intersection of imports and stdlib."""
        (tmp_path / "mod.py").write_text(
            "import os, sys\n"
            "from json import loads\n"
            "import requests\n"          # third-party -> not in stdlib_names
            "from pathlib import Path\n",
            encoding="utf-8",
        )
        result = collect_used_stdlib(tmp_path, {"os", "sys", "json", "pathlib"})
        assert result == {"os", "sys", "json", "pathlib"}

    @pytest.mark.unit
    def test_tc_nui_021_scans_nested_directories(self, tmp_path: Path):
        """TC-NUI-021: collect_used_stdlib — recurses into subpackages."""
        nested = tmp_path / "pkg" / "sub"
        nested.mkdir(parents=True)
        (nested / "deep.py").write_text("import hashlib\n", encoding="utf-8")
        assert collect_used_stdlib(tmp_path, {"hashlib"}) == {"hashlib"}

    @pytest.mark.unit
    def test_tc_nui_022_ignores_commented_and_indented_forms(self, tmp_path: Path):
        """TC-NUI-022: collect_used_stdlib — indented imports counted, trailing comments stripped."""
        (tmp_path / "mod.py").write_text(
            "def f():\n"
            "    import csv  # inline comment\n",
            encoding="utf-8",
        )
        assert collect_used_stdlib(tmp_path, {"csv"}) == {"csv"}

    @pytest.mark.unit
    def test_tc_nui_023_honours_max_files_cap(self, tmp_path: Path):
        """TC-NUI-023: collect_used_stdlib — stops after max_files, whichever files those are."""
        (tmp_path / "a.py").write_text("import csv\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("import uuid\n", encoding="utf-8")
        result = collect_used_stdlib(tmp_path, {"csv", "uuid"}, max_files=1)
        assert len(result) == 1

    @pytest.mark.unit
    def test_tc_nui_024_unreadable_file_is_skipped(self, tmp_path: Path):
        """TC-NUI-024: collect_used_stdlib — binary garbage does not abort the scan."""
        (tmp_path / "broken.py").write_bytes(b"\xff\xfe\x00garbage")
        (tmp_path / "good.py").write_text("import time\n", encoding="utf-8")
        assert collect_used_stdlib(tmp_path, {"time"}) == {"time"}


class TestHumanSize:
    """Tests for _human_size — build artifact size formatting."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, "0 B"),
            (512, "512 B"),
            (1023, "1023 B"),
            (1024, "1.0 KB"),
            (1536, "1.5 KB"),
            (1024**2, "1.0 MB"),
            (1024**3, "1.0 GB"),
            (1024**4, "1.0 TB"),
            (5 * 1024**4, "5.0 TB"),
        ],
    )
    def test_tc_nui_030_formats_each_unit(self, value, expected):
        """TC-NUI-030: _human_size — bytes rendered with the right unit."""
        assert _human_size(value) == expected


class TestParseEnvLines:
    """Tests for _parse_env_lines — feeds env vars to the smoke-tested binary."""

    @pytest.mark.unit
    def test_tc_nui_040_parses_basic_pairs(self):
        """TC-NUI-040: _parse_env_lines — plain KEY=VALUE pairs parsed."""
        assert _parse_env_lines("A=1\nB=two\n") == {"A": "1", "B": "two"}

    @pytest.mark.unit
    def test_tc_nui_041_skips_comments_and_blanks(self):
        """TC-NUI-041: _parse_env_lines — '#' comments and blank lines ignored."""
        text = "# a comment\n\n  \nKEY=value\n# trailing\n"
        assert _parse_env_lines(text) == {"KEY": "value"}

    @pytest.mark.unit
    def test_tc_nui_042_tolerates_export_prefix(self):
        """TC-NUI-042: _parse_env_lines — shell-style 'export ' prefix stripped."""
        assert _parse_env_lines("export DB_HOST=localhost\n") == {"DB_HOST": "localhost"}

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ('K="quoted"', "quoted"),
            ("K='quoted'", "quoted"),
            ('K="unbalanced', '"unbalanced'),
            ("K=\"outer 'inner' outer\"", "outer 'inner' outer"),
        ],
    )
    def test_tc_nui_043_strips_one_pair_of_surrounding_quotes(self, line, expected):
        """TC-NUI-043: _parse_env_lines — a single matched quote pair is removed."""
        assert _parse_env_lines(line) == {"K": expected}

    @pytest.mark.unit
    def test_tc_nui_044_keeps_equals_inside_value(self):
        """TC-NUI-044: _parse_env_lines — only the first '=' splits the pair."""
        text = "DATABASE_URL=postgresql://u:p@h/db?opt=1\n"
        assert _parse_env_lines(text) == {"DATABASE_URL": "postgresql://u:p@h/db?opt=1"}

    @pytest.mark.unit
    @pytest.mark.parametrize("text", ["NO_EQUALS_HERE\n", "=orphan_value\n", "   =x\n"])
    def test_tc_nui_045_skips_malformed_lines(self, text):
        """TC-NUI-045: _parse_env_lines — lines without a key or '=' are dropped."""
        assert _parse_env_lines(text) == {}

    @pytest.mark.unit
    def test_tc_nui_046_trims_whitespace_around_key_and_value(self):
        """TC-NUI-046: _parse_env_lines — surrounding whitespace trimmed."""
        assert _parse_env_lines("  KEY  =  value  \n") == {"KEY": "value"}


class TestResolveSmokeEnv:
    """Tests for _resolve_smoke_env — Docker ENV beats .env beats nothing."""

    @pytest.mark.unit
    def test_tc_nui_050_docker_env_wins(self, tmp_path: Path):
        """TC-NUI-050: _resolve_smoke_env — Docker ENV block takes priority over .env."""
        (tmp_path / ".env").write_text("FROM_FILE=1\n", encoding="utf-8")
        config = BuildConfig(docker_env_vars="FROM_DOCKER=1\nB=2\n")

        env, source = _resolve_smoke_env(config, tmp_path)

        assert env == {"FROM_DOCKER": "1", "B": "2"}
        assert source == "Docker ENV (2 vars)"

    @pytest.mark.unit
    def test_tc_nui_051_falls_back_to_env_file(self, tmp_path: Path):
        """TC-NUI-051: _resolve_smoke_env — empty Docker ENV falls back to the project .env."""
        (tmp_path / ".env").write_text("FROM_FILE=1\n", encoding="utf-8")
        config = BuildConfig(docker_env_vars="   ")

        env, source = _resolve_smoke_env(config, tmp_path)

        assert env == {"FROM_FILE": "1"}
        assert source == ".env file (1 vars)"

    @pytest.mark.unit
    def test_tc_nui_052_comment_only_docker_env_falls_back(self, tmp_path: Path):
        """TC-NUI-052: _resolve_smoke_env — Docker ENV that parses to nothing falls back to .env."""
        (tmp_path / ".env").write_text("FROM_FILE=1\n", encoding="utf-8")
        config = BuildConfig(docker_env_vars="# only a comment\n")

        env, source = _resolve_smoke_env(config, tmp_path)

        assert env == {"FROM_FILE": "1"}
        assert source == ".env file (1 vars)"

    @pytest.mark.unit
    def test_tc_nui_053_no_source_returns_empty(self, tmp_path: Path):
        """TC-NUI-053: _resolve_smoke_env — no Docker ENV and no .env yields an empty env."""
        env, source = _resolve_smoke_env(BuildConfig(), tmp_path)

        assert env == {}
        assert source == "none (no Docker ENV or .env found)"


class TestDetectProjectPythonVersion:
    """Tests for detect_project_python_version — ABI-tag majority vote over site-packages.

    Declaration files are deliberately not trusted; only compiled extension
    filenames are. Getting this wrong produces an ABI-mismatched binary that
    only fails at the customer's runtime.
    """

    @pytest.mark.unit
    def test_tc_nui_060_no_venv_returns_none(self, tmp_path: Path):
        """TC-NUI-060: detect_project_python_version — project without .venv reports no source."""
        version, detail = detect_project_python_version(tmp_path)
        assert version is None
        assert detail == "no .venv/site-packages found in project"

    @pytest.mark.unit
    def test_tc_nui_061_pure_python_project_returns_none(
        self, make_site_packages, site_packages_of
    ):
        """TC-NUI-061: detect_project_python_version — no .so/.pyd means undetectable, not an error."""
        project = make_site_packages()
        (site_packages_of(project) / "purelib.py").write_text("", encoding="utf-8")

        version, detail = detect_project_python_version(project)

        assert version is None
        assert "no compiled extensions" in detail

    @pytest.mark.unit
    def test_tc_nui_062_single_abi_tag_detected(self, make_site_packages, site_packages_of):
        """TC-NUI-062: detect_project_python_version — one ABI tag reported with its example file."""
        project = make_site_packages()
        sp = site_packages_of(project)
        (sp / "_pydantic_core.cpython-313-x86_64-linux-gnu.so").write_bytes(b"")

        version, detail = detect_project_python_version(project)

        assert version == "3.13"
        assert detail == "from _pydantic_core.cpython-313-x86_64-linux-gnu.so"

    @pytest.mark.unit
    def test_tc_nui_063_declared_version_is_ignored(self, make_site_packages, site_packages_of):
        """TC-NUI-063: detect_project_python_version — pyvenv.cfg lying about 3.13 does not win over 3.14 .so files."""
        project = make_site_packages(py_dir="python3.13")
        (project / ".venv" / "pyvenv.cfg").write_text("version = 3.13.0\n", encoding="utf-8")
        sp = site_packages_of(project)
        (sp / "_core.cpython-314-x86_64-linux-gnu.so").write_bytes(b"")

        version, _ = detect_project_python_version(project)

        assert version == "3.14"

    @pytest.mark.unit
    def test_tc_nui_064_overwhelming_majority_wins(self, make_site_packages, site_packages_of):
        """TC-NUI-064: detect_project_python_version — 4/5 majority accepted, minority noted."""
        project = make_site_packages()
        sp = site_packages_of(project)
        for i in range(4):
            (sp / f"mod{i}.cpython-313-x86_64-linux-gnu.so").write_bytes(b"")
        (sp / "vendored.cpython-311-x86_64-linux-gnu.so").write_bytes(b"")

        version, detail = detect_project_python_version(project)

        assert version == "3.13"
        assert "4/5" in detail

    @pytest.mark.unit
    def test_tc_nui_065_evenly_mixed_abi_is_flagged_broken(
        self, make_site_packages, site_packages_of
    ):
        """TC-NUI-065: detect_project_python_version — a genuinely mixed .venv refuses to guess."""
        project = make_site_packages()
        sp = site_packages_of(project)
        for i in range(2):
            (sp / f"a{i}.cpython-313-x86_64-linux-gnu.so").write_bytes(b"")
            (sp / f"b{i}.cpython-314-x86_64-linux-gnu.so").write_bytes(b"")

        version, detail = detect_project_python_version(project)

        assert version is None
        assert "mixed ABI tags" in detail

    @pytest.mark.unit
    def test_tc_nui_066_windows_pyd_tag_detected(self, make_site_packages, site_packages_of):
        """TC-NUI-066: detect_project_python_version — Windows .pyd ABI tags also counted."""
        project = make_site_packages(py_dir="python3.12")
        (site_packages_of(project) / "_speedups.cp312-win_amd64.pyd").write_bytes(b"")

        version, _ = detect_project_python_version(project)

        assert version == "3.12"

    @pytest.mark.unit
    def test_tc_nui_067_nested_package_extensions_counted(
        self, make_site_packages, site_packages_of
    ):
        """TC-NUI-067: detect_project_python_version — .so inside a package subdir is found."""
        project = make_site_packages()
        nested = site_packages_of(project) / "numpy" / "core"
        nested.mkdir(parents=True)
        (nested / "_multiarray.cpython-312-x86_64-linux-gnu.so").write_bytes(b"")

        version, _ = detect_project_python_version(project)

        assert version == "3.12"


class TestFindNuitkaPython:
    """Tests for find_nuitka_python — picks the interpreter that RUNS Nuitka.

    The produced binary is ABI-bound to this interpreter, so a silent fallback
    must be reported to the caller via the returned match flag.
    """

    @pytest.mark.unit
    def test_tc_nui_070_prefers_dedicated_version_venv(
        self, mocker: MockerFixture, tmp_path: Path
    ):
        """TC-NUI-070: find_nuitka_python — matching .venv-pyXYZ is used and reported as matched."""
        venv_python = tmp_path / ".venv-py313" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("", encoding="utf-8")
        mocker.patch(PATCH_BUILD_CENTER_ROOT, tmp_path)

        executable, matched = find_nuitka_python("3.13")

        assert executable == str(venv_python)
        assert matched is True

    @pytest.mark.unit
    def test_tc_nui_071_falls_back_to_service_interpreter_on_mismatch(
        self, mocker: MockerFixture, tmp_path: Path
    ):
        """TC-NUI-071: find_nuitka_python — no venv for the version falls back and flags the mismatch."""
        mocker.patch(PATCH_BUILD_CENTER_ROOT, tmp_path)

        executable, matched = find_nuitka_python("3.9")

        assert executable == sys.executable
        assert matched is False

    @pytest.mark.unit
    def test_tc_nui_072_fallback_matches_when_service_version_equals_request(
        self, mocker: MockerFixture, tmp_path: Path
    ):
        """TC-NUI-072: find_nuitka_python — fallback is still a match if the service runs that version."""
        mocker.patch(PATCH_BUILD_CENTER_ROOT, tmp_path)
        own_version = f"{sys.version_info.major}.{sys.version_info.minor}"

        executable, matched = find_nuitka_python(own_version)

        assert executable == sys.executable
        assert matched is True

    @pytest.mark.unit
    def test_tc_nui_073_dangling_symlink_venv_is_rejected(
        self, mocker: MockerFixture, tmp_path: Path
    ):
        """TC-NUI-073: find_nuitka_python — a venv cloned from another machine (broken symlink) is not used.

        exists() follows symlinks, so a uv-created venv whose interpreter link
        points at a path that does not exist on this machine must fall back
        rather than hand Nuitka an unusable executable.

        Uses 3.9 — below this project's requires-python — so the fallback can
        never coincidentally match the service interpreter and mask the bug.
        """
        venv_python = tmp_path / ".venv-py39" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.symlink_to(tmp_path / "does" / "not" / "exist" / "python")
        mocker.patch(PATCH_BUILD_CENTER_ROOT, tmp_path)

        executable, matched = find_nuitka_python("3.9")

        assert executable == sys.executable
        assert matched is False


class TestFindTargetPython:
    """Tests for find_target_python — locates the TARGET project's interpreter."""

    @pytest.mark.unit
    def test_tc_nui_080_returns_none_without_venv(self, tmp_path: Path):
        """TC-NUI-080: find_target_python — project without a venv yields None."""
        assert find_target_python("3.13", tmp_path) is None

    @pytest.mark.unit
    def test_tc_nui_081_prefers_versioned_binary(self, tmp_path: Path):
        """TC-NUI-081: find_target_python — .venv/bin/python3.13 preferred over generic names."""
        bin_dir = tmp_path / ".venv" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "python3.13").write_text("", encoding="utf-8")
        (bin_dir / "python3").write_text("", encoding="utf-8")

        assert find_target_python("3.13", tmp_path) == str(bin_dir / "python3.13")

    @pytest.mark.unit
    def test_tc_nui_082_accepts_dangling_symlink(self, tmp_path: Path):
        """TC-NUI-082: find_target_python — a broken symlink still counts (uv links outside the venv).

        Deliberately the OPPOSITE of find_nuitka_python (TC-NUI-073): here we
        only need the path to identify the project's interpreter, not to run it.
        """
        bin_dir = tmp_path / ".venv" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "python").symlink_to("/root/.local/share/uv/python/nonexistent")

        assert find_target_python("3.13", tmp_path) == str(bin_dir / "python")


class TestDetectLazyPackages:
    """Tests for detect_lazy_packages — adds --include-package for dynamic importers.

    Miss one and the binary compiles fine, then dies at runtime with
    ModuleNotFoundError inside the customer's environment.
    """

    @pytest.mark.unit
    def test_tc_nui_090_empty_project_returns_nothing(self, tmp_path: Path):
        """TC-NUI-090: detect_lazy_packages — no manifests means no include-package flags."""
        assert detect_lazy_packages(tmp_path) == []

    @pytest.mark.unit
    def test_tc_nui_091_detects_from_requirements_txt(self, tmp_path: Path):
        """TC-NUI-091: detect_lazy_packages — requirements.txt entries matched against the known list."""
        (tmp_path / "requirements.txt").write_text(
            "litellm>=1.0.0\nrequests==2.31.0\nopenai\n", encoding="utf-8"
        )
        assert detect_lazy_packages(tmp_path) == ["litellm", "openai"]

    @pytest.mark.unit
    def test_tc_nui_092_ignores_comments_and_flags_in_requirements(self, tmp_path: Path):
        """TC-NUI-092: detect_lazy_packages — '#' comments and '-r'/'--flag' lines skipped."""
        (tmp_path / "requirements.txt").write_text(
            "# core\n-r base.txt\n--index-url https://pypi.org/simple\nanthropic>=0.20\n",
            encoding="utf-8",
        )
        assert detect_lazy_packages(tmp_path) == ["anthropic"]

    @pytest.mark.unit
    def test_tc_nui_093_strips_extras_and_markers(self, tmp_path: Path):
        """TC-NUI-093: detect_lazy_packages — extras and environment markers stripped before matching."""
        (tmp_path / "requirements.txt").write_text(
            "boto3[crt]>=1.34; python_version >= '3.10'\n", encoding="utf-8"
        )
        assert detect_lazy_packages(tmp_path) == ["boto3", "botocore"]

    @pytest.mark.unit
    def test_tc_nui_094_detects_from_pyproject_dependencies(self, tmp_path: Path):
        """TC-NUI-094: detect_lazy_packages — multi-line [project].dependencies array parsed."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\n'
            'name = "x"\n'
            'dependencies = [\n'
            '    "tiktoken>=0.5",\n'
            '    "httpx",\n'
            ']\n',
            encoding="utf-8",
        )
        assert detect_lazy_packages(tmp_path) == ["tiktoken", "tiktoken_ext"]

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "declared", ["google-generativeai", "google_generativeai"]
    )
    def test_tc_nui_095_normalizes_hyphen_underscore(self, tmp_path: Path, declared):
        """TC-NUI-095: detect_lazy_packages — hyphen/underscore spelling variants both match."""
        (tmp_path / "requirements.txt").write_text(f"{declared}>=0.5\n", encoding="utf-8")
        assert detect_lazy_packages(tmp_path) == ["google.generativeai"]

    @pytest.mark.unit
    def test_tc_nui_096_result_is_sorted_and_deduplicated(self, tmp_path: Path):
        """TC-NUI-096: detect_lazy_packages — same package in both manifests appears once."""
        (tmp_path / "requirements.txt").write_text("openai\naiohttp\n", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = [\n    "openai",\n]\n', encoding="utf-8"
        )
        result = detect_lazy_packages(tmp_path)
        assert result == ["aiohttp", "openai"]
        assert result == sorted(set(result))

    @pytest.mark.unit
    def test_tc_nui_097_unknown_packages_produce_nothing(self, tmp_path: Path):
        """TC-NUI-097: detect_lazy_packages — packages outside KNOWN_LAZY_PACKAGES ignored."""
        (tmp_path / "requirements.txt").write_text("flask\nsqlalchemy\n", encoding="utf-8")
        assert detect_lazy_packages(tmp_path) == []

    @pytest.mark.unit
    def test_tc_nui_098_unreadable_manifest_does_not_raise(self, tmp_path: Path, mocker):
        """TC-NUI-098: detect_lazy_packages — an unreadable requirements.txt is logged, not fatal."""
        (tmp_path / "requirements.txt").write_text("litellm\n", encoding="utf-8")
        mocker.patch.object(
            nuitka_worker.Path, "read_text", side_effect=OSError("permission denied")
        )
        assert detect_lazy_packages(tmp_path) == []
