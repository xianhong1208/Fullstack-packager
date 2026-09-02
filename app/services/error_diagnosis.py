"""Build failure diagnosis helpers.

This module is consumed by the build pipeline to turn raw build/packaging logs
(Nuitka compilation, Docker image builds, frontend/npm builds, etc.) into a
small set of actionable, human-friendly failure reasons that can be surfaced
directly in the UI.

The single public entry point, :func:`diagnose_build_failure`, performs a
case-insensitive scan of the log text and returns a list of diagnosis dicts,
each shaped as ``{"problem": str, "suggestion": str, "evidence": str}``. The
list is ordered most-specific first so the UI can show the most relevant
explanation at the top. All user-facing text is in Traditional Chinese.

The function is intentionally defensive: it never raises, treats ``None`` as an
empty string, and de-duplicates so the same problem is not reported twice.
"""

from __future__ import annotations

import logging
import re

__all__ = ["diagnose_build_failure"]

# Maximum length of an evidence snippet before it gets trimmed with an ellipsis.
_MAX_EVIDENCE_LEN = 300

logger = logging.getLogger(__name__)


def _trim(snippet: str) -> str:
    """Normalize and length-limit a matched log snippet for display."""
    if snippet is None:
        return ""
    snippet = snippet.strip()
    if len(snippet) > _MAX_EVIDENCE_LEN:
        snippet = snippet[:_MAX_EVIDENCE_LEN].rstrip() + " ..."
    return snippet


def _search(pattern: str, text: str) -> "re.Match | None":
    """Run a case-insensitive regex search, swallowing any regex errors."""
    try:
        return re.search(pattern, text, re.IGNORECASE)
    except Exception:
        return None


def _line_containing(text_lower: str, original: str, needle_lower: str) -> str:
    """Return the first original-cased line containing ``needle_lower``."""
    try:
        idx = text_lower.find(needle_lower)
        if idx < 0:
            return ""
        start = original.rfind("\n", 0, idx) + 1
        end = original.find("\n", idx)
        if end < 0:
            end = len(original)
        return original[start:end]
    except Exception:
        return ""


