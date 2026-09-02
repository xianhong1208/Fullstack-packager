"""Project layout analysis for auto-suggesting packaging configuration.

This module is consumed by the ``/api/analyze-project`` endpoint. It inspects a
Python project directory and classifies its top-level subdirectories into
"source" (Python code), "data" (asset/resource files) or "skip" buckets, and
uses the entry point's imports to figure out which local modules the main
program actually depends on.

The goal is to let the UI auto-recommend which directories to bundle (via the
``extra_dirs`` / ``data_dirs`` packaging options), lowering the amount of
knowledge the user needs to configure a build correctly.

The single public entry point is :func:`analyze_project_layout`. It is a pure,
best-effort function: it NEVER raises. On any unexpected error it degrades
gracefully and returns whatever partial result it managed to build.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Directories that should never be considered for packaging. These are matched
# against the exact directory name (see also the prefix/suffix rules below).
_EXCLUDE_EXACT = {
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
}

# Hidden dirs (starting with ".") are excluded by default, EXCEPT these which
# often carry real config/data worth bundling.
_HIDDEN_KEEP_AS_DATA = {".streamlit"}

# File extensions that count as "data" (assets / resources).
_DATA_EXTENSIONS = {
    ".json",
    ".html",
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".txt",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".pem",
    ".crt",
    ".key",
    ".sql",
    ".csv",
    ".xml",
    ".ttf",
    ".woff",
    ".woff2",
    ".ico",
    ".pdf",
}

# Recursion / scan caps to keep the analysis fast on large projects.
_MAX_DEPTH = 3
_MAX_FILES_SCANNED = 2000


def _is_excluded(name: str) -> bool:
    """Return True if a top-level subdirectory name should be excluded entirely."""
    if name in _EXCLUDE_EXACT:
        return True
    if name.startswith(".venv-"):
        return True
    if name.endswith(".egg-info"):
        return True
    if name.startswith("."):
        # Hidden dirs are excluded unless explicitly kept.
        return name not in _HIDDEN_KEEP_AS_DATA
    return False


def _scan_directory(dir_path: Path) -> tuple[int, int, bool]:
    """Walk a directory (capped) and count .py vs data files.

    Returns ``(py_files, data_files, has_root_init)`` where ``has_root_init``
    indicates whether an ``__init__.py`` sits at the directory's own root.
    """
    py_files = 0
    data_files = 0
    has_root_init = False
    scanned = 0

    try:
        base_depth = len(dir_path.parts)
        for current, dirnames, filenames in _safe_walk(dir_path):
            current_path = Path(current)
            depth = len(current_path.parts) - base_depth
            if depth >= _MAX_DEPTH:
                # Stop descending further.
                dirnames[:] = []
            # Prune noise dirs while walking.
            dirnames[:] = [
                d
                for d in dirnames
                if d != "__pycache__" and not d.endswith(".egg-info")
            ]

            for fname in filenames:
                scanned += 1
                if scanned > _MAX_FILES_SCANNED:
                    return py_files, data_files, has_root_init
                suffix = Path(fname).suffix.lower()
                if suffix == ".py":
                    py_files += 1
                    if fname == "__init__.py" and current_path == dir_path:
                        has_root_init = True
                elif suffix in _DATA_EXTENSIONS:
                    data_files += 1
    except Exception:
        # Best effort: return whatever we counted so far.
        pass

    return py_files, data_files, has_root_init


def _safe_walk(dir_path: Path):
    """os.walk-like generator that tolerates unreadable directories."""
    import os

    try:
        yield from os.walk(str(dir_path), onerror=lambda e: None)
    except Exception:
        return


def _parse_entry_imports(entry_file: Path) -> list[str]:
    """Return the top-level module names imported by the entry point.

    Only the first dotted segment of each import is returned (e.g. ``from
    pkg.sub import x`` -> ``pkg``). Returns ``[]`` if the file is missing or
    cannot be parsed.
    """
    imports: list[str] = []
    try:
        if not entry_file.is_file():
            return []
        source = entry_file.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
        seen: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top and top not in seen:
                        seen.add(top)
                        imports.append(top)
            elif isinstance(node, ast.ImportFrom):
                # Skip relative imports (node.level > 0) with no module name.
                if node.module and node.level == 0:
                    top = node.module.split(".")[0]
                    if top and top not in seen:
                        seen.add(top)
                        imports.append(top)
    except Exception:
        return imports
    return imports


def analyze_project_layout(project_path, entry_point: str = "main.py") -> dict:
    """Classify a project's top-level subdirectories and suggest what to bundle.

    Returns:
    {
      "directories": [
        {"name": str, "role": "source"|"data"|"skip", "reason": str,
         "imported_by_entry": bool, "py_files": int, "data_files": int}
      ],
      "entry_imports": [str],          # top-level modules imported by the entry point
      "suggested_extra_dirs": [str],   # dir names to prefill into extra_dirs
      "suggested_data_dirs": [str],    # dir names to prefill into data_dirs
    }
    """
    result: dict = {
        "directories": [],
        "entry_imports": [],
        "suggested_extra_dirs": [],
        "suggested_data_dirs": [],
    }

    try:
        root = Path(project_path)
    except Exception:
        return result

    try:
        entry_imports = _parse_entry_imports(root / entry_point)
        result["entry_imports"] = entry_imports
        entry_import_set = set(entry_imports)

        try:
            children = sorted(
                (p for p in root.iterdir() if p.is_dir()),
                key=lambda p: p.name.lower(),
            )
        except Exception:
            children = []

        for child in children:
            name = child.name
            if _is_excluded(name):
                continue

            imported_by_entry = name in entry_import_set

            if name in _HIDDEN_KEEP_AS_DATA:
                # Kept hidden dirs are treated as data regardless of contents.
                py_files, data_files, _ = _scan_directory(child)
                result["directories"].append(
                    {
                        "name": name,
                        "role": "data",
                        "reason": f"設定/資料目錄({name}),建議一起打包",
                        "imported_by_entry": imported_by_entry,
                        "py_files": py_files,
                        "data_files": data_files,
                    }
                )
                continue

            py_files, data_files, has_root_init = _scan_directory(child)

            if py_files > 0:
                role = "source"
                if has_root_init:
                    reason = f"含 {py_files} 個 .py(含 __init__.py,Python 套件)"
                else:
                    reason = f"含 {py_files} 個 .py(Python 程式碼)"
            elif data_files > 0:
                role = "data"
                reason = f"以資料檔為主({data_files} 個),建議一起打包"
            else:
                role = "skip"
                reason = "無法判斷,預設略過"

            result["directories"].append(
                {
                    "name": name,
                    "role": role,
                    "reason": reason,
                    "imported_by_entry": imported_by_entry,
                    "py_files": py_files,
                    "data_files": data_files,
                }
            )

        # Build suggestions. Source dirs imported by the entry point come first.
        source_dirs = [d for d in result["directories"] if d["role"] == "source"]
        source_dirs_sorted = [d["name"] for d in source_dirs if d["imported_by_entry"]]
        source_dirs_sorted += [
            d["name"] for d in source_dirs if not d["imported_by_entry"]
        ]
        result["suggested_extra_dirs"] = source_dirs_sorted

        result["suggested_data_dirs"] = [
            d["name"] for d in result["directories"] if d["role"] == "data"
        ]
    except Exception:
        # Never raise: return whatever we have accumulated.
        return result

    return result


def classify_from_paths(file_paths, entry_point: str = "main.py") -> dict:
    """Same classification as analyze_project_layout, but from a flat list of
    repo-relative file paths (e.g. the output of ``git ls-tree -r``) instead of
    a checked-out working tree.

    Used for git mode, where the scan uses a treeless clone and file *contents*
    aren't available — so there's no import analysis: ``imported_by_entry`` is
    always False and ``entry_imports`` is empty. Never raises.
    """
    from collections import defaultdict

    result: dict = {
        "directories": [],
        "entry_imports": [],
        "suggested_extra_dirs": [],
        "suggested_data_dirs": [],
    }
    try:
        stats: dict[str, dict] = defaultdict(lambda: {"py": 0, "data": 0, "init": False})
        for path in file_paths:
            parts = str(path).split("/")
            if len(parts) < 2:
                continue  # a top-level file, not inside a subdirectory
            top = parts[0]
            if _is_excluded(top):
                continue
            fname = parts[-1]
            suffix = ("." + fname.rsplit(".", 1)[1].lower()) if "." in fname else ""
            if suffix == ".py":
                stats[top]["py"] += 1
                if fname == "__init__.py" and len(parts) == 2:
                    stats[top]["init"] = True
            elif suffix in _DATA_EXTENSIONS:
                stats[top]["data"] += 1

        for name in sorted(stats):
            s = stats[name]
            if s["py"] > 0:
                role = "source"
                reason = (
                    f"含 {s['py']} 個 .py(含 __init__.py,Python 套件)"
                    if s["init"]
                    else f"含 {s['py']} 個 .py(Python 程式碼)"
                )
            elif s["data"] > 0:
                role = "data"
                reason = f"以資料檔為主({s['data']} 個),建議一起打包"
            else:
                role = "skip"
                reason = "無法判斷,預設略過"
            result["directories"].append(
                {
                    "name": name,
                    "role": role,
                    "reason": reason,
                    "imported_by_entry": False,
                    "py_files": s["py"],
                    "data_files": s["data"],
                }
            )

        result["suggested_extra_dirs"] = [
            d["name"] for d in result["directories"] if d["role"] == "source"
        ]
        result["suggested_data_dirs"] = [
            d["name"] for d in result["directories"] if d["role"] == "data"
        ]
    except Exception:
        return result

    return result
