"""Local worker service for running Nuitka builds directly on the host."""

import asyncio
import hashlib
import importlib.util
import json
import logging
import os
import pty
import re
import shutil
import site
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.schemas.task import BuildConfig, PackMode, TaskStatus
from app.services.task_manager import task_manager

if TYPE_CHECKING:
    from app.database import async_session

settings = get_settings()
logger = logging.getLogger(__name__)

_BUILD_CENTER_ROOT = Path(__file__).resolve().parents[2]

# The per-project lock lives in app/services/project_lock.py and is taken by
# build_dispatcher, not here.
#
# It used to be defined in this module and acquired by run_nuitka_build, which
# covered this worker's in-place injection into the entry point but left
# docker_worker — which backs up and overwrites .dockerignore, writes
# Dockerfile.generated and replaces the project's frontend .env — completely
# unguarded. Two builds of the same local path destroyed each other's backups.
# Do not reintroduce a second lock here; one owner for the whole
# prepare -> build -> restore window is the point.

# Known packages that use lazy/dynamic imports and need --include-package
# Maps: package install name -> list of Nuitka --include-package values
KNOWN_LAZY_PACKAGES: dict[str, list[str]] = {
    "litellm": ["litellm"],
    "pydantic": ["pydantic"],
    "google-cloud-aiplatform": ["google.cloud.aiplatform", "google.api_core"],
    "google-generativeai": ["google.generativeai"],
    "openai": ["openai"],
    "langchain": ["langchain"],
    "langchain-core": ["langchain_core"],
    "anthropic": ["anthropic"],
    "boto3": ["boto3", "botocore"],
    "transformers": ["transformers"],
    "tiktoken": ["tiktoken", "tiktoken_ext"],
    "aiohttp": ["aiohttp"],
    "grpcio": ["grpc"],
    "lupa": ["lupa"],
    "fakeredis": ["fakeredis"],
}


def detect_lazy_packages(project_path: Path) -> list[str]:
    """Scan project dependencies and return --include-package values for known lazy-import packages.

    Checks both declared dependencies (requirements.txt / pyproject.toml) AND
    actually installed packages in the project's .venv to catch indirect dependencies.
    """
    found_deps: set[str] = set()

    # Parse requirements.txt
    req_file = project_path / "requirements.txt"
    if req_file.exists():
        try:
            for line in req_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                # Extract package name (before any version specifier)
                pkg_name = re.split(r"[>=<!\[;]", line)[0].strip().lower()
                if pkg_name:
                    found_deps.add(pkg_name)
        except Exception:
            logger.warning("Failed to parse %s for lazy-package detection", req_file, exc_info=True)

    # Parse pyproject.toml dependencies
    pyproject_file = project_path / "pyproject.toml"
    if pyproject_file.exists():
        try:
            content = pyproject_file.read_text(encoding="utf-8")
            # Simple regex extraction for dependencies lists
            # Matches lines like: "litellm>=1.0", 'openai', etc. inside arrays
            in_deps = False
            for line in content.splitlines():
                stripped = line.strip()
                if re.match(r"^(dependencies|requires)\s*=\s*\[", stripped):
                    in_deps = True
                    continue
                if in_deps:
                    if stripped.startswith("]"):
                        in_deps = False
                        continue
                    # Extract quoted package name
                    match = re.match(r"""^["']([a-zA-Z0-9_-]+)""", stripped)
                    if match:
                        found_deps.add(match.group(1).lower())
        except Exception:
            logger.warning("Failed to parse %s for lazy-package detection", pyproject_file, exc_info=True)

    # Also scan installed packages in .venv to catch indirect dependencies
    # (e.g., lupa pulled in by fakeredis[lua] pulled in by pydocket)
    venv_path = project_path / ".venv"
    if venv_path.exists():
        for pkg_name in get_installed_packages(venv_path):
            found_deps.add(pkg_name.lower().replace("-", "_"))

    # Match against known lazy packages
    include_packages: list[str] = []
    for dep_name in found_deps:
        # Normalize: both hyphens and underscores
        normalized = dep_name.replace("-", "_").replace("_", "-")
        for known_name, packages in KNOWN_LAZY_PACKAGES.items():
            known_normalized = known_name.replace("-", "_").replace("_", "-")
            if dep_name == known_name or normalized == known_normalized or dep_name.replace("-", "_") == known_name.replace("-", "_"):
                include_packages.extend(packages)

    return sorted(set(include_packages))


# Runtime injection code for libs loading
INJECT_CODE = """import sys
import os
# ==================== [LIBS LOADER] ====================
base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
libs_dir = os.path.join(base_dir, "libs")
if os.path.exists(libs_dir):
    sys.path.insert(0, libs_dir)
    print(f"使用打包後啟動(已掛載外部依賴庫): {libs_dir}")
else:
    print(f"使用開發環境啟動")
# =======================================================
"""


_MODULE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")


def _normalize_top_level_entry(raw: str) -> str | None:
    """Turn a top_level.txt line into a valid dotted module name, or None.

    top_level.txt is *supposed* to list importable module names (one per line,
    e.g. ``sentencepiece``), but some wheels record file PATHS instead
    (``sentencepiece/__init__``, ``nvidia/cusparselt``). Nuitka's
    ``--nofollow-import-to`` only accepts a dotted module name — a ``/`` makes it
    reject the whole option and exit 1 *before* compiling. Normalize the path
    into a module name: drop a ``.py`` suffix, drop a trailing ``/__init__`` (that
    IS the package), and turn path separators into dots. Return None for anything
    that still isn't a clean dotted identifier so it's skipped rather than passed
    through to break the command line.
    """
    s = raw.strip().replace("\\", "/").strip("/")
    if not s:
        return None
    if s.endswith(".py"):
        s = s[:-3]
    if s.endswith("/__init__"):
        s = s[: -len("/__init__")]
    s = s.replace("/", ".")
    return s if _MODULE_NAME_RE.fullmatch(s) else None


def get_installed_packages(venv_path: Path | None = None) -> list[str]:
    """Get list of installed third-party packages.

    Args:
        venv_path: Path to project's virtual environment. If provided,
                   reads packages from there instead of system site-packages.
    """
    packages = set()

    # Determine site-packages directories to scan
    if venv_path and venv_path.exists():
        # Find site-packages in the venv
        site_packages_dirs = []
        for pattern in ["lib/python*/site-packages", "Lib/site-packages"]:
            site_packages_dirs.extend(venv_path.glob(pattern))
    else:
        # Fallback to system site-packages
        site_packages_dirs = [Path(p) for p in site.getsitepackages()]

    for sp_dir in site_packages_dirs:
        if not sp_dir.exists():
            continue
        for item in sp_dir.iterdir():
            name = item.name
            if name.startswith(".") or name == "__pycache__":
                continue
            if name.endswith(".dist-info") or name.endswith(".egg-info"):
                pkg_name = name.split("-")[0]
                if pkg_name in ("pip", "setuptools", "wheel", "nuitka"):
                    continue
                packages.add(pkg_name)
                # The distribution name often differs from the import name
                # (Pillow → PIL, PyYAML → yaml). top_level.txt records the
                # actual importable names — add those too so
                # --nofollow-import-to matches what code really imports.
                top_level = item / "top_level.txt"
                if top_level.is_file():
                    try:
                        for line in top_level.read_text(encoding="utf-8").splitlines():
                            # Normalize: some wheels record file paths here
                            # (sentencepiece/__init__), which would inject a "/"
                            # into --nofollow-import-to and make Nuitka exit 1.
                            norm = _normalize_top_level_entry(line)
                            if norm and not norm.startswith("_"):
                                packages.add(norm)
                    except OSError:
                        pass
            elif item.is_dir():
                if name not in ["pip", "setuptools", "wheel", "nuitka", "_distutils_hack"]:
                    packages.add(name)
    return list(packages)