def diagnose_build_failure(log_text: str) -> list[dict]:
    """Return a list of {"problem": str, "suggestion": str, "evidence": str}
    for known failure patterns found in the build log. Empty list if nothing
    recognized. Ordered most-specific first. 'evidence' is the matched log
    snippet (trimmed). All text in Traditional Chinese."""
    if not log_text:
        return []

    try:
        text = str(log_text)
    except Exception:
        return []

    text_lower = text.lower()

    diagnoses: list[dict] = []
    seen_problems: set[str] = set()
    matched_generic_modules: set[str] = set()

    def add(
        problem: str,
        suggestion: str,
        evidence: str,
        action: dict | None = None,
    ) -> None:
        """Record a diagnosis, optionally with a fix the platform can apply.

        `action` is {"label": str, "overrides": {config field: value}} — the
        rule states what should change and the UI turns it into a button, so a
        new rule gains a one-click fix without touching the frontend.
        """
        if not problem or problem in seen_problems:
            return
        seen_problems.add(problem)
        entry = {
            "problem": problem,
            "suggestion": suggestion,
            "evidence": _trim(evidence),
        }
        if action:
            entry["action"] = action
        diagnoses.append(entry)

    # ------------------------------------------------------------------
    # 1. Python ABI mismatch
    #    ModuleNotFoundError: No module named '<pkg>._<pkg>'  OR
    #    a cpython-3XX .so that can't load.
    # ------------------------------------------------------------------
    try:
        abi_match = _search(
            r"ModuleNotFoundError:\s*No module named\s*['\"]([A-Za-z0-9_]+)\._\1['\"]",
            text,
        )
        # A bare `cpython-3XX...\.so` path is NORMAL, healthy output — Nuitka
        # lists the .so files it bundles, and any traceback frame inside a
        # compiled extension prints that path too. It is ONLY evidence of an ABI
        # mismatch when the .so actually FAILS TO LOAD, so require an error
        # indicator on the same line (import/link failure), not the mere path.
        so_match = _search(
            r"[^\n]*cpython-3\d{1,2}[^\n]*\.so[^\n]*"
            r"(?:cannot open shared object|undefined symbol|wrong elf class|"
            r"invalid elf header|no such file|importerror|"
            r"incompatible|symbol not found)[^\n]*",
            text,
        ) or _search(
            r"(?:cannot open shared object|undefined symbol|wrong elf class|"
            r"invalid elf header|importerror)[^\n]*cpython-3\d{1,2}[^\n]*\.so[^\n]*",
            text,
        )
        if abi_match or so_match:
            pkg = abi_match.group(1) if abi_match else ""
            evidence = abi_match.group(0) if abi_match else so_match.group(0)
            add(
                "目標專案的編譯 Python 版本與依賴的 .so ABI 不符",
                "將「Python 版本」改為「自動偵測」,或確認 build_center 已建對應的 "
                ".venv-pyXYZ(執行 scripts/setup_nuitka_venvs.sh)。",
                evidence,
                # The single most common diagnosed failure, and the fix is one
                # field. Making the user re-enter the whole form to change it
                # is how the same mistake gets submitted dozens of times.
                action={
                    "label": "改用自動偵測並重新建置",
                    "overrides": {"python_version": "auto"},
                },
            )
            if abi_match and pkg:
                # The ABI-form module name is not a "generic" missing module.
                matched_generic_modules.add(f"{pkg}._{pkg}")
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 2. python-dotenv find_dotenv stack walk failure
    #    find_dotenv AND (AssertionError OR frame.f_back)
    # ------------------------------------------------------------------
    try:
        if "find_dotenv" in text_lower and (
            "assertionerror" in text_lower or "frame.f_back" in text_lower
        ):
            evidence = _line_containing(text_lower, text, "find_dotenv")
            add(
                "python-dotenv 的 load_dotenv() 在 Nuitka 下爬呼叫堆疊失敗",
                "專案的 load_dotenv() 改成傳明確路徑,例如 "
                'load_dotenv(BASE_DIR / ".env")。',
                evidence,
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 3. Port already in use
    # ------------------------------------------------------------------
    try:
        needles = ["address already in use", "errno 98", "eaddrinuse"]
        found = next((n for n in needles if n in text_lower), None)
        if found:
            evidence = _line_containing(text_lower, text, found)
            add(
                "目標服務要綁的 port 已被佔用",
                "這通常是舊實例還在跑,非產物問題(本系統已自動視為警告);"
                "要完整驗證可先停掉佔用該 port 的服務。",
                evidence,
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 4. Docker COPY missing source
    #    COPY .* not found  OR  failed to compute cache key  OR
    #    "/<dir>": not found
    # ------------------------------------------------------------------
    try:
        copy_match = _search(r"COPY\b.*not found", text)
        cache_match = _search(r"failed to compute cache key", text)
        dir_match = _search(r'"/[^"\n]+"\s*:\s*not found', text)
        if copy_match or cache_match or dir_match:
            m = copy_match or dir_match or cache_match
            add(
                "Dockerfile 要 COPY 的目錄在 dist/ 不存在",
                "檢查「資料目錄(data_dirs)」是否勾了專案裡不存在的目錄"
                "(本系統已會自動跳過不存在的)。",
                m.group(0),
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 5. Generic ModuleNotFoundError (not matching #1)
    # ------------------------------------------------------------------
    try:
        for m in re.finditer(
            r"ModuleNotFoundError:\s*No module named\s*['\"]([^'\"]+)['\"]",
            text,
            re.IGNORECASE,
        ):
            mod = m.group(1)
            # Skip the ABI-form ('<pkg>._<pkg>') already handled by #1.
            if mod in matched_generic_modules:
                continue
            if re.fullmatch(r"([A-Za-z0-9_]+)\._\1", mod):
                continue
            add(
                f"執行時找不到模組 {mod}",
                f"該套件可能有動態/延遲 import,Nuitka 沒偵測到;"
                f"在「強制納入套件」欄位加入 {mod}。",
                m.group(0),
            )
            break
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 6. Nuitka not installed
    # ------------------------------------------------------------------
    try:
        needles = ["nuitka not installed", "no module named nuitka"]
        found = next((n for n in needles if n in text_lower), None)
        if found:
            evidence = _line_containing(text_lower, text, found)
            add(
                "選定的 Python 環境沒有安裝 Nuitka",
                "執行 scripts/setup_nuitka_venvs.sh 建立對應版本的 venv。",
                evidence,
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 7. npm / frontend build fail
    #    npm ERR!  OR  (vite build AND error)  OR  Cannot find module
    # ------------------------------------------------------------------
    try:
        npm_err = "npm err!" in text_lower
        vite_err = "vite build" in text_lower and "error" in text_lower
        cant_find = "cannot find module" in text_lower
        if npm_err or vite_err or cant_find:
            if npm_err:
                needle = "npm err!"
            elif cant_find:
                needle = "cannot find module"
            else:
                needle = "vite build"
            evidence = _line_containing(text_lower, text, needle)
            add(
                "前端建置失敗",
                "確認 frontend 目錄、build 指令與 Node 版本;"
                "查看下方前端 build log 的紅字。",
                evidence,
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 8. Out of memory
    # ------------------------------------------------------------------
    try:
        needles = ["memoryerror", "killed", "cannot allocate memory"]
        found = next((n for n in needles if n in text_lower), None)
        if found:
            evidence = _line_containing(text_lower, text, found)
            add(
                "編譯過程記憶體不足",
                "降低「CPU 並行數(nuitka_jobs)」,或改用 External 打包模式。"
                "並行數越高,同時開啟的 C 編譯器行程越多,記憶體用量幾乎是線性成長。",
                evidence,
                # 2 keeps some parallelism while cutting peak memory hard. Going
                # to 1 as a first move makes an already-slow build far slower for
                # no extra chance of success.
                action={
                    "label": "把並行數降到 2 並重新建置",
                    "overrides": {"nuitka_jobs": 2},
                },
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 9. Disk full
    # ------------------------------------------------------------------
    try:
        needles = ["no space left on device", "errno 28"]
        found = next((n for n in needles if n in text_lower), None)
        if found:
            evidence = _line_containing(text_lower, text, found)
            add(
                "磁碟空間不足",
                "清理輸出目錄或 workspace,確認 dist/ 與 docker_images 所在磁碟有空間。",
                evidence,
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 10. Permission denied (Python environment access)
    #     Permission denied AND (.venv OR python)
    # ------------------------------------------------------------------
    try:
        if "permission denied" in text_lower and (
            ".venv" in text_lower or "python" in text_lower
        ):
            evidence = _line_containing(text_lower, text, "permission denied")
            add(
                "存取 Python 環境時權限不足",
                "確認 venv 由執行服務的同一使用者建立(非 root),"
                "參考 setup_nuitka_venvs.sh。",
                evidence,
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 11. Git clone — authentication rejected
    # ------------------------------------------------------------------
    try:
        needles = [
            "authentication failed",
            "could not read username",
            "invalid username or password",
            "http basic: access denied",
            "403 forbidden",
        ]
        found = next((n for n in needles if n in text_lower), None)
        if found and ("git" in text_lower or "clone" in text_lower):
            add(
                "Git 認證失敗,無法取得這個 repository",
                "If this is a private repo, add a valid access token for its host in the console (Settings -> Git credentials)"
                "(token 會過期);公開 repo 請確認網址沒有打錯。"
                "注意:網址裡不要自己帶帳號密碼,系統會自行注入。",
                _line_containing(text_lower, text, found),
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 12. Git clone — branch/tag does not exist
    # ------------------------------------------------------------------
    try:
        ref_match = _search(
            r"(?:remote branch|pathspec|couldn't find remote ref)[^\n]*not found[^\n]*"
            r"|Remote branch [^\n]+ not found in upstream",
            text,
        )
        if ref_match:
            add(
                "指定的分支或標籤在遠端不存在",
                "回到第一步重新選一次分支/標籤 —— 下拉選單是即時從遠端讀取的。"
                "常見原因是分支已被刪除或改名。",
                ref_match.group(0),
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 13. Entry point missing
    #     Nuitka refuses to start when the main script is not where we say.
    # ------------------------------------------------------------------
    try:
        entry_match = _search(
            r"(?:FATAL:[^\n]*|Error,[^\n]*)?(?:file|path)[^\n]{0,40}"
            r"(?:does not exist|not found|no such file)[^\n]*\.py[^\n]*"
            r"|python:\s*can't open file[^\n]*",
            text,
        )
        if entry_match:
            add(
                "找不到進入點程式檔",
                "檢查第二步的「進入點」欄位。它是相對於專案根目錄的路徑,"
                "例如 main.py 或 src/app.py —— 不要填絕對路徑。",
                entry_match.group(0),
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 14. Dependency install failed (uv / pip) — distinct from a build error
    # ------------------------------------------------------------------
    try:
        dep_match = _search(
            r"(?:No solution found when resolving|error: Failed to (?:download|build|prepare)"
            r"|Could not find a version that satisfies|ERROR: Could not install)[^\n]*",
            text,
        )
        if dep_match:
            add(
                "安裝專案依賴時失敗",
                "這是專案自己的依賴問題,不是打包設定造成的。"
                "先在專案目錄手動跑一次 uv sync 或 pip install -r requirements.txt,"
                "確認能裝起來再重新打包。",
                dep_match.group(0),
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 15. npm install failed — separate from "frontend build failed" so the
    #     suggestion can point at the lock file rather than the build script.
    # ------------------------------------------------------------------
    try:
        npm_match = _search(
            r"(?:npm ERR!|ERR_PNPM_[A-Z_]+|error Couldn't find any versions)[^\n]*", text
        )
        if npm_match and (
            "npm ci" in text_lower
            or "npm install" in text_lower
            or "err_pnpm" in text_lower
            or "yarn install" in text_lower
        ):
            add(
                "前端依賴安裝失敗",
                "多半是 package-lock.json / pnpm-lock.yaml 與 package.json 對不上。"
                "在前端目錄重新產生 lock 檔並 commit,再重新打包。",
                npm_match.group(0),
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 16. Docker daemon unreachable
    # ------------------------------------------------------------------
    try:
        needles = [
            "cannot connect to the docker daemon",
            "is the docker daemon running",
            "permission denied while trying to connect to the docker daemon",
        ]
        found = next((n for n in needles if n in text_lower), None)
        if found:
            add(
                "連不上 Docker daemon",
                "這是伺服器端的問題,不是你的設定。請聯絡管理員確認 docker 服務正在執行"
                "、且執行本服務的使用者有權限使用它。",
                _line_containing(text_lower, text, found),
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 17b. Gateway timeout from a proxy in front of the app
    #      Checked BEFORE the generic timeout rule: "504" and "gateway
    #      time-out" also contain the word "timeout", and the generic advice
    #      (raise a build setting) is wrong for this one.
    # ------------------------------------------------------------------
    try:
        gw_match = _search(
            r"(?:504\s*(?:gateway\s*time-?out)?|gateway\s*time-?out"
            r"|upstream timed out[^\n]*while reading)[^\n]*",
            text,
        )
        if gw_match and (
            "504" in text_lower or "gateway" in text_lower or "upstream timed out" in text_lower
        ):
            add(
                "被反向代理判定逾時而中斷(504)",
                "這不是後端當掉 —— nginx 的 proxy_read_timeout 算的是「後端多久沒送出"
                "任何資料」。像語音辨識這種算很久、期間完全靜默的請求最容易踩到:"
                "nginx 放棄並回 504,你的後端卻還在跑,結果寫回一條沒人在聽的連線,"
                "所以後端日誌看起來是成功的。"
                "\n處理方式:(1) 把 Docker 設定的「後端最久可以不出聲多久」調到最壞情況的"
                "兩倍;(2) 更可靠的是讓後端在等待期間定期送出資料(SSE 每 10 秒送一行"
                "「: keepalive」),這樣計時器會被重置,而且鏈上每一層代理都同時滿足 —— "
                "如果容器外還有一層公司 nginx,只改容器內的設定是沒有用的。",
                gw_match.group(0),
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    # ------------------------------------------------------------------
    # 17. Timed out
    # ------------------------------------------------------------------
    try:
        needles = ["timed out", "timeout expired", "operation timed out"]
        found = next((n for n in needles if n in text_lower), None)
        if found:
            add(
                "步驟執行超時而被中止",
                "大型專案的 clone 或依賴安裝可能超過預設時限。"
                "請告知管理員調整 GIT_CLONE_TIMEOUT / VENV_BOOTSTRAP_TIMEOUT。",
                _line_containing(text_lower, text, found),
            )
    except Exception:
        # Isolated on purpose: a bug in one rule must not take the rest
        # down. Logged so a broken rule shows up as something other than
        # silently vanishing coverage.
        logger.debug("A diagnosis rule raised and was skipped", exc_info=True)

    return diagnoses


# ----------------------------------------------------------------------
# Guaranteed explanation
# ----------------------------------------------------------------------

# Lines worth showing a user who has no idea what went wrong. Ordered by how
# strongly they indicate the actual cause rather than downstream noise.
_ERROR_LINE_MARKERS = (
    "fatal:",
    "error:",
    "error ",
    "exception",
    "traceback",
    "failed",
    "err!",
    "cannot ",
    "no such file",
    "permission denied",
    "not found",
)

# Stage names come from task["stage"]; phrase them the way the UI already does.
_STAGE_HINTS = {
    "queued": "任務還在排隊時就結束了",
    "preflight": "在「預檢」階段失敗 —— 通常是路徑、Python 版本或依賴環境的問題",
    "compile": "在「編譯」階段失敗 —— Nuitka 執行時出錯",
    "bundle": "在「打包」階段失敗 —— 複製依賴或資料目錄時出錯",
    "verify": "在「驗證」階段失敗 —— 產物編出來了,但試跑不通過",
    "done": "在收尾階段失敗",
}


def extract_error_lines(log_text: str, limit: int = 6) -> list[str]:
    """Pull the lines most likely to explain a failure, newest last.

    Scans from the END of the log: the first error is often a symptom of a
    later, more specific one, and the last thing printed before the process
    died is usually the real reason.
    """
    if not log_text:
        return []
    hits: list[str] = []
    for line in reversed(str(log_text).splitlines()):
        stripped = line.strip()
        if not stripped or len(stripped) > 500:
            continue
        low = stripped.lower()
        if any(m in low for m in _ERROR_LINE_MARKERS):
            if stripped not in hits:
                hits.append(stripped)
            if len(hits) >= limit:
                break
    return list(reversed(hits))


def build_failure_report(log_text: str, *, stage: str | None = None) -> dict:
    """Explain a failed build — always, even when no pattern matches.

    Coverage of the pattern rules was 13% of real failures, and the caller only
    persisted a result when something matched. So 87% of the time a user was
    shown a red "failed" and nothing else, which is how one project accumulated
    55 retries in five weeks: there was nothing to act on, so people just ran
    it again.

    This never returns an empty diagnosis. When no rule fires it still names
    the stage that failed and surfaces the error lines from the log, which is
    the information someone would otherwise have to go digging for.
    """
    diagnoses = diagnose_build_failure(log_text)
    error_lines = extract_error_lines(log_text)

    if not diagnoses:
        stage_hint = _STAGE_HINTS.get((stage or "").lower())
        problem = stage_hint or "建置失敗,但系統無法自動判斷原因"
        if error_lines:
            suggestion = (
                "系統沒有比對到已知的失敗模式,以下是日誌中最後出現的錯誤訊息。"
                "如果看不懂,把這幾行連同任務連結一起貼給管理員即可。"
            )
        else:
            suggestion = (
                "日誌中沒有明顯的錯誤字樣。請開啟右側完整日誌從尾端往回看,"
                "或把這個任務的連結貼給管理員協助判讀。"
            )
        diagnoses = [
            {
                "problem": problem,
                "suggestion": suggestion,
                "evidence": _trim(error_lines[-1]) if error_lines else "",
            }
        ]

    return {
        "diagnosis": diagnoses,
        "error_lines": error_lines,
        "failed_stage": stage or None,
    }
