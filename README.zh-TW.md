# Build Center

**全端打包平台。** 指向一個本機路徑或 Git URL,它就用 [Nuitka](https://nuitka.net/) 把 Python 編成獨立執行檔、用 npm / yarn / pnpm / bun 建置前端、把兩者打包在一起,還能把結果輸出成 Docker image `.tar.gz`——全部在一個有即時建置日誌、使用者帳號與角色權限的網頁管理台裡完成。

[English](README.md) · [快速開始](#快速開始) · [運作方式](#運作方式) · [設定](#設定) · [Git 憑證](#git-憑證) · [完整手冊](docs/manual.zh-TW.md)

## 主要功能

- **四種打包模式。** 純後端(Nuitka)、純前端(任一 Node 套件管理器)、全端(前端產出作為 data 一起編進 Nuitka),以及 Docker 輸出(打包成 image 並匯出 `.tar.gz`)。
- **本機路徑或 Git URL 來源。** Git 模式會 clone repo、用 `uv sync` 建立 `.venv`、打包,完成後清理工作區。URL 驗證做了 SSRF 防護、subprocess 一律 argv 形式、每次 clone 有逾時上限。
- **Git 憑證在管理台設定。** 在介面新增 GitHub、GitLab 與自架服務的 token;加密儲存,依 clone URL 的主機自動選用——`.env` 裡不放任何密鑰。
- **即時日誌與歷史。** 每個建置都透過 WebSocket 串流;完成的建置保留當時完整設定,重新打包可完全重現。
- **帳號、角色、權限。** JWT session + refresh token 輪替、RBAC(使用者 / 角色 / 權限)、登入歷史與稽核日誌。首次啟動建立管理員。
- **可長期無人值守。** 背景排程依 TTL 回收工作區與匯出的 image 磁碟空間,systemd unit 維持服務常駐。

## 安裝

**環境需求:** Python 3.13+、[uv](https://docs.astral.sh/uv/)、Node.js(前端建置用),以及各引擎所需的 Nuitka 工具鏈(C 編譯器)與 Docker(Docker 輸出用)。資料庫預設用本機 SQLite 檔(免設定);PostgreSQL 為選用(設 `DATABASE_URL`)。`pigz` 選用,可加速 image 壓縮。

```bash
git clone https://github.com/xianhong1208/Fullstack-packager.git build-center
cd build-center
uv sync                       # 後端相依
cd frontend && npm install && npm run build && cd ..
cp .env.example .env          # 選用;用 SQLite 完全免設定
```

## 快速開始

```bash
uv run python main.py         # 先套用 migration,再於 http://0.0.0.0:5018 提供服務
```

- 開啟 `http://localhost:5018`,用預設帳號 **`admin` / `password123`** 登入(首次啟動自動建立——登入後請改密碼,或在首次啟動前設 `BOOTSTRAP_ADMIN_*`)。
- **新增 Git 憑證**(選用,私有 repo 需要):設定 → Git 憑證 → 選 GitHub / GitLab,貼上 Personal Access Token。
- **建立打包**:選本機路徑或 Git URL、選模式、送出。日誌即時串流;完成的產物可下載(或匯出成 Docker image `.tar.gz`)。

前端開發熱重載可在 `frontend/` 執行 `npm run dev`(會把 API 代理到 `:5018`)。逐步操作教學見 [docs/quickstart.zh-TW.md](docs/quickstart.zh-TW.md)。

## 運作方式

```
管理台 (React + antd)  ──HTTP / WebSocket──▶  FastAPI
                                              │
   auth (JWT + RBAC) ── tasks ── monitoring   │
                                              ▼
                          build dispatcher ──▶ Nuitka / npm / Docker workers
                                              │  (git clone → uv sync → 打包)
                                              ▼
                          產物 + Docker image .tar.gz
```

- **來源。** 一個建置不是*本機路徑*(限制在 `LOCAL_SOURCE_ROOTS` 內)就是 *Git URL*。Git 模式會驗證 URL(拒絕 `file://`、`ssh://`、localhost、link-local、或帶帳密的 URL),clone 到指定 branch/tag,Python 專案還會先建 `.venv` 再打包。
- **Token 注入。** 私有 repo 會依主機查出對應 token、解密,只在 subprocess 呼叫當下注入 clone URL(GitHub 用 `x-access-token:…@`、GitLab 用 `oauth2:…@`);絕不寫進持久化 URL、不明文記錄、不由 API 回傳。
- **併發。** `MAX_CONCURRENT_BUILDS` 以 semaphore 限制同時的 Nuitka 編譯;行程刻意單一 worker,因為任務狀態、WebSocket 連線與 build semaphore 都在行程記憶體裡。
- **清理。** 背景迴圈依狀態與時間刪除各任務工作區與匯出的 image,並可將成功建置的工作區縮到只剩產出。

## 設定

全部由環境變數驅動。`.env.example` 只列必填,完整清單與預設值見 `app/config.py`。最重要的幾個:

| 變數 | 預設 | 說明 |
|---|---|---|
| `DATABASE_URL` | *(空 → SQLite)* | 預設用本機 SQLite 檔;設 `postgresql+asyncpg://…` DSN 則改用 PostgreSQL。 |
| `HOST` / `PORT` | `0.0.0.0` / `5018` | 綁定位址。 |
| `JWT_SECRET_KEY` | 自動 | Session 簽章金鑰;留空則首次啟動產生並寫入 `.env`。 |
| `SETTINGS_ENCRYPTION_KEY` | 自動 | 加密儲存的 Git token;留空則自動產生寫入 `.env`。 |
| `CORS_ORIGINS` | 開發埠 | 允許呼叫 API 的瀏覽器來源(CSV)。 |
| `GIT_WORKSPACE_DIR` | `./data/workspace` | clone 與建置工作區位置(非 Docker 產物也從這裡下載)。 |
| `DOCKER_IMAGES_DIR` | `./data/docker_images` | 匯出的 Docker image(`.tar.gz`)寫入位置。 |
| `LOCAL_SOURCE_ROOTS` | *(空)* | 本機來源可讀取的絕對路徑前綴(CSV);留空則停用本機模式。 |
| `UV_BIN` / `PRE_BUILD_PATH` | 自動 | uv 路徑與 subprocess PATH;留空自動偵測。 |
| `MAX_CONCURRENT_BUILDS` | `3` | 同時進行的 Nuitka 建置數。 |
| `TRUSTED_PROXY_IPS` | *(空)* | 可信任 `X-Forwarded-For` 的代理 IP;留空最安全。 |

## 儲存與資料

Build Center 所有狀態都寫在單一的 `data/` 目錄下。預設這些路徑是**相對於你啟動時的工作目錄**(執行 `uv run python main.py` 的位置),所以預設安裝會把資料全部放在專案內:

```
data/
├── build_center.db     # SQLite 資料庫(使用者、角色、歷史、Git 憑證)
├── workspace/          # 每個建置一個目錄:clone、.venv、以及編譯產物
└── docker_images/      # 匯出的 Docker image,檔名為 <image>_<tag>.tar.gz
```

**建置完成後產物在哪:**
- **二進位 / 打包產物**(後端、前端、全端):留在該次建置的工作區 `data/workspace/<task-id>/`,管理台的 **Download** 按鈕就是從這裡串流下載。
- **Docker 輸出**:image 存成 `data/docker_images/` 下的 `.tar.gz`;之後用 `docker load -i <檔案>` 載入。

**搬移儲存位置**(例如換到更大的磁碟)——在 `.env` 設定絕對路徑後重啟:

```bash
GIT_WORKSPACE_DIR=/srv/build-center/workspace     # clone、建置、可下載的產物
DOCKER_IMAGES_DIR=/srv/build-center/docker_images # 匯出的 image tarball
# 資料庫也可一起搬(SQLite 檔,或改指向 PostgreSQL):
DATABASE_URL=sqlite+aiosqlite:////srv/build-center/build_center.db
```

把 `GIT_WORKSPACE_DIR` 和 `BOOTSTRAP_UV_CACHE_DIR` 放在同一個檔案系統,`uv` 安裝相依時才能用 hardlink 而非複製。

**保留期限**——背景清理會依 TTL 回收磁碟,避免無限成長。由於非 Docker 產物是從工作區下載的,成功 TTL 同時等於*產物可下載的期限*:

| 變數 | 預設 | 保留內容 |
|---|---|---|
| `WORKSPACE_SUCCESS_TTL_DAYS` | `7` | 成功的非 Docker 工作區(即產物可下載多久)。 |
| `WORKSPACE_FAILED_TTL_DAYS` | `30` | 失敗 / 取消的工作區(保留較久以便除錯)。 |
| `DOCKER_IMAGES_TTL_DAYS` | `30` | 匯出的 image tarball。 |

建置成功時工作區也會自動精簡:非 Docker 建置只留下產物(丟掉 clone、`.venv`、`.git`),Docker 建置則直接刪除整個工作區,因為 image tarball 已保存結果。

## Git 憑證

私有 repo 的存取權杖在管理台設定(設定 → Git 憑證),不放在 `.env`:

- 每個主機一組,支援 **GitHub**、**GitLab** 或自架實例(任意主機)。
- 以 `SETTINGS_ENCRYPTION_KEY` 加密儲存,API 不回傳——介面只顯示末四碼。
- 建置 clone URL 時,自動選用主機相符的 token,並以該服務期望的格式注入。
- `.env` 的 `GITLAB_TOKEN` 仍作為 GitLab 類主機在沒有相符憑證時的後備。

## 測試

```bash
uv run pytest -q tests/unit          # 後端單元測試(不需 DB)
cd frontend && npx vitest run        # 前端單元測試
```

## 安全與信任模型

Build Center 本質上會執行不受信任的建置指令,所以**帳號核准才是真正的安全邊界**:任何能建立任務的人都能以服務使用者的身分執行程式碼。Git URL 驗證、本機路徑限制、subprocess argv-only、token 遮罩與速率限制是縱深防禦,不是沙箱。請跑在隔離主機、前面掛 HTTPS,除非真的有代理改寫 `X-Forwarded-For`,否則 `TRUSTED_PROXY_IPS` 保持空白。

## 授權

[MIT](LICENSE)