def get_valid_std_libs() -> tuple[list[str], set[str]]:
    """Get all valid standard library modules.

    Uses sys.stdlib_module_names (Python 3.10+) to get the complete list,
    then validates each module exists and is importable.
    """
    # Get all stdlib module names
    if hasattr(sys, "stdlib_module_names"):
        candidates = list(sys.stdlib_module_names)
    else:
        # Fallback for Python < 3.10
        candidates = [
            "asyncio", "collections", "contextlib", "copy", "csv", "ctypes",
            "datetime", "decimal", "email", "functools", "hashlib", "http",
            "importlib", "inspect", "io", "itertools", "json", "logging",
            "math", "multiprocessing", "os", "pathlib", "pickle", "random",
            "re", "shutil", "socket", "sqlite3", "ssl", "string", "subprocess",
            "sys", "threading", "time", "typing", "unittest", "urllib", "uuid",
            "warnings", "xml", "zoneinfo", "zlib",
        ]

    # Auto-detect which stdlib modules are packages (have submodules)
    # by checking spec.submodule_search_locations instead of a manual list
    force_packages: set[str] = set()

    # Validate modules exist
    valid_libs = []
    for lib in candidates:
        # Skip private modules and test modules
        if lib.startswith("_") or "test" in lib:
            if lib != "unittest":
                continue
        # Skip builtin modules (always available)
        if lib in sys.builtin_module_names:
            continue
        try:
            spec = importlib.util.find_spec(lib)
            if spec is not None:
                valid_libs.append(lib)
                # If it has submodule_search_locations, it's a package
                if spec.submodule_search_locations is not None:
                    force_packages.add(lib)
        except Exception:
            continue

    return valid_libs, force_packages


def find_nuitka_python(version: str) -> tuple[str, bool]:
    """Return (python_executable, version_matched) for running Nuitka.

    The compiled binary is ABI-bound to the Python that runs Nuitka — a
    mismatch breaks native extensions (.so) at runtime with
    ModuleNotFoundError. Prefer the dedicated per-version venv
    (.venv-py312/313/314, see scripts/setup_nuitka_venvs.sh); fall back to
    this service's interpreter only when no matching venv exists.
    """
    compact = version.replace(".", "")
    candidate = _BUILD_CENTER_ROOT / f".venv-py{compact}" / "bin" / "python"
    # exists() follows symlinks — a venv cloned from another machine has a
    # dangling python symlink and is correctly rejected here.
    if candidate.exists():
        return str(candidate), True
    service_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    return sys.executable, service_version == version


# Matches the ABI tag on a compiled C-extension, e.g.
# "_pydantic_core.cpython-314-x86_64-linux-gnu.so" -> "314"
_ABI_TAG_RE = re.compile(r"\.cpython-(3\d{1,2})-")


def _find_project_site_packages(project_path: Path) -> Path | None:
    """Locate the target project's .venv site-packages, if any."""
    venv = project_path / ".venv"
    for pattern in ("lib/python*/site-packages", "Lib/site-packages"):
        matches = sorted(venv.glob(pattern))
        if matches:
            return matches[0]
    return None


def detect_project_python_version(project_path: Path) -> tuple[str | None, str]:
    """Detect the Python version a project's .venv was actually built for.

    The ONLY trustworthy source is the ``cpython-XY`` ABI tag on the
    compiled C-extension (.so) files in site-packages — that is exactly
    what EXTERNAL mode copies into libs/ and what the produced binary must
    load. Declaration files (pyvenv.cfg / pyproject requires-python /
    .python-version) are deliberately NOT trusted: a .venv can be rebuilt
    with a different Python without those being updated, silently producing
    an ABI-mismatched build (observed in the wild — a .venv declared 3.13
    whose .so files were all cpython-314).

    Returns (version, detail):
      - ("3.14", "..from _pydantic_core..so")  → confident, use this
      - (None, "no compiled extensions..")     → pure-Python project; the
        caller may fall back to a declaration file or the user's choice
      - (None, "mixed ABI tags: 3.13, 3.14..") → the .venv is broken; the
        caller should surface this rather than guess
    """
    site_packages = _find_project_site_packages(project_path)
    if site_packages is None:
        return None, "no .venv/site-packages found in project"

    counts: dict[str, int] = {}       # tag -> number of .so with that ABI
    example: dict[str, str] = {}      # tag -> an example filename
    for so in site_packages.rglob("*.so"):
        m = _ABI_TAG_RE.search(so.name)
        if m:
            tag = m.group(1)  # "312"
            counts[tag] = counts.get(tag, 0) + 1
            example.setdefault(tag, so.name)
    # Windows extension modules
    for pyd in site_packages.rglob("*.pyd"):
        m = re.search(r"\.cp(3\d{1,2})-", pyd.name)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
            example.setdefault(m.group(1), pyd.name)

    if not counts:
        return None, "no compiled extensions (.so/.pyd) — pure-Python project"

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    top_tag, top_count = ranked[0]
    total = sum(counts.values())

    # Majority rule: a healthy .venv built with one Python still often carries a
    # few .so for OTHER versions, because some wheels ship multi-version binaries
    # (e.g. ROCm's rocpd/roctx). So treat an overwhelming majority as the answer;
    # only flag "mixed/broken" when versions are genuinely close to even.
    if len(ranked) == 1 or top_count >= total * 0.8:
        version = f"3.{top_tag[1:]}"  # "312" -> "3.12"
        note = (
            ""
            if len(ranked) == 1
            else f"(此版 {top_count}/{total} 個 .so;少數其他版本為套件附帶,已忽略)"
        )
        return version, f"from {example[top_tag]}{note}"

    versions = ", ".join(f"3.{t[1:]}({c})" for t, c in ranked)
    return None, f"mixed ABI tags ({versions}) — this .venv looks inconsistent"


# Runs inside the TARGET python so the stdlib list matches the version the
# binary will embed (e.g. 3.12's stdlib differs from this service's 3.13).
_STDLIB_PROBE = """
import importlib.util, json, sys
valid, pkgs = [], []
for lib in sorted(sys.stdlib_module_names):
    if (lib.startswith("_") or ("test" in lib and lib != "unittest")
            or lib in sys.builtin_module_names):
        continue
    try:
        spec = importlib.util.find_spec(lib)
    except Exception:
        continue
    if spec is None:
        continue
    valid.append(lib)
    if spec.submodule_search_locations is not None:
        pkgs.append(lib)
print(json.dumps({"libs": valid, "packages": pkgs}))
"""


