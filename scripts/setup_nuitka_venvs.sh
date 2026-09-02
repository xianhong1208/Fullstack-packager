#!/bin/bash
# Setup per-Python-version Nuitka venvs for build_center
#
# Why this exists: a Nuitka-compiled binary is bound to the Python version used
# to compile it. Compiling a 3.12 project with 3.13 makes loading a native
# extension at runtime (e.g. pydantic_core._pydantic_core.so) fail with
# ModuleNotFoundError. So we need a separate venv for each supported Python
# version.
#
# Usage:
#   cd /media/disk0/Tony/devops/build_center
#   bash scripts/setup_nuitka_venvs.sh
#
# After install, build_center auto-detects these venvs on startup, and when the
# user picks Python 3.12 / 3.13 in the frontend it binds to the matching venv.

set -e

cd "$(dirname "$0")/.."
BUILD_CENTER_ROOT="$(pwd)"

# Supported Python versions (the frontend's version menu auto-detects these
# venvs via /api/system-info)
SUPPORTED_VERSIONS=("3.12" "3.13" "3.14")

# Nuitka version -- pinned to the same version as build_center's main venv to
# avoid behavioral differences.
# 4.1.2 fixes a 2.8.x bug compiling async generators: when a compiled async
# generator (e.g. FastAPI SSE's event_generator) is driven by an uncompiled
# asyncio/Starlette, the asend object's state gets confused and throws
# "cannot reuse already awaited __anext__()/asend()" at runtime. Nuitka 4.0's
# "uncompiled generator integration" fix resolves this class of issue (upstream
# issue #3608).
NUITKA_VERSION="nuitka==4.1.2"
# Target version string (the part after ==), used to compare against the
# version installed in an existing venv
NUITKA_TARGET_VER="${NUITKA_VERSION##*==}"   # "nuitka==4.1.2" -> "4.1.2"

if ! command -v uv >/dev/null 2>&1; then
    echo "❌ uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo "🎯 Setting up Nuitka venvs in: $BUILD_CENTER_ROOT"
echo

for version in "${SUPPORTED_VERSIONS[@]}"; do
    version_compact="${version//./}"          # "3.12" -> "312"
    venv_dir=".venv-py${version_compact}"

    echo "============================================================"
    echo "  Python $version  →  $venv_dir/"
    echo "============================================================"

    # A venv's bin/python is a symlink to the uv-managed interpreter, not a real
    # file. As soon as the user who created the venv differs from the user now
    # running the service (e.g. originally built as server3, now run as root),
    # or the machine changed and uv's python was removed, this link breaks --
    # while the directory is still there and everything looks fine.
    #
    # find_nuitka_python() checks with exists() (which follows symlinks), so it
    # correctly rejects it, and every compile silently falls back to the
    # service's own interpreter: the user picks 3.12/3.14 but gets an executable
    # built with 3.13, which only blows up with ModuleNotFoundError when the
    # client runs it. This actually happened: all three venvs broke, the version
    # menu was down to a single version, and one project failed 55 times in a
    # row.
    #
    # So we can't just check whether the directory exists -- we must confirm the
    # interpreter actually runs, and rebuild the whole thing if it doesn't.
    if [ -d "$venv_dir" ] && ! "$venv_dir/bin/python" -c "" 2>/dev/null; then
        echo "🔧 $venv_dir interpreter is broken (dangling symlink) — rebuilding"
        echo "     was pointing at: $(readlink "$venv_dir/bin/python" 2>/dev/null || echo '?')"
        rm -rf "$venv_dir"
    fi

    if [ -d "$venv_dir" ]; then
        # Already exists -- confirm Nuitka is installed AND is the target
        # version. Checking only import would miss the "old version installed"
        # case (e.g. upgrading 2.8.10 to 4.1.2, where import still succeeds),
        # causing a venv that needs upgrading to be misjudged as ready and
        # skipped.
        if "$venv_dir/bin/python" -c "import nuitka" 2>/dev/null; then
            cur_ver=$("$venv_dir/bin/python" -m nuitka --version 2>/dev/null | head -1)
            if [ "$cur_ver" = "$NUITKA_TARGET_VER" ]; then
                echo "✅ $venv_dir already has Nuitka $cur_ver — skipping"
                echo
                continue
            else
                echo "⬆️  $venv_dir has Nuitka ${cur_ver:-?} → upgrading to $NUITKA_TARGET_VER"
            fi
        else
            echo "⚠️  $venv_dir exists but Nuitka missing — installing"
        fi
    else
        # Create a new venv
        echo "📦 Creating venv with Python $version..."
        uv venv "$venv_dir" --python "$version"
    fi

    # Install Nuitka (write it straight into the venv with uv pip)
    echo "🔧 Installing Nuitka into $venv_dir..."
    uv pip install --python "$venv_dir/bin/python" "$NUITKA_VERSION"

    # Verify -- Nuitka 4.x has no nuitka.__version__, so we can only rely on a
    # successful import plus the CLI to confirm the version.
    if "$venv_dir/bin/python" -c "import nuitka" 2>/dev/null; then
        # Get the CLI version (every Nuitka version supports --version)
        installed_ver=$("$venv_dir/bin/python" -m nuitka --version 2>/dev/null | head -1 || echo "?")
        echo "✅ $venv_dir ready (Nuitka $installed_ver)"
    else
        echo "❌ Failed to import Nuitka in $venv_dir"
        exit 1
    fi
    echo
done

echo "============================================================"
echo "🎉 All Nuitka venvs ready:"
for version in "${SUPPORTED_VERSIONS[@]}"; do
    version_compact="${version//./}"
    venv_dir=".venv-py${version_compact}"
    py_path="$BUILD_CENTER_ROOT/$venv_dir/bin/python${version}"
    nuitka_ver=$("$py_path" -m nuitka --version 2>/dev/null | head -1 || echo "??")
    echo "  Python $version  →  $py_path  (Nuitka $nuitka_ver)"
done
echo
echo "Next: restart Build Center; the frontend Python 3.12 / 3.13 selector will bind the matching venv."
