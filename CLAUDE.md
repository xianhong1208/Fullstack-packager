# CLAUDE.md

Build Center — 自架的打包平台。使用者透過 Web UI 提交專案，伺服器用 Nuitka 編譯 Python、
建置前端、包 Docker image，再讓使用者下載產出。

使用手冊在 `README.md`，5 分鐘上手在 `QUICKSTART.md`。這份文件記錄的是**改動程式碼時
需要知道、但從程式碼讀不出來的事**。

## 常用指令

```bash
./start.sh                              # 建置前端 + uv sync + 啟動服務
SKIP_FRONTEND=1 ./start.sh              # 跳過前端重建（dist 已存在時）
uv run --group dev pytest tests/ -q     # 跑單元測試
uv run alembic upgrade head             # 資料庫遷移
./scripts/setup_nuitka_venvs.sh         # 建立各 Python 版本的 Nuitka 專用 venv
```

## 架構

```
main.py → app/main.py (FastAPI + 靜態 SPA + 限流中介層)
  api/routes/       auth / tasks / users / monitoring
  api/websocket.py  即時日誌推送
  services/
    task_manager      記憶體任務狀態 + DB 持久化 + WebSocket 廣播
    build_dispatcher  依 (project_type × pack_mode × docker) 分派到 worker
      frontend_worker   npm/yarn/pnpm/bun 建置
      nuitka_worker     Nuitka 編譯（最大也最複雜的一支）
      docker_worker     生成 Dockerfile → build → save
    git_service       Git URL 模式：驗證 / clone / 掃描 / 預覽
    workspace_cleanup 背景清理工作區與過期 image
    auth / permission / audit   JWT + RBAC + 稽核
```

`build_dispatcher` 只負責路由，不做實際工作。「Full Stack + Docker」會串成
`frontend_worker → nuitka_worker → docker_worker`。

## 必須知道的陷阱

### Nuitka venv 的挑選

編譯出來的執行檔**與跑 Nuitka 的那個直譯器 ABI 綁死**，版本不符會在執行期
`ModuleNotFoundError`。`find_nuitka_python()` 優先用 `.venv-py312/313/314`
（由 `scripts/setup_nuitka_venvs.sh` 建立），找不到才退回服務自己的直譯器並回報
`matched=False`。

- 用 `exists()`（會跟隨符號連結）判斷 —— **從別台機器複製過來的 venv 會有斷掉的
  python symlink，必須被拒絕**，不能改成 `is_symlink()`
- `find_target_python()` 的行為**刻意相反**：它接受斷掉的 symlink，因為它只需要路徑
  來識別專案的直譯器，不需要執行它。重構時別把這兩個「統一」掉（測試 TC-NUI-073 / 082 守著）

### 專案 Python 版本偵測

`detect_project_python_version()` **只信 site-packages 裡 `.so` 檔上的 `cpython-XY`
ABI 標籤**，不信 `pyvenv.cfg` / `requires-python` / `.python-version`。原因：venv 可以
用不同 Python 重建而那些宣告檔不會更新（實際遇過宣告 3.13、`.so` 全是 314 的 venv）。

採多數決：單一標籤或占比 ≥80% 就採用，接近五五分才回報「這個 venv 壞了」。

### `top_level.txt` 正規化

有些 wheel 在 `top_level.txt` 裡記的是**檔案路徑**（`sentencepiece/__init__`、
`nvidia/cusparselt`）而不是模組名。只要有一個 `/` 混進 `--nofollow-import-to`，
Nuitka 會在**開始編譯之前**就 exit 1。`_normalize_top_level_entry()` 負責轉換，
不合格的一律回 `None` 丟掉。

### 單一行程假設

`TaskManager` 的任務狀態、WebSocket 連線集合、build semaphore **全部在行程記憶體裡**。
因此**不能開多個 uvicorn worker** —— 第二個 worker 看不到第一個的任務。要橫向擴充必須
先把狀態搬到 Redis。目前 `main.py` 沒有帶 `workers=` 參數，維持這個假設。

### 資料庫 schema：alembic 是唯一來源

`init_db()` 的 `create_all()` 只是新安裝的便利措施 —— 它只 CREATE 缺少的**資料表**，
**永遠不 ALTER 既有資料表**。

**加欄位時**：只改 model 會讓新資料庫正常、既有資料庫靜默缺欄位。一定要補 migration
(`uv run alembic revision -m "..."`)。`history.result` 曾經違反這條規則、靠啟動時
硬寫的 ALTER 補救,現已由 migration `a1c4e9f27b30` 收編 —— 別再開這個先例。

### 密鑰

`JWT_SECRET_KEY` / `GITLAB_TOKEN` / `DB_PASSWORD` 都在 `.env`（已在 `.gitignore`）。
`.env` 必須是 `600`。GitLab token 的三條紅線寫在 `git_service.py` 開頭的
SECURITY INVARIANTS —— 不進資料庫、不由 API 回傳、寫 log 前一律過
`mask_token_in_log()`。改那支檔案前先讀那段。

### 角色繼承走外鍵,不要改回走 relationship