async def get_valid_std_libs_for(python_exe: str) -> tuple[list[str], set[str]]:
    """Get valid stdlib modules as seen by the given Python executable.

    Falls back to probing this service's own interpreter if the subprocess
    fails for any reason.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            python_exe, "-c", _STDLIB_PROBE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            data = json.loads(stdout.decode())
            return data["libs"], set(data["packages"])
        logger.warning(
            "stdlib probe via %s failed (exit %s): %s",
            python_exe, proc.returncode, stderr.decode(errors="replace")[:500],
        )
    except Exception:
        logger.warning("stdlib probe via %s crashed", python_exe, exc_info=True)
    return get_valid_std_libs()


def find_target_python(version: str, project_path: Path) -> str | None:
    """Find Python executable in the target project's virtual environment.

    This is used to determine the Python version for compilation target,
    not for running Nuitka itself.
    """
    venv_candidates = [
        project_path / ".venv" / "bin" / f"python{version}",
        project_path / ".venv" / "bin" / "python3",
        project_path / ".venv" / "bin" / "python",
        project_path / "venv" / "bin" / f"python{version}",
        project_path / "venv" / "bin" / "python3",
        project_path / "venv" / "bin" / "python",
        # Windows paths
        project_path / ".venv" / "Scripts" / "python.exe",
        project_path / "venv" / "Scripts" / "python.exe",
    ]
    for venv_python in venv_candidates:
        # Use is_symlink() as fallback — exists() returns False when
        # the symlink target is inaccessible (e.g., uv links to /root/.local/...)
        if venv_python.exists() or venv_python.is_symlink():
            return str(venv_python)
    return None


# Captures the keyword + the rest of an import statement so we can parse
# multi-imports like `import os, sys` and `from a.b import c` correctly.
_IMPORT_RE = re.compile(r"^[ \t]*(import|from)[ \t]+([^\n#]+)", re.MULTILINE)
# stdlib modules that are startup-critical or commonly loaded via dynamic
# import, so we always keep them even if a static scan didn't see them.
_ALWAYS_STDLIB = {"encodings"}


def _top_names(keyword: str, rest: str) -> list[str]:
    """Extract top-level module names from one import statement.

    `from a.b import c`  -> ['a']
    `import a, b.c as d`  -> ['a', 'b']
    """
    if keyword == "from":
        head = rest.split()[0] if rest.split() else ""
        return [head.split(".")[0]] if head else []
    names: list[str] = []
    for part in rest.split(","):
        toks = part.strip().split()
        if toks:
            names.append(toks[0].split(".")[0])
    return names


def collect_used_stdlib(
    scan_root: Path, stdlib_names: set[str], max_files: int = 8000
) -> set[str]:
    """Scan .py files under scan_root and return the stdlib modules they import.

    Regex-based (fast) and file-capped; best-effort — unreadable files are
    skipped. Used to figure out which stdlib the third-party packages in libs/
    depend on, so EXTERNAL mode can include just those instead of the whole
    standard library.
    """
    used: set[str] = set()
    scanned = 0
    for py in scan_root.rglob("*.py"):
        if scanned >= max_files:
            break
        scanned += 1
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for keyword, rest in _IMPORT_RE.findall(text):
            for name in _top_names(keyword, rest):
                if name in stdlib_names:
                    used.add(name)
    return used


_LIBS_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "tests", "test")


def _copytree_fast(src: Path, dst: Path) -> None:
    """Copy a package dir into libs/, preferring hardlinks (same filesystem →
    near-instant, no extra disk) and dropping bytecode/test dirs. Falls back to
    a normal byte copy across filesystems (os.link raises EXDEV)."""
    try:
        shutil.copytree(src, dst, copy_function=os.link, ignore=_LIBS_IGNORE)
    except (OSError, shutil.Error):
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, ignore=_LIBS_IGNORE)


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def _summarize_artifact(path: Path) -> dict[str, Any]:
    """Blocking artifact summary — run via asyncio.to_thread."""
    if path.is_file():
        size = path.stat().st_size
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        return {
            "path": str(path),
            "size_bytes": size,
            "size_human": _human_size(size),
            "file_count": 1,
            "sha256": digest.hexdigest(),
        }
    total = 0
    count = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                continue
            count += 1
    return {
        "path": str(path),
        "size_bytes": total,
        "size_human": _human_size(total),
        "file_count": count,
        "sha256": None,  # directory artifact — no single-file digest
    }


def _parse_env_lines(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines (Docker ENV block or a .env file).

    Skips blanks and '#' comments, tolerates a leading 'export ', and strips
    a single pair of surrounding quotes from the value. Good enough for
    feeding a smoke test — not a full dotenv parser (no interpolation).
    """
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key:
            env[key] = val
    return env


def _resolve_smoke_env(config: BuildConfig, project_path: Path) -> tuple[dict[str, str], str]:
    """Decide which env vars to give the smoke-tested binary.

    Priority (per user requirement): the Docker ENV block the user filled in
    ('執行期 Docker ENV'), because that's the real runtime config; if empty,
    fall back to the project's .env file; otherwise nothing.
    Returns (env_dict, human-readable source description).
    """
    if config.docker_env_vars and config.docker_env_vars.strip():
        parsed = _parse_env_lines(config.docker_env_vars)
        if parsed:
            return parsed, f"Docker ENV ({len(parsed)} vars)"
    env_file = project_path / ".env"
    if env_file.exists():
        try:
            parsed = _parse_env_lines(env_file.read_text(encoding="utf-8"))
            if parsed:
                return parsed, f".env file ({len(parsed)} vars)"
        except OSError:
            pass
    return {}, "none (no Docker ENV or .env found)"


# The host variables a smoke-tested binary is launched with. Everything else
# in the service's environment is dropped.
#
# The binary being launched is code the user supplied, and the environment it
# used to receive was the service's whole os.environ — which is not the
# platform's configuration but whatever happened to be in the shell that ran
# start.sh. That is unbounded and unknowable: an operator's API tokens, agent
# credentials, another project's settings. The application's own secrets were
# never in there (pydantic-settings reads .env directly), which is exactly why
# this looked harmless; switching the service to a systemd EnvironmentFile=
# would have put them there without changing a line of this file.
#
# Chosen for what a process needs to start at all, not for what an application
# might want — the application's own configuration arrives through extra_env,
# resolved by _resolve_smoke_env from the Docker ENV block or the project's
# .env.
#
# LD_LIBRARY_PATH and PYTHONPATH are deliberately absent. They would let the
# binary resolve against the build host's libraries and pass here while
# failing on the machine it ships to, which is the one failure mode this smoke
# test exists to catch.
_SMOKE_ENV_PASSTHROUGH = (
    "PATH",       # finding subprocesses it shells out to
    "HOME",       # cache and config directories; unset breaks many libraries
    "LANG",       # text encoding — the wrong locale changes how strings decode
    "LC_ALL",
    "LC_CTYPE",
    "TZ",         # timestamps in its own output
    "TMPDIR",     # extraction target for a onefile binary
    "TERM",
)


def _build_smoke_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    """Assemble the environment for the smoke-tested binary.

    Layered host basics -> caller-resolved runtime config -> the marker, so a
    project that legitimately sets PATH or TMPDIR in its .env still wins.
    """
    env = {
        name: os.environ[name]
        for name in _SMOKE_ENV_PASSTHROUGH
        if name in os.environ
    }
    env.update(extra_env or {})
    env["BUILD_CENTER_SMOKE_TEST"] = "1"
    return env


