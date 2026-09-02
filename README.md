# Build Center 操作手冊

> 🚀 **第一次使用?先看 [快速上手指南 QUICKSTART.md](./QUICKSTART.md)** —— 5 分鐘上手,涵蓋建立任務、打包模式、完整打包流(預檢/驗證/成果)、nginx 設定與常見排錯。本手冊是完整參考。

## 目錄

- [系統簡介](#系統簡介)
- [環境需求](#環境需求)
- [安裝與設定](#安裝與設定)
- [啟動服務](#啟動服務)
- [首次使用：建立管理員帳號](#首次使用建立管理員帳號)
- [登入系統](#登入系統)
- [建立打包任務](#建立打包任務)
- [監控任務進度](#監控任務進度)
- [查看歷史記錄](#查看歷史記錄)
- [下載打包產出](#下載打包產出)
- [Docker 打包](#docker-打包)
- [使用者管理（管理員）](#使用者管理管理員)
- [角色與權限管理（管理員）](#角色與權限管理管理員)
- [安全性設定](#安全性設定)
- [安全性與信任模型](#安全性與信任模型)
- [開發：執行測試](#開發執行測試)
- [附錄：環境變數一覽](#附錄環境變數一覽)
- [附錄：API 端點一覽](#附錄api-端點一覽)
- [常見問題](#常見問題)

---

## 系統簡介

Build Center 是一個全端打包管理平台，支援：

- **Backend Only** — 使用 Nuitka 將 Python 專案編譯為可執行檔
- **Frontend Only** — 使用 npm / yarn / pnpm / bun 建置前端專案
- **Full Stack** — 先建置前端，再用 Nuitka 編譯後端（前端產出作為 data 一起打包）
- **Docker 輸出** — 將打包結果包成 Docker image 並匯出為 `.tar.gz`

專案來源可以是**伺服器上的本機路徑**，也可以是 **Git URL**（系統自動 clone、建立 venv、打包完清理工作區）。

系統包含使用者認證、角色權限管理 (RBAC)、即時 WebSocket 日誌、任務歷史記錄等功能。

> ⚠️ **先讀 [安全性與信任模型](#安全性與信任模型)**。Build Center 會在伺服器上執行來自目標專案的程式碼，
> 因此「誰拿得到 `task:create` 權限」等同於「誰能在這台機器上執行指令」。

---

## 環境需求

| 項目 | 版本需求 |
|------|---------|
| Python | >= 3.11 |
| Node.js | >= 18 |
| PostgreSQL | >= 14 |
| uv | 最新版 |
| Docker | 選用，打包 Docker image 時需要 |
| Nuitka | >= 2.8.9（由 uv 自動安裝） |

---

## 安裝與設定

### 1. 下載專案

```bash
cd /media/disk0/Tony
# 假設已經有 build_center 目錄
cd build_center
```

### 2. 設定環境變數

複製範例設定檔並修改：

```bash
cp .env.example .env
```

編輯 `.env`：

```ini
# 資料庫
DB_HOST=localhost
DB_PORT=5432
DB_NAME=nuitka_db
DB_USER=postgres
DB_PASSWORD=你的密碼

# 伺服器
HOST=192.168.112.10
PORT=5018
DEBUG=false

# 任務設定
MAX_LOG_LINES=3000
MAX_CONCURRENT_BUILDS=3
```

完整清單見[附錄：環境變數一覽](#附錄環境變數一覽)。設定完成後**務必收緊權限** ——
這個檔案會存放 JWT 簽名密鑰與 GitLab token：

```bash
chmod 600 .env
```

### 3. 建立 PostgreSQL 資料庫

```bash
createdb nuitka_db
```

### 4. 安裝後端依賴

```bash
uv sync
```

### 5. 執行資料庫遷移

```bash
uv run alembic upgrade head
```

> ℹ️ **alembic 是 schema 變更的唯一來源。** 服務啟動時的 `Base.metadata.create_all()`
>（`app/database.py`）只是新安裝的便利措施 —— 它只會 CREATE 缺少的**資料表**，
> 永遠不會 ALTER 既有資料表。
>
> **加新欄位時**：只改 model 會讓全新的資料庫正常、既有的資料庫靜默缺欄位。
> 一定要補一張 migration：`uv run alembic revision -m "..."`。

### 6. 安裝並建置前端

```bash
cd frontend
npm install
npm run build
cd ..
```

---

## 啟動服務

### 方式一：使用啟動腳本（推薦）

```bash
./start.sh
```

腳本會自動：
1. 檢查前端是否已 build（沒有就自動 build）
2. `uv sync` 同步後端依賴
3. 啟動 FastAPI 伺服器

自訂 host / port：

```bash
./start.sh --host 0.0.0.0 --port 8080
```

### 方式二：手動啟動

```bash
HOST=192.168.112.10 PORT=5018 uv run python main.py
```

啟動成功會看到：

```
  ____        _ _     _    ____           _
 | __ ) _   _(_) | __| |  / ___|___ _ __ | |_ ___ _ __
 |  _ \| | | | | |/ _` | | |   / _ \ '_ \| __/ _ \ '__|
 | |_) | |_| | | | (_| | | |__|  __/ | | | ||  __/ |
 |____/ \__,_|_|_|\__,_|  \____\___|_| |_|\__\___|_|

  Build Center Service v1.0.0
  Starting server at http://192.168.112.10:5018
```

---

## 首次使用：建立管理員帳號

1. 用瀏覽器打開 `http://<你的 HOST>:<PORT>`
2. 系統偵測到沒有任何使用者，會自動進入 **註冊模式**
3. 填寫：
   - **Username** — 管理員帳號名稱
   - **Password** — 密碼（至少 4 個字元）
   - **Confirm Password** — 再次輸入密碼
4. 點擊 **Create Account**

> 第一位註冊的使用者會自動被指派為 **admin** 角色、直接啟用，並自動登入。
>
> **之後註冊的使用者不會自動啟用。** 他們會被指派為 **user** 角色但 `is_active = false`，
> 註冊後只會看到「請等待管理員核准」，無法登入。管理員需到 **Users** 頁面把帳號改為啟用。
> 這是刻意的設計 —— 見[安全性與信任模型](#安全性與信任模型)。

---

## 登入系統

1. 進入登入頁面 `http://<HOST>:<PORT>/login`
2. 輸入 **Username** 和 **Password**
3. 可勾選 **Remember Me** 延長登入有效期（7 天）
4. 點擊 **Sign In**

登入後會自動跳轉到 Dashboard。

### 忘記密碼

1. 在登入頁點擊 **Forgot Password**
2. 輸入帳號名稱
3. 如果有設定安全問題，需回答正確
4. 設定新密碼

---

## 建立打包任務

從側邊欄點擊 **Create Task**，進入任務建立表單。

### Step 1：基本資訊

| 欄位 | 說明 |
|------|------|
| Project Name | 專案顯示名稱（例如 `token-server`） |
| Project Type | `Backend Only` / `Frontend Only` / `Full Stack` |
| **Source** | `Local Path`（伺服器上的既有目錄）或 `Git URL`（自動 clone） |
| Project Path | *Local 模式*：專案絕對路徑（例如 `/media/disk0/Tony/token-server`） |
| Git URL / Ref | *Git 模式*：repo 網址與分支/標籤 |
| Output Directory | 輸出資料夾名稱（預設 `dist`） |

> Local 模式的路徑必須位於 `/media/disk0/` 或 `/media/disk1/` 底下，其他路徑一律回 403
> （`app/api/routes/tasks.py` 的 `ALLOWED_PATH_PREFIXES`）。

#### Git URL 來源模式

選 **Git URL** 後，系統會：

1. **驗證網址** — 只接受 http/https；拒絕內嵌帳密、`..` 路徑、localhost，以及指向
   loopback / 私有網段 / link-local（雲端 metadata）的 IP。若要打內網 GitLab，
   把該主機加進 `GIT_ALLOWED_HOSTS`（見環境變數表）。
2. **列出分支與標籤** — 你選一個 ref。
3. **淺層掃描** — 用 `--filter=blob:none --no-checkout` 讀出頂層目錄與 pyproject 的
   `[dependency-groups]`，讓你直接勾選要打包的目錄，不必先完整 clone。
4. **Clone 並建立 venv** — `GIT_AUTO_VENV=true` 時自動在 clone 目錄跑 `uv sync`。
5. **打包完清理工作區** — 見下方「工作區生命週期」。

**Token 處理**：私有 repo 的存取權來自伺服器端的 `GITLAB_TOKEN`。它只在呼叫 git 子行程的
那一刻被注入網址，**不會寫進資料庫、不會由 API 回傳、寫進日誌前一律遮罩成 `***`**。
使用者不需要（也不應該）把帳密放進 Git URL —— 放了會被驗證擋下來。

**工作區生命週期**（Git 模式專用，背景排程每 `WORKSPACE_CLEANUP_INTERVAL_HOURS` 小時掃一次）：

| 任務狀態 | 保留策略 |
|---------|---------|
| 成功（非 Docker） | 立即刪除 output 以外的所有東西（原始碼 / `.venv` / `.git`），`WORKSPACE_SUCCESS_TTL_DAYS` 天後整個刪除 |
| 成功（Docker） | 立即整個刪除 —— image 的 `.tar.gz` 已經另存在 `DOCKER_IMAGES_DIR` |
| 失敗 / 取消 | 保留 `WORKSPACE_FAILED_TTL_DAYS` 天（比較久，方便除錯） |
| 孤兒目錄（無對應歷史記錄） | 保留 `WORKSPACE_ORPHAN_TTL_DAYS` 天 |

> ⚠️ 非 Docker 任務的**下載功能依賴工作區還在**。也就是說
> `WORKSPACE_SUCCESS_TTL_DAYS`（預設 7 天）同時就是「產出還能下載多久」。

### Step 2：Backend 設定（Backend Only / Full Stack）

| 欄位 | 說明 |
|------|------|
| Python Version | 選擇 3.10 / 3.11 / 3.12 / 3.13 |
| Entry Point | 主程式進入點（預設 `main.py`） |
| Output Name | 輸出執行檔名稱（空白則自動取 entry point 名稱） |
| Onefile | 開啟 = 單一執行檔；關閉 = 資料夾模式 |
| Pack Mode | `Full` = Nuitka 打包所有依賴；`External` = 第三方庫放 libs/ |
| Extra Directories | 額外的 Python 原始碼目錄（例如 `src`, `utils`） |
| Data Directories | 需要一起打包的資料目錄（例如 `config`, `static`, `templates`） |
| Nuitka Jobs | 平行編譯 CPU 核心數（0 = 自動） |

> 輸入 Project Path 後，系統會列出子目錄讓你勾選 Extra Directories 和 Data Directories。

### Step 3：Frontend 設定（Frontend Only / Full Stack）

| 欄位 | 說明 |
|------|------|
| Frontend Dir | 前端目錄（預設 `frontend`） |
| Build Tool | `npm` / `yarn` / `pnpm` / `bun`（系統自動偵測） |
| Build Command | 建置指令（預設 `build`，可改為 `build:prod`） |
| Output Dir | 前端建置產出目錄（自動從 vite.config 偵測） |
| Env Filename | 環境變數檔名（預設 `.env`） |
| Env Content | 建置前寫入的環境變數內容 |

> 點擊 **Detect Config** 按鈕可自動偵測 build tool、build command 和 output dir。
> 點擊 **Load Env** 按鈕可讀取現有的 `.env` 檔案內容。

### Step 4：Docker 設定（選用）

勾選 **Enable Docker** 後出現：

| 欄位 | 說明 |
|------|------|
| Image Name | Docker image 名稱（例如 `token-server:latest`） |
| Base Image | 基底映像（預設 `python:3.13-slim`） |
| Expose Port | 容器對外 port |
| Install Node.js | 在 Docker image 中安裝 Node.js |
| Custom Commands | 額外的 Dockerfile RUN 指令（每行一條） |
| API Proxy | nginx 反向代理的後端 API URL（Frontend Only 時用） |

### Step 5：送出

點擊 **Start Build** 開始打包。系統會自動跳轉到任務詳情頁面。

---

## 監控任務進度

### Dashboard

- 首頁顯示所有 **進行中** 的任務
- 每個任務卡片顯示：專案名稱、狀態、進度條
- 每 5 秒自動刷新
- 點擊卡片進入任務詳情

### 任務詳情頁（`/task/:taskId`）

- **左側面板**：狀態徽章、進度條、專案設定摘要、取消 / 重建 / 下載按鈕
- **右側面板**：即時 Build Log（WebSocket 推送，每秒更新）

---

## 查看歷史記錄

從側邊欄點擊 **History**。

### 列表頁功能

| 功能 | 說明 |
|------|------|
| Type 欄位 | 顯示 Backend / Frontend / Full Stack 彩色標籤 |
| Docker 欄位 | 有使用 Docker 會顯示 Docker 圖示 |
| Status 篩選 | 可篩選 Completed / Failed / Cancelled |
| Type 篩選 | 可篩選 Backend / Frontend / Full Stack |
| Docker 篩選 | 可篩選有/無 Docker |
| Duration 排序 | 可依耗時排序 |
| 點擊整行 | 進入詳細頁面 |
| Export CSV | 匯出歷史記錄為 CSV 檔案 |

### 詳細頁面（`/history/:taskId`）

點擊任一筆記錄後進入，顯示完整的 Build Configuration：

- **左側**：狀態、使用者、時間、耗時、輸出路徑、重建 / 下載按鈕
- **右側**：
  - 標籤列（Project Type、Docker、Pack Mode、Single File）
  - Backend 設定區（Python 版本、Entry Point、CPU Jobs 等）
  - Frontend 設定區（Build Tool、Build Command、Env 內容等）
  - Docker 設定區（Image Name、Base Image、Port、Custom Commands 等）

---

## 下載打包產出

### 從歷史記錄下載

1. 進入 **History** 頁面
2. 找到狀態為 **Completed** 的任務
3. 點擊下載按鈕（或進入詳細頁後點擊 **Download Output**）
4. 系統下載 `.tar.gz` 壓縮檔

下載走瀏覽器**原生下載器**（有進度、可續傳、直接寫入磁碟），而不是先把整個檔案讀進網頁記憶體 ——
產出動輒數 GB，後者會失去續傳能力，大型 Docker image 甚至可能讓分頁當掉。

實作上前端會先用已認證的請求換一張**只活 120 秒、只綁定該任務**的下載票證，再把網址交給瀏覽器。
票證兌換時會重新檢查帳號是否仍啟用、權限是否仍在，所以停用帳號無法完成已開始的下載。

> Docker 產出走 `FileResponse`（有 Content-Length，支援 Range 續傳）；
> 非 Docker 產出是邊打包邊串流的 `.tar.gz`，因此是 chunked、無法續傳 —— 這是必然的取捨，
> 因為壓縮檔在下載開始時還不存在。

### 產出內容

| 打包類型 | 產出內容 |
|---------|---------|
| Backend Only | 編譯後的執行檔 + libs/（External 模式）+ data dirs |
| Frontend Only | 建置後的靜態檔案（dist/） |
| Full Stack | 執行檔 + 前端靜態檔 + libs/ + data dirs |
| Docker | `.tar.gz` 格式的 Docker image |

### 載入 Docker Image

如果是 Docker 打包的產出：

```bash
# 載入 image
docker load < token-server_latest.tar.gz

# 執行容器（搭配 .env）
docker run --rm --network host --env-file .env token-server:latest
```

---

## Docker 打包

### 打包流程（以 Full Stack + Docker 為例）

```
Step 1: Frontend Build
  npm install → npm run build → 產出 frontend/dist/

Step 2: Nuitka Compilation
  Nuitka 編譯 Python → 產出 dist/main（執行檔）
  複製 data_dirs → dist/config/, dist/static/ 等

Step 3: Docker Build
  生成 Dockerfile → docker build → docker save → .tar.gz
```

### 注意事項

- Docker 打包時，`.env` 檔案 **不會** 被包進 image（基於安全考量）
- 執行容器時用 `--env-file` 或 `-e` 傳入環境變數
- `data_dirs` 指定的目錄會被 COPY 進 Docker image 中
- Docker image 匯出存放在 `/media/disk1/docker_images/`

---

## 使用者管理（管理員）

需要 **admin** 角色或擁有 `user:view` / `user:manage` 權限。

從側邊欄點擊 **Users**。

### 功能

| 操作 | 說明 |
|------|------|
| 查看使用者列表 | 帳號、Email、狀態、角色、建立日期、最後登入 |
| 搜尋 | 依使用者名稱搜尋 |
| 篩選 | 依狀態（啟用/停用）、角色篩選 |
| 變更角色 | 指派不同角色給使用者 |
| 停用帳號 | 禁止使用者登入（不刪除資料） |
| 重設密碼 | 由管理員設定新的臨時密碼 |
| 登入歷史 | 查看該使用者的登入記錄（IP、裝置、成功/失敗） |

---

## 角色與權限管理（管理員）

需要 `role:manage` 權限。

從側邊欄點擊 **Roles**。

### 預設角色

| 角色 | 說明 | 可刪除 |
|------|------|:------:|
| admin | 系統管理員，擁有所有權限 | 否 |
| user | 一般使用者，可建立任務和查看自己的記錄 | 否 |

### 權限清單

| 權限代碼 | 說明 |
|---------|------|
| `task:create` | 建立打包任務 |
| `task:view_own` | 查看自己的任務 |
| `task:view_all` | 查看所有人的任務 |
| `task:cancel_own` | 取消自己的任務 |
| `task:cancel_all` | 取消所有人的任務 |
| `history:view_own` | 查看自己的歷史記錄 |
| `history:view_all` | 查看所有人的歷史記錄 |
| `history:export` | 匯出歷史記錄 CSV |
| `user:view` | 查看使用者列表 |
| `user:manage` | 管理使用者（變更角色、停用帳號） |
| `user:reset_password` | 重設使用者密碼 |
| `role:view` | 查看角色列表 |
| `role:manage` | 管理角色（新增、編輯、刪除） |

### 自訂角色

1. 點擊 **Create Role**
2. 填寫角色名稱和顯示名稱
3. 勾選需要的權限
4. 可選擇父角色（繼承其權限）
5. 儲存

---

## 安全性設定

使用者可從側邊欄的 **Settings > Security** 進入。

### 安全問題

- 設定安全問題和答案，用於忘記密碼時的身份驗證
- 設定後可透過「忘記密碼」流程自行重設密碼

### 變更密碼

- 需輸入目前密碼才能變更
- 新密碼至少 4 個字元

### 活躍工作階段

- 查看目前所有登入中的工作階段
- 可逐一登出，或一次登出全部其他工作階段

---

## 安全性與信任模型

這一節描述系統實際的安全邊界。部署前請確認你同意這些假設。

### 核心假設：`task:create` ≈ 這台機器的 shell

Build Center 的工作就是**執行來自目標專案的程式碼**：

- Git 模式會 clone 任意 repo，然後跑 `npm install` / `npm run build` —— 這會執行對方
  `package.json` 裡的 scripts
- 打包完成後的 **smoke test 會直接執行編譯出來的執行檔**

這對任何 build server / CI 都是本質性的，不是缺陷。但結論很重要：

> **拿到 `task:create` 權限的使用者，實質上等於拿到這台伺服器的指令執行能力。**

因此真正的安全邊界不是打包流程裡的各種檢查，而是**帳號核准**。系統的設計反映了這點：

- 第一個註冊者成為 admin，**之後所有註冊都是停用狀態**，必須由管理員手動啟用
- 預設 `user` 角色包含 `task:create` —— 所以「核准一個帳號」就是「授予執行權」，請當作這個層級的決定來看待
- `docker_worker.py` 的 custom command 允許清單只比對指令開頭，擋得住手滑、**擋不住刻意繞過**
  （`echo x && ...` 就過了）。基於上述前提，它本來就不是安全邊界

### 密鑰處理

| 密鑰 | 位置 | 規則 |
|------|------|------|
| `JWT_SECRET_KEY` | `.env` | 首次啟動時自動產生並寫入 `.env`。**檔案權限必須是 `600`** |
| `GITLAB_TOKEN` | `.env` | 只在 git 子行程呼叫時注入網址；不進資料庫、不由 API 回傳、寫 log 前遮罩 |
| `DB_PASSWORD` | `.env` | 同上，靠檔案權限保護 |

```bash
chmod 600 .env      # 部署後務必執行；.env 已在 .gitignore 中
```

> `.env` 若曾經是 world-readable，請視同 `JWT_SECRET_KEY` 已洩漏並輪替它
>（刪掉 `.env` 裡那一行，重啟服務會產生新的；代價是所有使用者被登出一次）。

### 網路曝險

- 服務預設綁在 `0.0.0.0:5018`。放在信任網段內，或前面擺一層反向代理

**限流的身分來源**：`X-Forwarded-For` 是用戶端可以任意填寫的 header，若無條件相信它，
任何人都能每次請求換一個假 IP、直接走過登入限流。因此系統的規則是：

- **預設完全不信任 XFF**，一律用實際連線位址(`TRUSTED_PROXY_IPS` 為空時)
- 只有當**實際連線位址就是設定中的反向代理**時，才採信該 header
- 採信時取 **XFF 最右邊**那個值。nginx 的 `$proxy_add_x_forwarded_for` 是把觀察到的位址
  *附加*在用戶端送來的值後面，所以最左邊仍是攻擊者可控的，最右邊才是代理背書的

前面有 nginx 時才需要設定：

```ini
TRUSTED_PROXY_IPS=127.0.0.1,10.0.0.5
```

> ⚠️ **uvicorn 的 proxy headers 已刻意關閉**（`main.py` 的 `UVICORN_KWARGS`）。
> uvicorn 內建一層 `ProxyHeadersMiddleware`，預設開啟且信任 `127.0.0.1`，它會在
> 應用程式碼執行**之前**就用 `X-Forwarded-For` 改寫 `request.client`。兩層各自判斷
> 信任的結果，就是限流拿到一個已經被污染、且無從察覺的位址。關掉它之後
> `request.client` 永遠是真實對端，`TRUSTED_PROXY_IPS` 是唯一的決策點。
>
> 這也代表：**放在 nginx 後面時一定要設定 `TRUSTED_PROXY_IPS`**，否則所有使用者會被
> 算成同一個 IP（nginx 的位址），限流會過嚴。這是刻意選擇的失敗方向 —— 會馬上被發現，
> 而不是靜默失效。

登入記錄與稽核日誌的 IP 也走同一個解析器（`app/middleware/client_ip.py`），
所以攻擊者無法在管理員查看的登入歷史裡寫入任意 IP。

**建立任務的限流**：`POST /api/tasks` 有獨立的每分鐘上限（`BUILD_SUBMIT_PER_MINUTE`，
預設 20）。`/api/tasks` 前綴整體被排除在限流外，讓 Dashboard 可以自由輪詢狀態，
但這個「方法限定」的規則優先於該排除 —— 昂貴的是**送出**建置，不是查詢狀態。
同時執行的編譯數另由 `MAX_CONCURRENT_BUILDS` 限制。

### 路徑存取

Local 模式的檔案系統操作（列目錄、讀 `.env`、分析專案）全部經過
`_validate_allowed_path()`，解析符號連結後必須落在 `ALLOWED_PATH_PREFIXES`
（`/media/disk0/`、`/media/disk1/`）之內。

---

## 開發：執行測試

```bash
uv run --group dev pytest tests/ -q
```

測試位於 `tests/unit/services/`，涵蓋打包流程中的純函式：

| 檔案 | 涵蓋範圍 |
|------|---------|
| `test_git_service.py` | Git URL 驗證（SSRF 防護）、ref 驗證、token 注入與遮罩、dependency-groups 解析 |
| `test_nuitka_worker.py` | `top_level.txt` 模組名正規化、`.venv` ABI 版本偵測、env 檔解析、lazy-import 套件偵測、直譯器挑選 |

慣例：純 pytest-mock（不用 `unittest.mock` 的 decorator）、TC ID 同時出現在函式名與 docstring、
所有測試標記 `@pytest.mark.unit`。碰真實檔案系統的用 `tmp_path`，不要 mock `Path`。

**新增測試時的優先順序**：邏輯錯了會產生「能編譯、但在客戶端執行才壞掉」的函式最值得測 ——
ABI 版本偵測、`--nofollow-import-to` 參數組裝、lazy-import 套件清單都屬於這一類。

---

## 附錄：環境變數一覽

### 資料庫

| 變數名稱 | 預設值 | 說明 |
|---------|-------|------|
| `DB_HOST` | `localhost` | PostgreSQL 主機位址 |
| `DB_PORT` | `5432` | PostgreSQL 連接埠 |
| `DB_NAME` | `nuitka_db` | 資料庫名稱 |
| `DB_USER` | `postgres` | 資料庫使用者 |
| `DB_PASSWORD` | `admin` | 資料庫密碼 |

### 伺服器

| 變數名稱 | 預設值 | 說明 |
|---------|-------|------|
| `HOST` | `192.168.112.10` | 伺服器監聽位址（`start.sh` 預設 `0.0.0.0`） |
| `PORT` | `5018` | 伺服器監聽埠 |
| `DEBUG` | `false` | 開啟 SQLAlchemy SQL echo。**不會**啟用 hot reload |
| `LOG_LEVEL` | `INFO` | 應用程式日誌等級(DEBUG/INFO/WARNING/ERROR) |
| `JWT_SECRET_KEY` | 隨機生成 | JWT 簽名密鑰；未設定時首次啟動會產生並寫入 `.env` |
| `TRUSTED_PROXY_IPS` | 空 | CSV 反向代理位址清單。**空 = 完全不信任 `X-Forwarded-For`**。只有前面確實有代理時才設定 |

### 任務與併發

| 變數名稱 | 預設值 | 說明 |
|---------|-------|------|
| `MAX_LOG_LINES` | `3000` | 每個任務保留的最大日誌行數 |
| `MAX_CONCURRENT_BUILDS` | `3` | 同時執行的編譯數上限（Nuitka 吃滿 CPU，別調太高） |
| `BUILD_SUBMIT_PER_MINUTE` | `20` | `POST /api/tasks` 每分鐘每 IP 的送出上限 |
| `FINISHED_TASK_RETENTION_MINUTES` | `60` | 完成的任務在記憶體中保留多久後淘汰（歷史記錄永久留在資料庫） |

### Git 來源模式

| 變數名稱 | 預設值 | 說明 |
|---------|-------|------|
| `GITLAB_TOKEN` | 無 | 私有 repo 的存取 token。**伺服器端專用密鑰** |
| `GIT_WORKSPACE_DIR` | `/media/disk1/Build_workspace` | clone 的工作區根目錄 |
| `GIT_ALLOWED_HOSTS` | 空 | CSV 主機允許清單。空 = 允許任何公開 http(s) 主機。**列在此的主機可豁免私有 IP 檢查**，內網 GitLab 要靠這個 |
| `GIT_CLONE_TIMEOUT` | `600` | clone 逾時秒數 |
| `GIT_AUTO_VENV` | `true` | clone 後自動跑 `uv sync` 建立 `.venv` |
| `UV_BIN` | `/home/server3/.local/bin/uv` | uv 執行檔絕對路徑，**必須存在** |
| `PRE_BUILD_PATH` | `/home/server3/.local/bin:/usr/local/bin:/usr/bin:/bin` | 前置作業子行程的 PATH，必須包含 `UV_BIN` 所在目錄 |
| `VENV_BOOTSTRAP_TIMEOUT` | `900` | 建立 venv 的逾時秒數 |
| `BOOTSTRAP_UV_CACHE_DIR` | `/media/disk1/.uv-cache` | bootstrap 專用的 uv 快取。**要和 `GIT_WORKSPACE_DIR` 同一個檔案系統**，否則 uv 無法硬連結、只能複製 |

### 工作區清理（Git 模式）

| 變數名稱 | 預設值 | 說明 |
|---------|-------|------|
| `WORKSPACE_CLEANUP_ENABLED` | `true` | 是否啟用背景清理 |
| `WORKSPACE_CLEANUP_INTERVAL_HOURS` | `6` | 清理排程間隔 |
| `WORKSPACE_SUCCESS_TTL_DAYS` | `7` | 成功任務的工作區保留天數。**同時是產出可下載的天數** |
| `WORKSPACE_FAILED_TTL_DAYS` | `30` | 失敗 / 取消任務的保留天數 |
| `WORKSPACE_ORPHAN_TTL_DAYS` | `2` | 無對應歷史記錄的孤兒目錄保留天數 |
| `WORKSPACE_SHRINK_ON_SUCCESS` | `true` | 非 Docker 任務成功後立即刪除 output 以外的內容 |
| `WORKSPACE_DOCKER_DELETE_ON_SUCCESS` | `true` | Docker 任務成功後整個刪除工作區 |

### Docker image 匯出

| 變數名稱 | 預設值 | 說明 |
|---------|-------|------|
| `DOCKER_IMAGES_DIR` | `/media/disk1/docker_images` | 匯出的 `.tar.gz` 存放位置 |
| `DOCKER_EXPORT_COMPRESS_THREADS` | `32` | 壓縮匯出 image 的執行緒數(需安裝 `pigz`,未安裝則自動退回單執行緒 gzip) |
| `DOCKER_IMAGES_CLEANUP_ENABLED` | `true` | 是否定期清理舊 image |
| `DOCKER_IMAGES_TTL_DAYS` | `30` | image 保留天數（依 mtime） |

---

## 附錄：API 端點一覽

「權限」欄列出呼叫該端點所需的權限碼；標示 *登入* 表示只需有效的 access token。

### 認證 `/auth`

| 方法 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| GET | `/auth/check-first-user` | 公開 | 檢查是否還沒有任何使用者 |
| POST | `/auth/register` | 公開 | 註冊；非第一位使用者建立後為停用狀態 |
| POST | `/auth/login` | 公開 | 登入 |
| POST | `/auth/refresh` | 公開（需 refresh token） | 換新的 access token |
| POST | `/auth/forgot-password` | 公開 | 啟動密碼重設流程 |
| POST | `/auth/verify-security-answer` | 公開 | 驗證安全問題答案 |
| POST | `/auth/reset-password` | 公開（需重設 token） | 完成密碼重設 |
| POST | `/auth/logout` | 登入 | 登出目前工作階段 |
| POST | `/auth/logout-all` | 登入 | 登出所有工作階段 |
| GET | `/auth/me` | 登入 | 取得目前使用者資訊與權限 |
| POST | `/auth/change-password` | 登入 | 變更密碼（需舊密碼） |
| PUT | `/auth/security-question` | 登入 | 設定安全問題 |
| GET | `/auth/login-history` | 登入 | 自己的登入記錄 |
| GET | `/auth/active-sessions` | 登入 | 自己的活躍工作階段 |
| DELETE | `/auth/sessions/{id}` | 登入 | 登出指定工作階段 |

### 任務 `/api`

| 方法 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| POST | `/api/tasks` | `task:create` | 建立打包任務 |
| GET | `/api/tasks` | `task:view_own` / `task:view_all` | 列出進行中的任務 |
| GET | `/api/tasks/{id}` | 同上 | 單一任務詳情 |
| GET | `/api/tasks/{id}/logs` | 同上 | 目前的日誌快照 |
| DELETE | `/api/tasks/{id}` | `task:cancel_own` / `task:cancel_all` | 取消任務 |

> 沒有 `*_all` 權限的使用者，回應會自動過濾成只有自己的任務。

### 歷史記錄 `/api`

| 方法 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| GET | `/api/history` | `history:view_own` / `history:view_all` | 列出歷史記錄 |
| GET | `/api/history/{id}` | 同上 | 單筆詳情 |
| POST | `/api/history/{id}/download-ticket` | 同上 | 換取短效下載票證(Web UI 用） |
| GET | `/api/download/{ticket}` | 票證本身 | 憑票證下載產出（瀏覽器原生下載） |
| GET | `/api/history/{id}/download` | 同上 | 下載打包產出（header 認證，供 API/CLI） |
| DELETE | `/api/history/{id}/workspace` | 同上 | 手動刪除該任務的工作區 |
| GET | `/api/history/export` | `history:export` | 匯出 CSV |
| GET | `/api/history/users` | `history:view_all` | 列出有歷史記錄的使用者 |

### Git 來源模式 `/api/git`

| 方法 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| POST | `/api/git/refs` | `task:create` | 列出 repo 的分支與標籤 |
| POST | `/api/git/scan-tree` | `task:create` | 淺層掃描頂層目錄與 dependency-groups |
| POST | `/api/git/preview-frontend` | `task:create` | 偵測前端設定並讀取 env 檔 |
| POST | `/api/git/diagnose` | `task:create` | 診斷 clone 後的 repo 結構 |

### 本機專案工具 `/api`

| 方法 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| GET | `/api/system-info` | 登入 | CPU 核心數、可用的 Python 編譯版本、工作區路徑 |
| GET | `/api/directories?path=` | 登入 | 列出子目錄 |
| GET | `/api/analyze-project?path=&entry_point=` | 登入 | 分析專案結構，建議 extra/data dirs |
| GET | `/api/pyproject-groups?path=` | 登入 | 讀取 `[dependency-groups]` |
| GET | `/api/detect-frontend-config?path=` | 登入 | 偵測 build tool / command / 輸出目錄 |
| GET | `/api/list-env-files?path=` | 登入 | 列出 `.env*` 檔案 |
| GET | `/api/env-file?path=&filename=` | 登入 | 讀取 env 檔案內容 |

### 使用者與角色 `/api/users`

| 方法 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| GET | `/api/users` | `user:view` | 使用者列表 |
| GET | `/api/users/count` | `user:view` | 使用者數量 |
| GET | `/api/users/{id}` | `user:view` | 單一使用者 |
| GET | `/api/users/{id}/login-history` | `user:view` | 該使用者的登入記錄 |
| PUT | `/api/users/{id}/role` | `user:manage` | 變更角色 |
| PUT | `/api/users/{id}/status` | `user:manage` | 啟用 / 停用帳號（**新註冊帳號在此核准**） |
| POST | `/api/users/{id}/reset-password` | `user:reset_password` | 管理員重設密碼 |
| GET | `/api/users/roles/list` | `role:view` | 角色列表 |
| GET | `/api/users/roles/{id}` | `role:view` | 單一角色 |
| GET | `/api/users/permissions/list` | `role:view` | 所有權限碼 |
| POST | `/api/users/roles` | `role:manage` | 建立角色 |
| PUT | `/api/users/roles/{id}` | `role:manage` | 編輯角色 |
| DELETE | `/api/users/roles/{id}` | `role:manage` | 刪除角色 |

### 監控 `/api/monitoring`

| 方法 | 路徑 | 權限 | 說明 |
|------|------|------|------|
| GET | `/api/monitoring/stats` | 登入 | CPU / 記憶體 / 磁碟 / GPU 使用狀況 |

### WebSocket

| 路徑 | 權限 | 說明 |
|------|------|------|
| `WS /ws/tasks` | `task:view_all` | 所有任務的即時串流 |
| `WS /ws/tasks/{task_id}` | 任務擁有者或 `task:view_all` | 單一任務的即時日誌與進度 |

**認證方式**：JWT 透過 `Sec-WebSocket-Protocol` header 傳遞，**不放在 URL**
（query string 會被寫進 access log 與 proxy log）：

```js
new WebSocket(url, ['bearer', accessToken])
```

伺服器接受連線時會回應 `bearer` 子協定。舊版的 `?token=` query 參數仍可用但不建議。

連線後**不會**重播既有日誌 —— 用戶端應先用 `GET /api/tasks/{id}/logs` 取快照，
WebSocket 只推送連線之後的增量。

---

## 常見問題

### Q: 資料庫連線失敗

確認 PostgreSQL 正在運行，且 `.env` 中的設定正確：

```bash
# 檢查 PostgreSQL 狀態
systemctl status postgresql

# 確認資料庫存在
psql -U postgres -l | grep nuitka_db

# 如果不存在，建立資料庫
createdb -U postgres nuitka_db
```

### Q: 第一次進入沒有出現註冊畫面

確認資料庫遷移已執行，且 `roles` 表中有 admin 和 user 角色：

```bash
uv run alembic upgrade head
```

### Q: 打包失敗：Nuitka not found

確認 Nuitka 已安裝在服務的 Python 環境中：

```bash
uv run python -m nuitka --version
```

### Q: 前端 Build 失敗

```bash
cd frontend
rm -rf node_modules
npm install
npm run build
```

### Q: Docker build 失敗 "curl not found"

已修復。`python:*-slim` 映像不包含 curl，系統會自動先安裝 curl 再安裝 Node.js。

### Q: Docker 容器讀不到 .env

`.env` 不會被打包進 Docker image。執行容器時需帶入：

```bash
docker run --rm --network host --env-file /path/to/.env image-name:latest
```

### Q: 如何重建 Python 虛擬環境

```bash
rm -rf .venv
uv sync
```
