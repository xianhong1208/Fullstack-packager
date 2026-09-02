"""Git operations for the Git-URL source mode.

SECURITY INVARIANTS (do not violate):
    1. GitLab tokens are SERVER-ONLY. They come from settings.gitlab_token,
       are injected into the URL only at subprocess call time, and are
       never persisted to DB, returned via API, or logged unmasked.
    2. All subprocess calls use argv-list form with shell=False. No string
       interpolation into a shell.
    3. All external URLs are validated via validate_git_url() before ANY
       network operation. SSRF vectors (file://, ssh://, git://, localhost,
       link-local, userinfo in URL, '..' in path) are rejected.
    4. Long-running operations (ls-remote, clone) are wrapped in
       asyncio.wait_for() with timeouts; timed-out processes are killed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import shutil
import tempfile
import tomllib
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse, urlunparse

# Alias the argv-list subprocess spawner used by the rest of the codebase
# (see app/services/nuitka_worker.py). Using an alias keeps call sites short
# and avoids ambiguity with shell-based launchers.
_spawn = asyncio.create_subprocess_exec


# ---------- URL validation ----------

_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOSTNAMES = {"localhost", "ip6-localhost", "ip6-loopback"}


class GitUrlError(ValueError):
    """Raised when a provided Git URL fails validation."""


def _current_allowed_hosts() -> list[str]:
    """Fetch the current GIT_ALLOWED_HOSTS from settings.

    Used by the async git operations below so that even direct internal
    callers (not via the HTTP route) get the same allowlist enforcement
    as the route handlers. Import locally to avoid a top-level cycle.
    """
    from app.config import get_settings
    return get_settings().allowed_git_hosts


def validate_git_url(url: str, allowed_hosts: list[str] | None = None) -> None:
    """Validate a Git HTTP(S) URL for use with our cloning pipeline.

    Raises GitUrlError with a human-readable message on failure.
    Returns None on success.

    Always rejects:
    - Non-http/https schemes (file, ssh, git, etc.)
    - URLs that already embed userinfo (we inject our own token)
    - localhost / ip6-localhost (even if explicitly allowlisted)
    - Path components containing '..'

    Allowlist semantics:
    - If ``allowed_hosts`` is a non-empty list, the URL's host MUST be in
      it (IP literals or hostnames). Membership here also acts as an
      explicit opt-in to IPs that would otherwise be blocked by SSRF
      defences below (e.g. private-range internal GitLab instances).
    - If ``allowed_hosts`` is None/empty, strict SSRF heuristics apply:
      IP literals pointing at loopback / link-local / private ranges /
      multicast / reserved / unspecified are rejected.
    """
    if not url or not isinstance(url, str):
        raise GitUrlError("Git URL is required")

    url = url.strip()
    if len(url) > 2048:
        raise GitUrlError("Git URL is too long")

    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise GitUrlError(f"Git URL is malformed: {e}") from e

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise GitUrlError(
            f"Unsupported URL scheme '{parsed.scheme}'. Only http and https are allowed."
        )

    if parsed.username or parsed.password:
        # We inject our own token. A user-supplied credential in the URL is
        # almost always a mistake or an attempt to bypass policy.
        raise GitUrlError(
            "Git URL must not contain embedded credentials (username/password). "
            "The server injects its own token automatically."
        )

    host = (parsed.hostname or "").lower()
    if not host:
        raise GitUrlError("Git URL is missing a host component")

    if ".." in (parsed.path or ""):
        raise GitUrlError("Git URL path must not contain '..'")

    # Always-blocked hostnames — no allowlist override.
    if host in _BLOCKED_HOSTNAMES:
        raise GitUrlError(f"Git URL host '{host}' is not allowed")

    # Allowlist semantics: explicit membership grants trust and short-circuits
    # the SSRF heuristics below. This lets operators opt into internal
    # GitLab instances that live on private RFC-1918 addresses.
    if allowed_hosts:
        if host not in allowed_hosts:
            raise GitUrlError(
                f"Git URL host '{host}' is not in the allowlist. "
                f"Permitted hosts: {', '.join(allowed_hosts)}"
            )
        return  # trusted — skip IP range checks

    # No allowlist configured — apply strict SSRF defence for IP literals.
    # Hostnames are NOT resolved here (DNS-based SSRF isn't handled);
    # operators who need stricter control should populate GIT_ALLOWED_HOSTS.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_private
        or ip.is_multicast
        or ip.is_reserved
    ):
        raise GitUrlError(
            f"Git URL host '{host}' is not a routable public address. "
            "If this is an intentional internal target, add it to GIT_ALLOWED_HOSTS."
        )


# ---------- Ref name validation ----------

# A branch/tag name used as a positional arg to `git clone --branch ...`.
# Defense in depth: even though we pass it via argv (not shell), reject
# values that could be mistaken for an option, contain whitespace, or
# escape a directory.
_REF_BAD_CHARS = re.compile(r"[\s\x00-\x1f~^:?*\[\\]")


def validate_git_ref(ref: str) -> None:
    """Raise GitUrlError if `ref` is unsafe to pass to git.

    Rules:
    - Must be 1..255 chars
    - Must not start with '-' (would be interpreted as a git option)
    - Must not contain whitespace, control chars, or git-forbidden chars
    - Must not contain '..'
    """
    if not ref or not isinstance(ref, str):
        raise GitUrlError("Git ref (branch or tag) is required")
    if len(ref) > 255:
        raise GitUrlError("Git ref is too long")
    if ref.startswith("-"):
        raise GitUrlError("Git ref must not start with '-'")
    if ".." in ref:
        raise GitUrlError("Git ref must not contain '..'")
    if _REF_BAD_CHARS.search(ref):
        raise GitUrlError("Git ref contains forbidden characters")


# ---------- Token injection & masking ----------

# Username portion of the injected userinfo, per provider. GitHub authenticates a
# token as "x-access-token"; GitLab (and self-hosted) use "oauth2". Both accept a
# Personal Access Token as the password.
_PROVIDER_USERINFO_USER = {"github": "x-access-token", "gitlab": "oauth2", "generic": "oauth2"}


def inject_token(url: str, token: str | None, provider: str = "gitlab") -> str:
    """Return `url` with `<user>:{token}@` injected into the authority.

    The username depends on the provider (GitHub -> x-access-token, GitLab -> oauth2).
    If token is None/empty, return `url` unchanged. The returned string contains a
    secret and must never be logged or persisted; use it only as an argv to a single
    subprocess call.
    """
    if not token:
        return url
    user = _PROVIDER_USERINFO_USER.get(provider, "oauth2")
    parsed = urlparse(url)
    host = parsed.hostname or ""
    netloc = f"{user}:{token}@{host}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


_OAUTH_PATTERN = re.compile(r"((?:oauth2|x-access-token):)[^@\s/]+(@)")


def mask_token_in_log(text: str, token: str | None = None) -> str:
    """Return `text` with any occurrence of the token or an oauth2 userinfo
    triple replaced by '***'. Safe to call on subprocess stderr/stdout
    before passing it to logs, notifications, or error messages.
    """
    if not text:
        return text
    masked = text
    if token:
        masked = masked.replace(token, "***")
    masked = _OAUTH_PATTERN.sub(r"\1***\2", masked)
    return masked


# ---------- Subprocess environment ----------

def _git_env() -> dict[str, str]:
    """Minimal environment for git subprocesses.

    GIT_TERMINAL_PROMPT=0 and GIT_ASKPASS=/bin/true ensure that git NEVER
    blocks on an interactive credential prompt — bad tokens fail fast
    instead of hanging the request.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/true",
    }