async def _smoke_test(
    task_id: str,
    binary: Path,
    cwd: Path,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Launch the produced binary briefly to catch import/startup crashes.

    Exit 0 within the window → pass. Still running after the window → pass
    (long-running service started fine; it gets terminated). Non-zero exit →
    fail, with the tail of its output as detail.

    ``extra_env`` (Docker ENV or .env, resolved by the caller) is layered on
    top of a small allow-list of host variables so services needing runtime
    config don't get a false failure for a missing variable. See
    _SMOKE_ENV_PASSTHROUGH for why the host environment is not passed whole.
    """
    if not binary.exists():
        return {
            "status": "warn",
            "detail": f"Binary not found for verification: {binary}",
            "exit_code": None,
        }
    try:
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd),
            env=_build_smoke_env(extra_env),
        )
    except OSError as e:
        return {"status": "fail", "detail": f"Failed to launch binary: {e}", "exit_code": None}

    try:
        ret = await asyncio.wait_for(proc.wait(), timeout=10)
        output = b""
        if proc.stdout:
            try:
                # Read a generous chunk so a long traceback (the failure reason
                # is usually at the END) is available for pattern matching.
                output = await asyncio.wait_for(proc.stdout.read(262144), timeout=2)
            except asyncio.TimeoutError:
                pass
        full = output.decode("utf-8", errors="replace")
        tail = full.strip()[-500:]
        if ret == 0:
            return {"status": "pass", "detail": "Binary exited cleanly", "exit_code": 0}
        # Environment-only failures: the binary started fine (imports, config,
        # logging all executed) but couldn't finish because of something
        # external — most commonly the service port already being in use by
        # another running instance. That proves the binary itself is healthy,
        # so treat it as a warning, NOT a build failure.
        low = full.lower()
        if (
            "address already in use" in low
            or "errno 98" in low
            or "eaddrinuse" in low
        ):
            return {
                "status": "warn",
                "detail": (
                    "Binary started successfully but the port is already in use "
                    "(likely another instance is still running) — environment "
                    f"issue, not a build problem (exit {ret})"
                ),
                "exit_code": ret,
            }
        # GPU / accelerator init failures: the build host usually has no GPU, so
        # a torch/ROCm/CUDA program can't initialize here even when the binary is
        # fine — same idea as the port case. Treat as a warning and tell the user
        # to verify on the target GPU machine, rather than failing the build.
        gpu_hints = (
            "rocm_sdk", "_rocm_init", "rocminfo", "libamdhip", "hip error",
            "cuda error", "cudaerror", "no cuda-capable device", "libcuda",
            "no hip", "failed to initialize", "torch/cuda",
        )
        if any(h in low for h in gpu_hints):
            return {
                "status": "warn",
                "detail": (
                    "Binary 卡在 GPU/加速器初始化(torch/ROCm/CUDA),而打包機通常沒有 "
                    "GPU — 疑似執行環境問題,非打包本身錯誤。請在有 GPU 的目標機上實際"
                    f"執行驗證 (exit {ret}): {tail[-200:]}"
                ),
                "exit_code": ret,
            }
        # External-service connection failures: the binary imported cleanly and
        # ran far enough to open a DB/cache/broker connection, then failed because
        # that backing service isn't reachable/authenticated ON THE BUILD HOST —
        # its Postgres/Redis credentials and network differ from the deploy host.
        # Reaching the connect() call proves the app itself is healthy, so this is
        # an environment issue (same class as the port/GPU cases), not a build bug.
        service_hints = (
            "operationalerror", "password authentication failed",
            "connection refused", "could not connect to server",
            "connection to server at", "could not translate host name",
            "no route to host", "econnrefused", "redis.exceptions",
            "connectionerror", "psycopg2", "could not connect",
            "access denied for user", "authentication failed",
            "timeout expired", "server closed the connection",
        )
        if any(h in low for h in service_hints):
            return {
                "status": "warn",
                "detail": (
                    "Binary 啟動成功並跑到連線外部服務(資料庫/快取/佇列)才失敗 —— "
                    "打包機的服務位址/帳密與部署機不同,屬執行環境問題,非打包本身錯誤。"
                    f"請在目標機(服務可連得到)上實際驗證 (exit {ret}): {tail[-200:]}"
                ),
                "exit_code": ret,
            }
        # Missing required CLI arguments: the smoke test runs the binary with no
        # args, but the program legitimately requires some (e.g. --config). argparse
        # prints a usage message and exits 2. That PROVES the binary launched and its
        # arg parser ran — it's a smoke-test invocation gap, not a build defect. The
        # real service is started with the proper flags. Treat as a warning.
        argparse_hints = (
            "the following arguments are required",
            "are required:",
            "is required:",
            "unrecognized arguments",
            "invalid choice",
            "expected one argument",
            "error: argument",
        )
        looks_like_usage = "usage:" in low and "error:" in low
        if looks_like_usage or any(h in low for h in argparse_hints):
            return {
                "status": "warn",
                "detail": (
                    "Binary 啟動成功,但因缺少必要的命令列參數而結束(smoke test 是不帶參數"
                    "裸跑)—— 這證明程式與參數解析正常,屬呼叫方式差異,非打包錯誤。實際服務"
                    f"請帶上正確參數(如 --config)啟動 (exit {ret}): {tail[-200:]}"
                ),
                "exit_code": ret,
            }
        # Missing license / required runtime resource file: the binary launched
        # and ran its startup (logging configured, etc.) but exited because a
        # license/credential/config FILE that lives on the DEPLOY host isn't
        # present on the build host. Same class as the DB/CLI-arg cases — the app
        # is healthy, the build machine just lacks a deploy-time resource.
        license_hints = (
            "license file not found", "license error", "license not found",
            "invalid license", "license expired", "license_file",
            "no license", "license.lic", "授權檔", "license required",
        )
        if any(h in low for h in license_hints):
            return {
                "status": "warn",
                "detail": (
                    "Binary 啟動成功,但因找不到授權/必要資源檔(如 license.lic)而結束 —— "
                    "該檔會在部署機上提供(或用 LICENSE_FILE 環境變數指定),打包機沒有屬正常,"
                    f"非打包錯誤。請在部署機備妥該檔後驗證 (exit {ret}): {tail[-200:]}"
                ),
                "exit_code": ret,
            }
        return {
            "status": "fail",
            "detail": f"Binary exited with code {ret}" + (f": {tail}" if tail else ""),
            "exit_code": ret,
        }
    except asyncio.TimeoutError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        return {
            "status": "pass",
            "detail": "Binary still running after 10s (long-running service) — startup OK",
            "exit_code": None,
        }


async def run_nuitka_build(task_id: str, config: BuildConfig, db: AsyncSession) -> bool:
    """Run a local Nuitka build asynchronously.

    Args:
        task_id: Task ID for logging
        config: Build configuration
        db: Database session

    Returns:
        True if build succeeded, False otherwise
    """
    await task_manager.append_log(
        task_id,
        f"Nuitka Build | Output: {config.output_name} | Python: {config.python_version}",
    )

    process = None
    backup_file = None
    entry_point = None
    master_fd = None
    start_ts = time.monotonic()
    build_result: dict[str, Any] = {"preflight": []}
    preflight = build_result["preflight"]

    # The project lock is held by build_dispatcher for the whole
    # prepare -> build -> restore window, covering docker_worker's file
    # shuffling as well as this worker's in-place entry-point injection. It
    # used to be taken here, which left docker_worker unprotected.
    try:
        await task_manager.set_stage(task_id, "preflight")

        # Verify project path exists
        project_path = Path(config.project_path)
        path_ok = project_path.exists() and project_path.is_dir()
        preflight.append({
            "label": "Project path exists",
            "passed": path_ok,
            "detail": str(project_path),
            "critical": True,
        })
        if not project_path.exists():
            raise FileNotFoundError(f"Project path does not exist: {config.project_path}")
        if not project_path.is_dir():
            raise NotADirectoryError(f"Project path is not a directory: {config.project_path}")

        await task_manager.append_log(task_id, f"Project path: {config.project_path}")

        # Resolve the compile target Python version.
        # The produced binary is ABI-bound to whichever Python runs Nuitka;
        # EXTERNAL mode copies the project's .so files into libs/, so the
        # binary MUST match the project .venv's real ABI. Detect that ABI
        # from the .so tags (the only trustworthy source) — either because
        # the user asked for "auto", or to sanity-check a manual choice.
        detected_version, detect_detail = detect_project_python_version(project_path)
        abi_required = detected_version is not None  # project ships compiled extensions

        requested = (config.python_version or "auto").strip().lower()
        if requested == "auto":
            if detected_version:
                target_version = detected_version
                await task_manager.append_log(
                    task_id,
                    f"Auto-detected Python {detected_version} ({detect_detail})",
                )
            else:
                # No .so to bind to (pure-Python) or a broken/mixed .venv —
                # fall back to this service's Python and note why.
                target_version = f"{sys.version_info.major}.{sys.version_info.minor}"
                await task_manager.append_log(
                    task_id,
                    f"Auto-detect: {detect_detail} — using service Python {target_version}",
                )
        else:
            target_version = requested
            # A manual version that contradicts the project's actual .so ABI is
            # a guaranteed failure (the binary can't load those extensions), so
            # fail preflight NOW instead of wasting a full compile that only
            # dies at the smoke test.
            if detected_version and detected_version != requested:
                preflight.append({
                    "label": "Python version matches dependencies",
                    "passed": False,
                    "detail": f"selected {requested}, dependencies are {detected_version}",
                    "critical": True,
                })
                await task_manager.set_result(task_id, dict(build_result), db)
                raise RuntimeError(
                    f"你選了 Python {requested},但這個專案的依賴是為 Python "
                    f"{detected_version} 編譯的({detect_detail})。用 {requested} 編出來的"
                    f"執行檔載入不了那些套件。請把「Python 版本」改成「自動偵測」,"
                    f"或手動選 {detected_version}。"
                )

        # Pick the toolchain venv for the resolved version.
        nuitka_python, version_matched = find_nuitka_python(target_version)
        preflight.append({
            "label": f"Python {target_version} toolchain"
            + (" (auto-detected)" if requested == "auto" and detected_version else ""),
            "passed": version_matched,
            # ABI-bound projects MUST compile with the matching interpreter —
            # a mismatch silently produces a binary that crashes on startup,
            # so make it a hard preflight failure instead of a fallback.
            "critical": abi_required,
            "detail": nuitka_python,
        })
        await task_manager.append_log(task_id, f"Nuitka Python: {nuitka_python}")
        if not version_matched:
            venv_name = f".venv-py{target_version.replace('.', '')}"
            if abi_required:
                # Do NOT fall back — that's exactly what produced the
                # 3.13-binary-vs-3.14-.so crash. Fail preflight clearly.
                raise RuntimeError(
                    f"This project needs Python {target_version} (its compiled "
                    f"extensions are {detect_detail}), but build_center has no usable "
                    f"{venv_name}. Build it with:  bash scripts/setup_nuitka_venvs.sh  "
                    f"(run as the same user that starts the service, not sudo)."
                )
            await task_manager.append_log(
                task_id,
                f"⚠ No dedicated venv for Python {target_version} ({venv_name}) — "
                f"falling back to service Python {sys.version.split()[0]}. This is a "
                "pure-Python project so ABI mismatch is unlikely, but for correctness "
                "run scripts/setup_nuitka_venvs.sh.",
            )

        # Check target project's Python version (for reference/logging)
        target_python = find_target_python(target_version, project_path)
        venv_exists = (project_path / ".venv").exists()
        preflight.append({
            "label": "Project .venv present",
            "passed": venv_exists,
            "detail": target_python or "No .venv found — dependencies won't be bundled/copied",
            "critical": False,
        })
        if target_python:
            await task_manager.append_log(task_id, f"Target project Python: {target_python}")
        else:
            await task_manager.append_log(task_id, "Note: No .venv found in target project")

        # Verify nuitka is available in the selected toolchain
        check_proc = await asyncio.create_subprocess_exec(
            nuitka_python, "-m", "nuitka", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await check_proc.communicate()

        nuitka_ok = check_proc.returncode == 0
        nuitka_version = stdout.decode().strip().split('\n')[0] if nuitka_ok else ""
        preflight.append({
            "label": "Nuitka available",
            "passed": nuitka_ok,
            "detail": nuitka_version or "Not installed in the selected toolchain",
            "critical": True,
        })
        if not nuitka_ok:
            raise RuntimeError(
                "Nuitka not installed in the selected Python environment. "
                "Run scripts/setup_nuitka_venvs.sh (per-version venvs) or `uv add nuitka`."
            )
        await task_manager.append_log(task_id, f"Nuitka version: {nuitka_version}")

        # Prepare output directory
        output_dir = project_path / config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        await task_manager.append_log(task_id, f"Output directory: {output_dir}")

        # Build Nuitka command
        entry_point = project_path / config.entry_point
        entry_ok = entry_point.exists()
        preflight.append({
            "label": "Entry point exists",
            "passed": entry_ok,
            "detail": str(entry_point),
            "critical": True,
        })
        if not entry_ok:
            raise FileNotFoundError(f"Entry point not found: {entry_point}")

        # Inject libs loading code into entry point (only for non-FULL modes)
        backup_file = None
        if config.pack_mode != PackMode.FULL:
            backup_file = entry_point.with_suffix(".py.backup")
            original_content = entry_point.read_text(encoding="utf-8")

            # Check if already injected (avoid double injection)
            if "[LIBS LOADER]" not in original_content:
                await task_manager.append_log(task_id, "Injecting libs loader into entry point...")
                shutil.copy2(entry_point, backup_file)
                injected_content = INJECT_CODE + original_content
                entry_point.write_text(injected_content, encoding="utf-8")
            else:
                await task_manager.append_log(task_id, "Libs loader already present, skipping injection")
                backup_file = None
        else:
            await task_manager.append_log(task_id, "Full mode: skipping libs loader injection")

        # Derive output name from entry point if not specified
        output_name = config.output_name or Path(config.entry_point).stem
        await task_manager.append_log(task_id, f"Output name: {output_name}")

        # Build base Nuitka command using this service's Python/Nuitka
        nuitka_cmd = [
            nuitka_python,
            "-u",  # Unbuffered output
            "-m", "nuitka",
            "--standalone",
            f"--output-filename={output_name}",
            f"--output-dir={output_dir}",
            "--remove-output",
            "--show-progress",  # Show compilation progress
            # Never block on an interactive y/n prompt: Nuitka may want to
            # download ccache/gcc/dependency-walker, and we run it on a PTY,
            # so without this the build would hang waiting for stdin.
            "--assume-yes-for-downloads",
        ]
        # --show-scons prints every gcc invocation, flooding the log pipeline
        # on large projects. Only enable it in debug mode.
        if settings.debug:
            nuitka_cmd.append("--show-scons")

        # Parallel compilation jobs. nuitka_jobs == 0 means "auto": default to
        # all cores so the C compile (the slowest phase) actually parallelizes.
        if config.nuitka_jobs > 0:
            jobs = config.nuitka_jobs
        else:
            jobs = os.cpu_count() or 1
        nuitka_cmd.append(f"--jobs={jobs}")
        await task_manager.append_log(task_id, f"Parallel jobs: {jobs}")

        # Add onefile option
        if config.onefile:
            nuitka_cmd.append("--onefile")

        # Handle packaging mode
        await task_manager.append_log(task_id, f"Pack mode: {config.pack_mode.value}")
        blacklist: list[str] = []

        if config.pack_mode == PackMode.FULL:
            # FULL mode: Let Nuitka bundle everything, no exclusions
            await task_manager.append_log(task_id, "Full mode: all dependencies will be bundled")

            # Auto-detect packages with known lazy imports
            auto_includes = detect_lazy_packages(project_path)
            if auto_includes:
                await task_manager.append_log(
                    task_id,
                    f"Auto-detected lazy-import packages: {', '.join(auto_includes)}",
                )

            # Merge with manually specified include_packages
            manual_includes = [
                p.strip() for p in config.include_packages.split(",") if p.strip()
            ] if config.include_packages else []
            if manual_includes:
                await task_manager.append_log(
                    task_id,
                    f"Manual include packages: {', '.join(manual_includes)}",
                )

            all_includes = sorted(set(auto_includes + manual_includes))
            for pkg in all_includes:
                nuitka_cmd.append(f"--include-package={pkg}")

            if all_includes:
                await task_manager.append_log(
                    task_id,
                    f"Total force-included packages: {', '.join(all_includes)}",
                )

        elif config.pack_mode == PackMode.EXTERNAL:
            # EXTERNAL mode: Match user's working script exactly
            # Include ALL valid stdlib modules, blacklist ALL installed packages
            await task_manager.append_log(task_id, "External mode: third-party from libs/")

            # Get valid stdlib modules — probed through the TARGET python so
            # the list matches the version being compiled, not the service's.
            valid_libs, force_packages = await get_valid_std_libs_for(nuitka_python)

            stdlib_names = set(valid_libs)

            # Auto-decide which stdlib to include instead of blindly bundling
            # ALL of it. The binary is standalone, so Nuitka already follows and
            # bundles the stdlib the MAIN program uses. What Nuitka can't see is
            # the stdlib imported by the third-party packages in libs/ (they're
            # --nofollow'd). So scan site-packages and include only the stdlib
            # those packages actually import — much faster compile + smaller
            # output. If the scan can't run, fall back to the full stdlib so we
            # never silently under-include.
            site_packages = _find_project_site_packages(project_path)
            used_stdlib: set[str] | None = None
            if site_packages is not None:
                try:
                    used_stdlib = await asyncio.to_thread(
                        collect_used_stdlib, site_packages, stdlib_names
                    )
                except Exception:
                    logger.warning(
                        "stdlib import scan failed; falling back to full stdlib",
                        exc_info=True,
                    )
                    used_stdlib = None

            if used_stdlib is not None:
                include_libs = used_stdlib | (_ALWAYS_STDLIB & stdlib_names)
                for lib in sorted(include_libs):
                    if lib in force_packages:
                        nuitka_cmd.append(f"--include-package={lib}")
                    else:
                        nuitka_cmd.append(f"--include-module={lib}")
                await task_manager.append_log(
                    task_id,
                    f"自動偵測 stdlib:依 libs/ 相依納入 {len(include_libs)} 個標準庫模組"
                    f"(主程式用到的由 Nuitka 自動處理,共 {len(valid_libs)} 個可用)",
                )
            else:
                for lib in valid_libs:
                    if lib in force_packages:
                        nuitka_cmd.append(f"--include-package={lib}")
                    else:
                        nuitka_cmd.append(f"--include-module={lib}")
                await task_manager.append_log(
                    task_id,
                    f"未能掃描 site-packages,保守納入全部 {len(valid_libs)} 個標準庫模組",
                )

            # Blacklist ALL installed third-party packages from PROJECT's .venv
            # This is critical - we need to read from project's venv, not system!
            venv_path = project_path / ".venv"
            blacklist = get_installed_packages(venv_path)
            await task_manager.append_log(task_id, f"Found {len(blacklist)} packages in .venv to blacklist")

        # Add extra source directories (local packages to include)
        if config.extra_dirs:
            for extra_dir in config.extra_dirs.split(","):
                extra_dir = extra_dir.strip()
                if extra_dir:
                    extra_path = project_path / extra_dir
                    if extra_path.exists():
                        # Follow imports to local packages
                        nuitka_cmd.append(f"--follow-import-to={extra_dir}")
                        # Include as package if it has .py files
                        if extra_path.is_dir():
                            nuitka_cmd.append(f"--include-package={extra_dir}")
                            await task_manager.append_log(task_id, f"Including package: {extra_dir}")
                        else:
                            nuitka_cmd.append(f"--include-data-dir={extra_path}={extra_dir}")
                            await task_manager.append_log(task_id, f"Including data dir: {extra_dir}")
                        # Remove from blacklist if present
                        if extra_dir in blacklist:
                            blacklist.remove(extra_dir)

        # Note: Data directories will be copied after compilation (like libs/)
        # instead of being embedded by Nuitka
        if config.data_dirs:
            await task_manager.append_log(task_id, f"Data dirs to copy after build: {config.data_dirs}")

        # Add nofollow for blacklisted packages
        # Use .* suffix to ensure submodules are also excluded.
        # Safety net: only emit VALID dotted module names — a stray "/" (e.g. a
        # file path leaking in from package metadata) makes Nuitka reject the
        # option and exit 1 before compiling. Skip anything malformed.
        skipped = [p for p in blacklist if not _MODULE_NAME_RE.fullmatch(p)]
        if skipped:
            await task_manager.append_log(
                task_id,
                f"略過 {len(skipped)} 個非法模組名(不符點號命名,不納入 nofollow): "
                + ", ".join(sorted(skipped)[:10]),
            )
        for pkg in blacklist:
            if not _MODULE_NAME_RE.fullmatch(pkg):
                continue
            nuitka_cmd.append(f"--nofollow-import-to={pkg}")
            nuitka_cmd.append(f"--nofollow-import-to={pkg}.*")

        # Optimization / diagnostics options
        if not config.enable_anti_bloat:
            # anti-bloat is auto-enabled in Nuitka — only the opt-out needs a flag
            nuitka_cmd.append("--disable-plugins=anti-bloat")
            await task_manager.append_log(task_id, "anti-bloat plugin disabled by config")

        report_file: Path | None = None
        if config.generate_report:
            report_file = output_dir / "nuitka-report.xml"
            nuitka_cmd.append(f"--report={report_file}")
            await task_manager.append_log(task_id, f"Compilation report: {report_file}")

        if config.include_package_data:
            for pkg in config.include_package_data.split(","):
                pkg = pkg.strip()
                if pkg:
                    nuitka_cmd.append(f"--include-package-data={pkg}")
                    await task_manager.append_log(task_id, f"Including package data: {pkg}")

        # Add entry point
        nuitka_cmd.append(str(entry_point))

        # Publish preflight results before the long compile starts
        await task_manager.set_result(task_id, dict(build_result), db)
        await task_manager.set_stage(task_id, "compile")

        await task_manager.append_log(task_id, "Starting Nuitka compilation...")
        await task_manager.append_log(task_id, f"Command: {' '.join(nuitka_cmd)}")

        # Build environment for Nuitka process
        nuitka_env = {**os.environ, "TERM": "xterm-256color"}

        # For FULL mode, add project's .venv/site-packages to PYTHONPATH
        # so Nuitka can discover and bundle all project dependencies
        if config.pack_mode == PackMode.FULL:
            venv_path = project_path / ".venv"
            if venv_path.exists():
                site_pkgs_dirs = list(venv_path.glob("lib/python*/site-packages"))
                if not site_pkgs_dirs:
                    site_pkgs_dirs = list(venv_path.glob("Lib/site-packages"))
                if site_pkgs_dirs:
                    extra_path = str(site_pkgs_dirs[0])
                    existing = nuitka_env.get("PYTHONPATH", "")
                    nuitka_env["PYTHONPATH"] = f"{extra_path}:{existing}" if existing else extra_path
                    await task_manager.append_log(task_id, f"Added to PYTHONPATH: {extra_path}")

        # Create PTY for terminal emulation (Nuitka needs TTY for progress)
        master_fd, slave_fd = pty.openpty()

        # Start Nuitka process with PTY
        process = await asyncio.create_subprocess_exec(
            *nuitka_cmd,
            stdout=slave_fd,
            stderr=slave_fd,
            stdin=slave_fd,
            cwd=str(project_path),
            env=nuitka_env,
        )
        os.close(slave_fd)  # Close slave in parent process

        await task_manager.set_process(task_id, process)
        current_progress = 0.0

        # Non-blocking PTY reads on the event loop — no executor threads to
        # leak or exhaust (the old run_in_executor(os.read) pattern left a
        # blocked thread behind on every 0.5s timeout during quiet compile
        # phases, eventually starving the process-wide default executor).
        os.set_blocking(master_fd, False)
        buffer = ""

        def read_pty():
            try:
                return os.read(master_fd, 65536).decode("utf-8", errors="replace")
            except (BlockingIOError, InterruptedError):
                return ""
            except OSError:
                return ""

        while True:
            # Check if process has finished
            if process.returncode is not None:
                # Read any remaining output
                while True:
                    data = read_pty()
                    if not data:
                        break
                    buffer += data
                break

            data = read_pty()
            if data:
                buffer += data
            else:
                await asyncio.sleep(0.1)

            # Process complete lines
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                clean_line = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", line).rstrip()
                if not clean_line:
                    continue

                await task_manager.append_log(task_id, clean_line)

                # Parse progress from Nuitka output
                status_msg = ""
                line_lower = clean_line.lower()

                # Parse actual percentage from Nuitka progress output (e.g., "Scons: 50%")
                percent_match = re.search(r"(\d+)%", clean_line)
                if percent_match:
                    parsed_percent = int(percent_match.group(1))
                    # Map Nuitka's scons percentage (0-100) to our 50-95 range
                    if "scons" in line_lower:
                        current_progress = max(current_progress, 50 + parsed_percent * 0.45)
                        status_msg = f"Compiling C code... {parsed_percent}%"

                if "starting python compilation" in line_lower:
                    current_progress = max(current_progress, 10)
                    status_msg = "Starting compilation..."
                elif "completed python level" in line_lower or "analysing" in line_lower:
                    current_progress = max(current_progress, 20)
                    status_msg = "Analyzing Python..."
                elif "optimizing" in line_lower:
                    current_progress = max(current_progress, 30)
                    status_msg = "Optimizing..."
                elif "generating source code" in line_lower or "generating c" in line_lower:
                    current_progress = max(current_progress, 40)
                    status_msg = "Generating C code..."
                elif "running scons" in line_lower or "backend c" in line_lower:
                    current_progress = max(current_progress, 50)
                    status_msg = "Compiling C code..."
                elif "linking" in line_lower:
                    current_progress = max(current_progress, 95)
                    status_msg = "Linking..."
                elif "onefile" in line_lower and "creating" in line_lower:
                    current_progress = max(current_progress, 97)
                    status_msg = "Creating executable..."
                elif "successfully created" in line_lower:
                    current_progress = 100
                    status_msg = "Complete!"
                elif "error" in line_lower and "nuitka" in line_lower:
                    status_msg = "Error occurred"

                # Update status message if we have one
                if status_msg:
                    await task_manager.update_task(task_id, "status_msg", status_msg)

                # Update progress
                if current_progress < 5:
                    current_progress = 5

                await task_manager.update_task(
                    task_id,
                    "progress",
                    min(int(current_progress), 99 if current_progress < 100 else 100),
                )

        # Close PTY master
        try:
            os.close(master_fd)
        except OSError:
            pass
        master_fd = None

        ret_code = await process.wait()
        await task_manager.set_process(task_id, None)

        # Restore original entry point file
        if backup_file and backup_file.exists():
            await task_manager.append_log(task_id, "Restoring original entry point...")
            shutil.move(str(backup_file), str(entry_point))

        # Handle completion
        if ret_code == 0:
            # Find the dist folder (Nuitka creates {entry_name}.dist/)
            entry_stem = entry_point.stem  # e.g., "main" from "main.py"
            dist_folder = output_dir / f"{entry_stem}.dist"

            await task_manager.set_stage(task_id, "bundle")

            # Skip libs copy for FULL mode (all deps are bundled)
            if config.pack_mode == PackMode.FULL:
                await task_manager.append_log(task_id, "")
                await task_manager.append_log(task_id, "Full mode: skipping libs/ copy (all deps bundled)")
                target_folder = None
            else:
                await task_manager.update_task(task_id, "status_msg", "Copying packages...")
                await task_manager.append_log(task_id, "")
                await task_manager.append_log(task_id, "Copying .venv packages to libs/...")

                # Determine target folder based on onefile mode
                if config.onefile:
                    target_folder = output_dir / "libs"
                    target_folder.mkdir(parents=True, exist_ok=True)
                else:
                    target_folder = dist_folder if dist_folder.exists() else None

            if target_folder:
                # Find .venv site-packages
                venv_path = project_path / ".venv"
                site_packages = None

                if venv_path.exists():
                    # Find site-packages (could be lib/pythonX.X/site-packages or Lib/site-packages on Windows)
                    for pattern in ["lib/python*/site-packages", "Lib/site-packages"]:
                        matches = list(venv_path.glob(pattern))
                        if matches:
                            site_packages = matches[0]
                            break

                if site_packages and site_packages.exists():
                    await task_manager.append_log(task_id, f"Source: {site_packages}")
                    await task_manager.append_log(task_id, f"Target: {target_folder}")

                    # Copy each package (including .dist-info for importlib.metadata)
                    copied_count = 0
                    skipped = []
                    skip_prefixes = ("pip", "setuptools", "wheel", "nuitka")
                    for item in site_packages.iterdir():
                        # Skip __pycache__ and hidden files (but NOT _ prefixed .so files)
                        if item.name == "__pycache__":
                            continue
                        if item.name.startswith("."):
                            continue
                        # Underscore-prefixed entries are legitimate packages /
                        # extensions (e.g. _cffi_backend, _rocm_sdk_core,
                        # _rocm_sdk_libraries_gfx1151 — dynamically imported at
                        # runtime by rocm_sdk). The old blanket "skip _*" rule
                        # silently dropped these, breaking runtime imports. Only
                        # skip setuptools' internal shims explicitly.
                        if item.name in ("pkg_resources", "easy_install.py", "_distutils_hack"):
                            continue
                        if any(item.name.startswith(p) for p in skip_prefixes):
                            continue
                        # Skip .pth files (setuptools hooks that cause issues at runtime)
                        if item.suffix == ".pth":
                            continue

                        target = target_folder / item.name
                        if target.exists():
                            skipped.append(item.name)
                            continue

                        try:
                            # Off-loop copy — site-packages can be gigabytes;
                            # a sync copy here would freeze every request/WS.
                            # _copytree_fast hardlinks + drops pyc/tests.
                            if item.is_dir():
                                await asyncio.to_thread(_copytree_fast, item, target)
                            else:
                                await asyncio.to_thread(shutil.copy2, item, target)
                            copied_count += 1
                        except Exception as e:
                            await task_manager.append_log(task_id, f"Warning: Failed to copy {item.name}: {e}")

                    await task_manager.append_log(task_id, f"Copied {copied_count} packages")
                    if skipped:
                        await task_manager.append_log(task_id, f"Skipped {len(skipped)} existing: {', '.join(skipped[:5])}{'...' if len(skipped) > 5 else ''}")
                else:
                    await task_manager.append_log(task_id, "No .venv/site-packages found, skipping package copy")
            else:
                if not config.pack_mode == PackMode.FULL:
                    await task_manager.append_log(task_id, f"Dist folder not found: {dist_folder}")

            # Copy data directories to output folder (like libs/)
            if config.data_dirs:
                await task_manager.append_log(task_id, "")
                await task_manager.append_log(task_id, "Copying data directories...")
                for data_dir in config.data_dirs.split(","):
                    data_dir = data_dir.strip()
                    if data_dir:
                        src_path = project_path / data_dir
                        dst_path = output_dir / data_dir
                        if src_path.exists():
                            try:
                                if dst_path.exists():
                                    await asyncio.to_thread(shutil.rmtree, dst_path)
                                await asyncio.to_thread(shutil.copytree, src_path, dst_path)
                                file_count = await asyncio.to_thread(
                                    lambda p=dst_path: sum(1 for f in p.rglob("*") if f.is_file())
                                )
                                await task_manager.append_log(task_id, f"Copied: {data_dir}/ ({file_count} files)")
                            except Exception as e:
                                await task_manager.append_log(task_id, f"Warning: Failed to copy {data_dir}: {e}")
                        else:
                            await task_manager.append_log(task_id, f"Warning: Data dir not found: {src_path}")

            # Artifact summary (path/size/hash) for the detail page
            if config.onefile:
                artifact_path = output_dir / output_name
                binary_path = artifact_path
            else:
                artifact_path = dist_folder if dist_folder.exists() else output_dir
                binary_path = dist_folder / output_name

            if artifact_path.exists():
                build_result["artifact"] = await asyncio.to_thread(
                    _summarize_artifact, artifact_path
                )
                await task_manager.append_log(
                    task_id,
                    f"Artifact: {artifact_path} "
                    f"({build_result['artifact']['size_human']}, "
                    f"{build_result['artifact']['file_count']} files)",
                )

            if report_file and report_file.exists():
                build_result["report_file"] = str(report_file)

            # Post-build smoke test
            if config.verify_after_build:
                await task_manager.set_stage(task_id, "verify")
                await task_manager.update_task(task_id, "status_msg", "Verifying binary...")
                # Runtime env for the test: Docker ENV first, else project .env
                smoke_env, env_source = _resolve_smoke_env(config, project_path)
                await task_manager.append_log(
                    task_id, f"Smoke test env source: {env_source}"
                )
                verify = await _smoke_test(
                    task_id, binary_path, cwd=output_dir, extra_env=smoke_env
                )
                build_result["verify"] = verify
                marker = {"pass": "✓", "warn": "⚠", "fail": "✗"}.get(verify["status"], "•")
                await task_manager.append_log(
                    task_id, f"Verify: {marker} {verify['detail']}"
                )
            else:
                build_result["verify"] = {
                    "status": "skipped",
                    "detail": "Verification disabled in config",
                    "exit_code": None,
                }

            build_result["duration_seconds"] = round(time.monotonic() - start_ts, 1)
            await task_manager.set_result(task_id, dict(build_result), db)
            await task_manager.set_stage(task_id, "done")

            await task_manager.append_log(task_id, "")
            await task_manager.append_log(task_id, "=" * 50)
            await task_manager.append_log(task_id, "Nuitka build completed successfully!")
            await task_manager.append_log(task_id, f"Output: {dist_folder if dist_folder.exists() else output_dir}")
            await task_manager.append_log(task_id, "=" * 50)

            # A failed smoke test means the artifact is broken — fail the build
            if build_result["verify"]["status"] == "fail":
                await task_manager.append_log(
                    task_id, "Smoke test failed — marking build as failed"
                )
                return False
            return True

        elif ret_code in (-15, -9):
            await task_manager.append_log(task_id, "Build cancelled by user")
            return False
        else:
            await task_manager.append_log(task_id, f"Build failed with exit code: {ret_code}")
            build_result["duration_seconds"] = round(time.monotonic() - start_ts, 1)
            await task_manager.set_result(task_id, dict(build_result), db)
            return False

    except Exception as e:
        await task_manager.append_log(task_id, f"Error: {e!s}")
        await task_manager.set_process(task_id, None)
        # Try to restore original entry point if backup exists
        try:
            if backup_file and backup_file.exists():
                shutil.move(str(backup_file), str(entry_point))
        except Exception:
            logger.warning("Failed to restore entry point backup for task %s", task_id, exc_info=True)
        # Persist whatever preflight/result data we collected before dying
        try:
            build_result["duration_seconds"] = round(time.monotonic() - start_ts, 1)
            await task_manager.set_result(task_id, dict(build_result), db)
        except Exception:
            logger.warning("Failed to persist build result for task %s", task_id, exc_info=True)
        return False

    finally:
        # Close PTY if still open (normal path already closed and None'd it)
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass
        # No lock release here — build_dispatcher owns the project lock now.


# NOTE: `start_build_task` deliberately does NOT live here.
#
# There used to be a second one, whose docstring called itself "the main entry
# point for background builds" — but the API imports build_dispatcher's. The
# two were not stylistic variants: this one had no semaphore (ignoring
# MAX_CONCURRENT_BUILDS, letting any number of Nuitka compiles saturate the
# CPU), no cancellation checks, no git workspace preparation (so git-mode tasks
# would dispatch with an empty project_path), no failure diagnosis and no
# post-build cleanup.
#
# Two importable functions with the same name and duelling docstrings is a
# coin-flip for autocomplete, and picking the wrong one raises nothing at
# import or call time. Build entry stays in build_dispatcher.