`resolve_role_permissions()` 沿 `parent_role_id` 一跳一跳自己發查詢。看起來繞遠路,
但它取代的遞迴版本(走 `role.parent`)要求**呼叫端事先 eager load 完整族譜** —— 而兩個
呼叫端載入深度不一致(一個三層、一個兩層)。只要角色鏈比載入深度多一層,碰到未載入的
relationship 就會在 async session 裡觸發 lazy load 並丟 `MissingGreenlet`:**那個角色底下
每一個使用者的每一個請求都 500**,而 traceback 完全看不出跟角色階層有關。角色 API 沒有
任何深度上限,管理員從 UI 就能做出來。

`parent_role_id` 是欄位,一定會載入。沒有 parent 的角色(預設安裝的全部)不會多發任何查詢。

**這類問題單元測試證明不了** —— 假造的角色物件沒有 lazy load 行為。驗證方式是開交易、
建六層角色、跑完 rollback(當初就是這樣抓到四層會炸的)。

### 前端只有一個換 token 的入口

`api/client.ts` 的 `refreshTokens()` 持有單一 in-flight promise,401 攔截器與 `AuthContext`
的到期計時器都走它。後端 `/auth/refresh` **輪替並立即註銷舊 token**,所以兩個各自帶旗標的
refresh 實作必然互踩:晚完成的那個拿著剛被註銷的 token 去換,收 401,把使用者登出 ——
而新 token 就躺在 storage 裡。別再在別處寫第二個 refresh。

攔截器換完會發 `TOKENS_REFRESHED_EVENT`,`AuthContext` 靠它重排計時器。

### TaskManager 的鎖不能罩住 I/O

`_lock` 保護的是**整張任務表**,不是單一任務。`update_task` / `append_log` 在鎖裡只做
記憶體寫入,DB 與 WebSocket 廣播一律放到鎖外。`_send_to_connections` 是逐一 await 每條
連線,一個 receive buffer 塞住的客戶端(背景分頁、慢速網路)就會卡住整個鎖,全站所有 build
的日誌串流同時停擺 —— 而且 log 裡完全看不出跟 WebSocket 有關。

`_schedule_eviction` 留在鎖內是刻意的:它只註冊計時器,搬出去反而讓兩次終態寫入競出兩個
eviction task。

## 信任模型

**`task:create` 權限 ≈ 這台機器的指令執行能力。** Git 模式會 clone 任意 repo 然後跑
`npm install`（執行對方的 package.json scripts），smoke test 會直接執行編譯出來的執行檔。

這對 build server 是本質性的。結論是：真正的安全邊界是**帳號核准**（新註冊帳號
`is_active=False`，需管理員啟用），而不是打包流程裡的各種檢查。

`docker_worker.py` 的 `ALLOWED_COMMAND_PREFIXES` 只比對指令開頭，`echo x && ...` 就繞過了。
它擋的是手滑，不是攻擊者 —— 別把它當安全機制來加強。

## 請求來源位址：三個不變式

「這個請求從哪來」只有一個答案來源:`app/middleware/client_ip.py` 的
`resolve_client_ip()`。限流、稽核日誌、登入歷史全部走它。改動時務必保住
(測試 TC-CIP-004 / TC-CIP-010 / TC-RL-031 守著):

1. **`X-Forwarded-For` 預設不可信。** 只有實際連線位址在 `TRUSTED_PROXY_IPS` 裡才採信,
   且採信時取**最右邊**的值 —— nginx 是把真實位址*附加*在用戶端送來的值後面,最左邊
   永遠是攻擊者可控的
2. **`main.py` 的 `proxy_headers=False` 不可以改回 True。** uvicorn 內建的
   `ProxyHeadersMiddleware` 預設開啟、信任 `127.0.0.1`,會在應用程式碼跑之前就用 XFF
   改寫 `scope["client"]`。開著的話 `resolve_client_ip` 收到的是**已經被污染**的
   `request.client`,而且所有單元測試依然會通過 —— 因為測試自己組 scope,跳過了那一層。
   這個坑實際發生過:限流看起來修好了、單元測試全綠,端到端仍可繞過
3. **`path_limits` 優先於 `excluded_prefixes`。** `/api/tasks` 整個前綴被排除讓 Dashboard
   自由輪詢,但 `"POST /api/tasks"` 這個方法限定的 key 必須仍然生效 —— 順序反了會靜默
   解除系統最貴端點的保護

教訓:**涉及「請求中繼資料從哪來」的修正,單元測試證明不了。** 一定要實際起服務、
用真實 HTTP 請求驗證。

### 建置設定裡的 env 內容 = 別人的正式密鑰

使用者會把**真的正式環境 `.env`** 貼進表單,所以 `history.config` 的
`frontend_env_content` / `docker_env_vars` 裡躺著其他系統的 JWT 簽章金鑰、資料庫密碼、
第三方 API key,而且永久保存(掃描時是 365/670 筆、5 位使用者、橫跨半年)。

**這些值一律原樣回傳,不遮罩 —— 這是刻意的決定,別再加回去**(測試 TC-CFV-001/002 守著)。