# ---------- list_refs ----------

async def list_refs(
    url: str,
    token: str | None,
    *,
    provider: str = "gitlab",
    timeout: float = 30.0,
) -> dict[str, list[str]]:
    """Return {"branches": [...], "tags": [...]} for a remote.

    Uses `git ls-remote --heads --tags <url>` — no clone, no working tree.
    Output is de-duplicated and sorted; annotated-tag '^{}' peelings are
    collapsed to the tag name. All errors are masked before re-raising.
    """
    validate_git_url(url, _current_allowed_hosts())
    injected = inject_token(url, token, provider)

    cmd = ["git", "ls-remote", "--heads", "--tags", injected]
    try:
        proc = await _spawn(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_git_env(),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise GitUrlError(f"git ls-remote timed out after {timeout:.0f}s")
    except FileNotFoundError as e:
        raise GitUrlError("git binary not found on server") from e

    if proc.returncode != 0:
        stderr = mask_token_in_log(stderr_bytes.decode("utf-8", "replace"), token)
        raise GitUrlError(f"git ls-remote failed: {stderr.strip() or 'unknown error'}")

    stdout = stdout_bytes.decode("utf-8", "replace")

    branches: set[str] = set()
    tags: set[str] = set()
    for line in stdout.splitlines():
        # Format: "<sha>\trefs/heads/<name>" or "refs/tags/<name>" or "refs/tags/<name>^{}"
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        ref = parts[1]
        if ref.startswith("refs/heads/"):
            branches.add(ref[len("refs/heads/"):])
        elif ref.startswith("refs/tags/"):
            name = ref[len("refs/tags/"):]
            if name.endswith("^{}"):
                name = name[:-3]
            tags.add(name)

    return {
        "branches": sorted(branches),
        "tags": sorted(tags),
    }


# ---------- clone_repo ----------

async def clone_repo(
    url: str,
    ref: str,
    ref_type: Literal["branch", "tag"],
    target_dir: Path,
    token: str | None,
    *,
    provider: str = "gitlab",
    timeout: float = 600.0,
) -> None:
    """Clone `url` @ `ref` into `target_dir` (which must not yet exist).

    Uses `--depth 1 --single-branch --branch <ref>` for speed.
    On any error, partial state at target_dir is removed and a masked
    exception is raised.
    """
    validate_git_url(url, _current_allowed_hosts())
    validate_git_ref(ref)
    if ref_type not in ("branch", "tag"):
        raise GitUrlError(f"Invalid ref_type '{ref_type}'")

    target_dir = Path(target_dir)
    if target_dir.exists():
        raise GitUrlError(f"Clone target already exists: {target_dir}")

    target_dir.parent.mkdir(parents=True, exist_ok=True)

    injected = inject_token(url, token, provider)
    cmd = [
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--branch",
        ref,
        injected,
        str(target_dir),
    ]

    try:
        proc = await _spawn(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_git_env(),
        )
        try:
            _, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            _safe_rmtree(target_dir)
            raise GitUrlError(f"git clone timed out after {timeout:.0f}s")
    except FileNotFoundError as e:
        _safe_rmtree(target_dir)
        raise GitUrlError("git binary not found on server") from e

    if proc.returncode != 0:
        stderr = mask_token_in_log(stderr_bytes.decode("utf-8", "replace"), token)
        _safe_rmtree(target_dir)
        raise GitUrlError(f"git clone failed: {stderr.strip() or 'unknown error'}")


def _safe_rmtree(path: Path) -> None:
    """Best-effort recursive delete; swallows errors so cleanup never masks
    the original exception."""
    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


# ---------- pyproject dependency-groups ----------


def parse_dependency_groups(toml_text: str) -> list[str]:
    """Return the sorted PEP 735 dependency-group names from pyproject text.

    Reads the ``[dependency-groups]`` table (NOT ``[project.optional-dependencies]``).
    Returns an empty list when the table is absent or the TOML can't be parsed,
    so callers can treat "no groups" and "unreadable" identically — the picker
    just shows nothing to select.
    """
    try:
        data = tomllib.loads(toml_text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []
    groups = data.get("dependency-groups")
    if not isinstance(groups, dict):
        return []
    return sorted(groups.keys())


# ---------- scan_tree ----------

# Directories to hide from the scan result — mirrors the exclusion list used
# by the existing /api/directories endpoint so local-mode and git-mode give
# users the same menu of choices.
_SCAN_EXCLUDED_DIRS = {
    "__pycache__", ".git", ".svn", ".hg",
    "node_modules", ".venv", "venv", "env",
    ".idea", ".vscode", ".mypy_cache", ".pytest_cache", ".tox",
    "dist", "build",
}


async def scan_tree(
    url: str,
    ref: str,
    ref_type: Literal["branch", "tag"],
    token: str | None,
    *,
    provider: str = "gitlab",
    timeout: float = 30.0,
) -> dict:
    """Return top-level directory names + pyproject dependency-groups for ``url @ ref``.

    Returns ``{"directories": [...], "dependency_groups": [...]}``.

    Implementation is a "treeless" partial clone + no-checkout:
      git clone --depth 1 --filter=blob:none --no-checkout
                --single-branch --branch <ref> <url> <tmp>
      git -C <tmp> ls-tree -d --name-only HEAD

    The clone fetches only commit + tree objects — no blobs, no working tree —
    so listing dirs is fast (<1 s) and cheap. Reading the root pyproject.toml
    uses ``git show HEAD:pyproject.toml``, which lazily fetches just that one
    blob from the promisor remote (missing file → no groups, not an error).
    The temporary clone is removed unconditionally before returning.
    """
    validate_git_url(url, _current_allowed_hosts())
    validate_git_ref(ref)
    if ref_type not in ("branch", "tag"):
        raise GitUrlError(f"Invalid ref_type '{ref_type}'")

    # Dedicated per-call temp dir — never collides with concurrent scans.
    tmp_root = Path(tempfile.mkdtemp(prefix="bc_scan_"))
    target = tmp_root / "repo"
    try:
        injected = inject_token(url, token, provider)
        clone_cmd = [
            "git",
            "clone",
            "--depth", "1",
            "--filter=blob:none",
            "--no-checkout",
            "--single-branch",
            "--branch", ref,
            injected,
            str(target),
        ]
        try:
            proc = await _spawn(
                *clone_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_git_env(),
            )
            try:
                _, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise GitUrlError(f"git scan-tree clone timed out after {timeout:.0f}s")
        except FileNotFoundError as e:
            raise GitUrlError("git binary not found on server") from e

        if proc.returncode != 0:
            stderr = mask_token_in_log(stderr_bytes.decode("utf-8", "replace"), token)
            raise GitUrlError(
                f"git scan-tree clone failed: {stderr.strip() or 'unknown error'}"
            )

        # List top-level dirs via ls-tree (reads tree objects only, does not
        # trigger lazy blob fetch).
        ls_cmd = ["git", "ls-tree", "-d", "--name-only", "HEAD"]
        proc2 = await _spawn(
            *ls_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_git_env(),
            cwd=str(target),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc2.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc2.kill()
            await proc2.wait()
            raise GitUrlError(f"git ls-tree timed out after {timeout:.0f}s")

        if proc2.returncode != 0:
            stderr = mask_token_in_log(stderr_bytes.decode("utf-8", "replace"), token)
            raise GitUrlError(
                f"git ls-tree failed: {stderr.strip() or 'unknown error'}"
            )

        names: list[str] = []
        for raw in stdout_bytes.decode("utf-8", "replace").splitlines():
            name = raw.strip()
            if not name:
                continue
            # Exclude hidden dirs and known junk, matching list_directories
            if name.startswith(".") and name != ".streamlit":
                continue
            if name.lower() in _SCAN_EXCLUDED_DIRS or name.endswith(".egg-info"):
                continue
            names.append(name)

        # Read root pyproject.toml (lazy single-blob fetch) for PEP 735 groups.
        # Any failure — file absent, fetch error, parse error — degrades to an
        # empty group list rather than failing the whole scan.
        dependency_groups: list[str] = []
        show_cmd = ["git", "show", "HEAD:pyproject.toml"]
        try:
            proc3 = await _spawn(
                *show_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_git_env(),
                cwd=str(target),
            )
            pyproject_bytes, _ = await asyncio.wait_for(
                proc3.communicate(), timeout=timeout
            )
            if proc3.returncode == 0:
                dependency_groups = parse_dependency_groups(
                    pyproject_bytes.decode("utf-8", "replace")
                )
        except (asyncio.TimeoutError, FileNotFoundError, OSError):
            dependency_groups = []

        # Classify subdirectories (source/data/skip) from the full file list —
        # git mode's equivalent of local analyze_project_layout. ls-tree -r
        # reads tree objects only (no blob fetch), so contents aren't available
        # and there's no import analysis; best-effort, never fails the scan.
        analysis: dict = {
            "directories": [],
            "entry_imports": [],
            "suggested_extra_dirs": [],
            "suggested_data_dirs": [],
        }
        try:
            lsr_cmd = ["git", "ls-tree", "-r", "--name-only", "HEAD"]
            proc4 = await _spawn(
                *lsr_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_git_env(),
                cwd=str(target),
            )
            lsr_bytes, _ = await asyncio.wait_for(proc4.communicate(), timeout=timeout)
            if proc4.returncode == 0:
                from app.services.project_analysis import classify_from_paths

                paths = lsr_bytes.decode("utf-8", "replace").splitlines()
                analysis = classify_from_paths(paths)
        except Exception:
            pass

        return {
            "directories": sorted(names),
            "analysis": analysis,
            "dependency_groups": dependency_groups,
        }
    finally:
        _safe_rmtree(tmp_root)


# ---------- preview_frontend ----------

# Matches the /api/list-env-files + /api/env-file behavior in routes/tasks.py.
_ENV_FILENAME_RE = re.compile(r"^\.env(\.\w+)*$")


def _detect_frontend_config(fe_path: Path) -> dict:
    """Read package.json + vite.config to infer build tool, build command,
    and outDir. Ported from /api/detect-frontend-config in routes/tasks.py
    so that git-mode preview gives the same shape of result as local mode.
    """
    import json as _json

    result: dict = {
        "detected": False,
        "build_tool": None,
        "build_command": None,
        "output_dir": None,
        "scripts": [],
        "has_vite_config": False,
    }

    # 1. vite.config.*
    for cfg_name in ("vite.config.ts", "vite.config.js", "vite.config.mts", "vite.config.mjs"):
        cfg_file = fe_path / cfg_name
        if cfg_file.exists():
            result["has_vite_config"] = True
            try:
                content = cfg_file.read_text(encoding="utf-8")
                match = re.search(r"""outDir\s*:\s*['"](.+?)['"]""", content)
                if match:
                    result["output_dir"] = match.group(1)
            except Exception:
                pass
            break

    # 2. package.json
    pkg_json = fe_path / "package.json"
    if pkg_json.exists():
        try:
            pkg = _json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = pkg.get("scripts", {})
            result["scripts"] = list(scripts.keys())
            for cmd in ("build", "build:prod", "build:production"):
                if cmd in scripts:
                    result["build_command"] = cmd
                    break
            pm_field = pkg.get("packageManager", "")
            if pm_field:
                pm_name = pm_field.split("@")[0].strip().lower()
                if pm_name in ("npm", "yarn", "pnpm", "bun"):
                    result["build_tool"] = pm_name
        except Exception:
            pass

    # 3. Lockfile fallback
    if not result["build_tool"]:
        lock_map = {
            "bun.lockb": "bun",
            "bun.lock": "bun",
            "pnpm-lock.yaml": "pnpm",
            "yarn.lock": "yarn",
            "package-lock.json": "npm",
        }
        for lock_file, tool in lock_map.items():
            if (fe_path / lock_file).exists():
                result["build_tool"] = tool
                break

    result["detected"] = bool(
        result["has_vite_config"] or result["build_tool"] or result["build_command"]
    )
    return result


def _read_env_files(fe_path: Path, max_size: int = 100_000) -> dict[str, str]:
    """Return a mapping of `.env*` filename -> content (UTF-8).

    Files larger than ``max_size`` bytes are skipped silently — env files are
    tiny by convention and a huge one usually means the filename pattern got
    lucky on something else (bug, committed data, etc.).
    """
    result: dict[str, str] = {}
    try:
        for item in sorted(fe_path.iterdir()):
            if not item.is_file():
                continue
            if not _ENV_FILENAME_RE.match(item.name):
                continue
            try:
                if item.stat().st_size > max_size:
                    continue
                result[item.name] = item.read_text(encoding="utf-8")
            except Exception:
                continue
    except Exception:
        pass
    return result


async def preview_frontend(
    url: str,
    ref: str,
    ref_type: Literal["branch", "tag"],
    token: str | None,
    frontend_dir: str = ".",
    *,
    provider: str = "gitlab",
    timeout: float = 120.0,
    max_env_file_size: int = 100_000,
) -> dict:
    """Shallow-clone a repo to temp and return everything the CreateTask
    form needs to populate the Frontend Settings card in git mode:

    Returns:
        {
            "frontend_config": { detected, build_tool, build_command,
                                 output_dir, scripts, has_vite_config },
            "env_files":       { ".env.example": "content...", ... },
            "frontend_dir_exists": bool,
        }

    Unlike ``scan_tree``, this function DOES download blobs — we need to
    read ``package.json`` / ``vite.config.*`` / ``.env*`` file contents.
    """
    validate_git_url(url, _current_allowed_hosts())
    validate_git_ref(ref)
    if ref_type not in ("branch", "tag"):
        raise GitUrlError(f"Invalid ref_type '{ref_type}'")

    # Defence in depth: frontend_dir is used as a relative path below.
    if ".." in frontend_dir or frontend_dir.startswith("/"):
        raise GitUrlError("frontend_dir must be a relative path without '..'")

    tmp_root = Path(tempfile.mkdtemp(prefix="bc_preview_fe_"))
    target = tmp_root / "repo"
    try:
        injected = inject_token(url, token, provider)
        clone_cmd = [
            "git",
            "clone",
            "--depth", "1",
            "--single-branch",
            "--branch", ref,
            injected,
            str(target),
        ]
        try:
            proc = await _spawn(
                *clone_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_git_env(),
            )
            try:
                _, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise GitUrlError(
                    f"preview-frontend clone timed out after {timeout:.0f}s"
                )
        except FileNotFoundError as e:
            raise GitUrlError("git binary not found on server") from e

        if proc.returncode != 0:
            stderr = mask_token_in_log(stderr_bytes.decode("utf-8", "replace"), token)
            raise GitUrlError(
                f"preview-frontend clone failed: {stderr.strip() or 'unknown error'}"
            )

        # Resolve frontend dir within the clone, reject anything that escapes.
        target_resolved = target.resolve()
        fe_path = (target / frontend_dir).resolve()
        try:
            fe_path.relative_to(target_resolved)
        except ValueError:
            raise GitUrlError(
                f"frontend_dir '{frontend_dir}' escapes the cloned repo root"
            )

        if not fe_path.is_dir():
            return {
                "frontend_config": {
                    "detected": False,
                    "build_tool": None,
                    "build_command": None,
                    "output_dir": None,
                    "scripts": [],
                    "has_vite_config": False,
                },
                "env_files": {},
                "frontend_dir_exists": False,
            }

        return {
            "frontend_config": _detect_frontend_config(fe_path),
            "env_files": _read_env_files(fe_path, max_size=max_env_file_size),
            "frontend_dir_exists": True,
        }
    finally:
        _safe_rmtree(tmp_root)


# ---------- diagnose_repo ----------
#
# TODO(user): This is one of the places where your domain knowledge matters
# most. The REPO_CHECKS below are a starting set — please extend them with
# the structural fingerprints YOUR projects actually rely on. Things to
# consider:
#   - Is there a convention in your team (e.g. always `uv.lock`, always
#     `Makefile`, always a specific env file layout)?
#   - Should "missing .venv" be a red flag or just a hint? Nuitka pipelines
#     here expect .venv — arguably it should be status="missing" not "warn".
#   - Do you want a check that detects monorepos (multiple frontend/ dirs)?
#   - Do you want a check for "has a README" to help humans sanity-check the
#     clone worked?
# The label strings are displayed verbatim as <Tag> chips in the frontend;
# keep them in Traditional Chinese and short.

REPO_CHECKS: list[dict] = [
    # {
    #     "key": str — stable identifier (e.g. "venv")
    #     "label_ok": str — 繁中 label when the check finds the target
    #     "label_missing": str — 繁中 label when it doesn't
    #     "status_when_missing": "warn" | "missing" — is it bad, or just a hint?
    #     "paths": list[str] — relative paths to look for. First match wins.
    # },
    {
        "key": "venv",
        "label_ok": "已偵測到 .venv",
        "label_missing": "沒有 .venv（Nuitka 打包前可能需要先建立）",
        "status_when_missing": "warn",
        "paths": [".venv"],
    },
    {
        "key": "pyproject",
        "label_ok": "找到 pyproject.toml",
        "label_missing": "沒有 pyproject.toml",
        "status_when_missing": "warn",
        "paths": ["pyproject.toml"],
    },
    {
        "key": "entry_point",
        "label_ok": "找到 Python 進入點",
        "label_missing": "找不到常見的 Python 進入點",
        "status_when_missing": "warn",
        "paths": ["main.py", "app/main.py", "src/main.py", "run.py"],
    },
    {
        "key": "package_json",
        "label_ok": "找到 package.json",
        "label_missing": "沒有 package.json（若為純後端可忽略）",
        "status_when_missing": "warn",
        "paths": ["package.json", "frontend/package.json"],
    },
    {
        "key": "dockerfile",
        "label_ok": "找到 Dockerfile",
        "label_missing": "沒有 Dockerfile（若不輸出 Docker 可忽略）",
        "status_when_missing": "warn",
        "paths": ["Dockerfile"],
    },
]


def diagnose_repo(path: Path) -> dict:
    """Scan `path` for structural fingerprints and return status chips.

    Returns:
        {"checks": [{"key", "label", "status", "path"}, ...]}

    Status values:
    - "ok":      target found
    - "warn":    target missing but might be fine
    - "missing": target missing and almost certainly a problem
    """
    path = Path(path)
    if not path.exists() or not path.is_dir():
        return {"checks": []}

    results: list[dict] = []
    for check in REPO_CHECKS:
        hit_path: str | None = None
        for candidate in check["paths"]:
            probe = path / candidate
            if probe.exists():
                hit_path = candidate
                break
        if hit_path is not None:
            results.append({
                "key": check["key"],
                "label": check["label_ok"],
                "status": "ok",
                "path": hit_path,
            })
        else:
            results.append({
                "key": check["key"],
                "label": check["label_missing"],
                "status": check["status_when_missing"],
                "path": None,
            })

    return {"checks": results}
