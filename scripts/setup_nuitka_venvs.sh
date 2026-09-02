#!/bin/bash
# Setup per-Python-version Nuitka venvs for build_center
#
# 為什麼需要這個:Nuitka 編譯出來的 binary 跟它執行所用的 Python 版本綁定。
# 如果用 3.13 編譯 3.12 專案,runtime 載入 native extension(例如 pydantic_core._pydantic_core.so)
# 就會 ModuleNotFoundError。所以我們需要每個支援的 Python 版本各自一個 venv。
#
# 用法:
#   cd /media/disk0/Tony/devops/build_center
#   bash scripts/setup_nuitka_venvs.sh
#
# 安裝後 build_center 啟動會自動偵測這些 venvs,使用者在 frontend
# 選 Python 3.12 / 3.13 時會掛到對應的 venv。

set -e

cd "$(dirname "$0")/.."
BUILD_CENTER_ROOT="$(pwd)"

# 支援的 Python 版本(frontend 的版本選單由 /api/system-info 自動偵測這些 venv)
SUPPORTED_VERSIONS=("3.12" "3.13" "3.14")

# Nuitka 版本 — 鎖死跟 build_center 主 venv 同版,避免行為差異
# 4.1.2 修掉 2.8.x 編譯 async generator 的 bug:被編譯的 async generator
# (例如 FastAPI SSE 的 event_generator)被未編譯的 asyncio/Starlette 驅動時,
# asend 物件狀態錯亂 → 執行期丟 "cannot reuse already awaited __anext__()/asend()"。
# Nuitka 4.0 的 "uncompiled generator integration" 修正解掉這類問題(上游 issue #3608)。
NUITKA_VERSION="nuitka==4.1.2"
# 目標版本字串(== 後面那段),用來跟既有 venv 裡裝的版本比對
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

    # venv 的 bin/python 是指向 uv 管理的直譯器的 symlink,不是實體檔案。
    # 只要「建立這個 venv 的使用者」跟「現在跑服務的使用者」不同(例如當初用
    # server3 建、現在以 root 執行),或是機器換過、uv 的 python 被清掉,
    # 這條連結就會斷掉 —— 目錄還在、看起來一切正常。
    #
    # find_nuitka_python() 用 exists()(會跟隨 symlink)判斷,會正確地拒絕它,
    # 於是所有編譯默默退回服務自己的直譯器,使用者選 3.12/3.14 卻拿到 3.13 編的
    # 執行檔,直到客戶端執行才炸 ModuleNotFoundError。實際發生過:三個 venv 全斷,
    # 版本選單只剩一個版本,某專案連續失敗 55 次。
    #
    # 所以這裡不能只看目錄在不在 —— 要確認直譯器真的跑得動,跑不動就整個重建。
    if [ -d "$venv_dir" ] && ! "$venv_dir/bin/python" -c "" 2>/dev/null; then
        echo "🔧 $venv_dir 的直譯器已失效(symlink 斷鏈)— 重建"
        echo "     原指向: $(readlink "$venv_dir/bin/python" 2>/dev/null || echo '?')"
        rm -rf "$venv_dir"
    fi

    if [ -d "$venv_dir" ]; then
        # 已存在 — 確認 Nuitka 裝好「而且是目標版本」。只檢查 import 會漏掉
        # 「裝著舊版」的情況(例如 2.8.10 升 4.1.2 時 import 照樣成功),
        # 導致該升級的 venv 被誤判成已就緒而 skip。
        if "$venv_dir/bin/python" -c "import nuitka" 2>/dev/null; then
            cur_ver=$("$venv_dir/bin/python" -m nuitka --version 2>/dev/null | head -1)
            if [ "$cur_ver" = "$NUITKA_TARGET_VER" ]; then
                echo "✅ $venv_dir already has Nuitka $cur_ver — skipping"
                echo
                continue
            else
                echo "⬆️  $venv_dir has Nuitka ${cur_ver:-?} → 升級到 $NUITKA_TARGET_VER"
            fi
        else
            echo "⚠️  $venv_dir exists but Nuitka missing — installing"
        fi
    else
        # 建新 venv
        echo "📦 Creating venv with Python $version..."
        uv venv "$venv_dir" --python "$version"
    fi

    # 裝 Nuitka(用 uv pip 直接寫進 venv)
    echo "🔧 Installing Nuitka into $venv_dir..."
    uv pip install --python "$venv_dir/bin/python" "$NUITKA_VERSION"

    # 驗證 — Nuitka 4.x 沒有 nuitka.__version__,只能靠 import 成功 + CLI 確認版本
    if "$venv_dir/bin/python" -c "import nuitka" 2>/dev/null; then
        # 取 CLI 版本(Nuitka 任何版本都支援 --version)
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
echo "Next:重啟 build_center 後,frontend 選 Python 3.12 / 3.13 會自動掛對應 venv。"