曾經做過「非擁有者一律遮成 `KEY=<hidden>`」,結論是代價遠大於收益:

- **它砸掉了平台的主要用途。** 在共用打包平台上打開別人的紀錄,就是為了看「這個能跑的
  build 是怎麼設定的」。只給 key 名稱等於什麼都沒說 —— 對方本來就猜得到有哪些變數
- **它幾乎沒擋到什麼。** 沒有任何角色「看得到別人的紀錄卻沒有 `task:create`」——
  `user` 只有 `history:view_own`,`admin` 是 `*:*`。看得到別人 config 的人,本來就能送一個
  build 去直接讀資料庫。真正的邊界是**帳號核准**,見上方信任模型
- **遮罩本身會產生壞掉的建置。** TaskDetail 的「重建」按鈕拿 API 回應預填表單,遮過的值
  會被當成真的設定送回去 —— 資料庫裡就有一筆 `DATABASE_URL=<hidden>` 是這樣來的

`mask_env_values()` / `redacted()` 已整個移除,不留未使用的版本 —— 放在 schema 裡沒人呼叫的
遮罩函式,讀起來會像是還在生效的防護。

存量資料是明文,尚未清理。

### logging 由 `app/main.py` 的 `_configure_logging()` 統一設定

在此之前**沒有任何地方設定 root logger** —— uvicorn 只管它自己的 `uvicorn.*`。
後果是所有 `logger.info()` 被靜默丟棄,WARNING 以上靠 Python 的 `lastResort` 輸出、
沒有時間戳也沒有模組名。也就是說**寫了 log 等於沒寫**。

用 `force=True` 是因為 uvicorn 可能已經動過 root logger,不加的話既有 handler 會讓
格式設定失效。等級由 `LOG_LEVEL` 控制。

順帶澄清一個容易誤判的事:`nuitka_worker.py` 裡的 3 個 `print()` **都在字串常值裡**
(`INJECT_CODE`、`_STDLIB_PROBE`),那是要注入使用者編譯產物、或給子行程執行的程式碼,
必須維持 print。`migrate_auth_v2.py` 是獨立 CLI 腳本,print 也是對的。
**不要用 grep 數 print 就斷定「混用 logger」。**

### 產出下載走票證,不要改回 XHR

`client.ts` 的 `getOutputDownloadUrl()` 換一張短效票證,再由 `triggerUrlDownload()` 交給瀏覽器
原生下載。**不要**為了「拿到進度」或「統一錯誤處理」改回 `responseType: 'blob'` —— 產出常常
好幾 GB,XHR 會把整包塞進網頁記憶體,失去續傳、失去原生進度,大型 image 會讓分頁當掉,
而且白費後端 `FileResponse` 的 Content-Length 與 Range 支援。

票證放在 URL 裡是刻意的,與 WebSocket「token 絕不進 URL」的原則不衝突:票證只活 120 秒、
只綁一個任務、不能呼叫任何其他 API,兌換時還會重驗帳號狀態與權限。爆炸半徑完全不同。

`triggerUrlDownload` 用隱藏 iframe 而非 `<a>` 點擊:票證過期或產出被 TTL 掃掉時,錯誤會落在
iframe 裡,SPA 不會被導去一頁 JSON。

## 已知待修

- `frontend/src/pages/CreateTask.tsx` 2200+ 行，四個 step 全塞在一個元件
  （**但使用體驗其實不差** —— 有動態步驟、簡易/進階切換、逐步說明與首次導覽。
  這是維護性問題，不是 UX 問題，別因為行數就急著重寫）
- 43 處 `except ...: pass`，多數合理但建議補一行 log
- 服務靠 `start.sh` 手動啟動，機器重開就沒了 —— 應該寫成 systemd unit。
  改用 systemd `EnvironmentFile=` 會讓密鑰進到 `os.environ`;smoke test 那邊已經
  改成白名單（`_SMOKE_ENV_PASSTHROUGH`）所以不會外流，但其他地方若有 `**os.environ`
  要一併檢查

## 撰寫測試

測試在 `tests/unit/services/`，跑法見上方指令。慣例：

- **只用 pytest-mock**（`mocker.patch`），不要 `unittest.mock` 的 decorator 或 context manager
- TC ID（`TC-NUI-001`）同時出現在函式名與 docstring
- 所有測試標 `@pytest.mark.unit`；async 測試另加 `@pytest.mark.asyncio`
- 共用常數與 fixture 放 `conftest.py`
- **碰檔案系統的用 `tmp_path`，不要 mock `Path`** —— 這些函式的職責就是解讀真實目錄結構
  （venv、site-packages、dist-info），mock 掉只會測到 mock

優先測「錯了不會拋例外、而是產生能編譯但在客戶端執行才壞掉的產物」那類函式：
ABI 偵測、Nuitka 參數組裝、lazy-import 套件清單。

## 風格

- 繁體中文回覆；程式碼註解說明**為什麼**，不是做什麼（現有註解的風格值得延續）
- 型別註記齊全，async 一路到底
- 新的設定項加在 `app/config.py` 的 `Settings`，同步更新 `.env.example` 與 README 的環境變數表
