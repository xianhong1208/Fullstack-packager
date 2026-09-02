# Build Center

**A full-stack build & packaging platform.** Point it at a local path or a Git URL and it compiles Python to a standalone executable with [Nuitka](https://nuitka.net/), builds frontends with npm / yarn / pnpm / bun, packages the two together, and can export the result as a Docker image `.tar.gz` — all from a web console with live build logs, user accounts and role-based permissions.

[繁體中文](README.zh-TW.md) · [Quick start](#quick-start) · [How it works](#how-it-works) · [Configuration](#configuration) · [Git credentials](#git-credentials) · [Detailed manual](docs/manual.zh-TW.md)

## Key features

- **Four build modes.** Backend-only (Nuitka), frontend-only (any Node package manager), full-stack (frontend bundled into the Nuitka build as data), and Docker output (the build packaged into an image and exported as `.tar.gz`).
- **Local path or Git URL sources.** Git mode clones the repo, bootstraps a `.venv` with `uv sync`, builds, and cleans the workspace afterward. SSRF-hardened URL validation, argv-only subprocess calls, and per-host clone timeouts.
- **Git credentials managed in the console.** Add GitHub, GitLab and self-hosted tokens in the UI; they are stored encrypted and chosen automatically by the clone URL's host — no secrets in `.env`.
- **Live logs and history.** Every build streams over WebSocket; finished builds keep a full record with the exact config that produced them, so a rebuild is deterministic.
- **Accounts, roles, permissions.** JWT sessions with refresh-token rotation, an RBAC model (users / roles / permissions), login history and an audit log. First run creates the administrator.
- **Built to run unattended.** Background sweeps reclaim workspace and exported-image disk on a TTL, and a systemd unit keeps the service up.

## Install

**Prerequisites:** Python 3.13+, [uv](https://docs.astral.sh/uv/), Node.js (for frontend builds), and — for the engines they drive — Nuitka's toolchain (a C compiler) and Docker (for Docker output). The database is a local SQLite file by default (no setup); PostgreSQL is optional (set `DATABASE_URL`). `pigz` is optional and speeds up image compression.

```bash
git clone https://github.com/xianhong1208/Fullstack-packager.git build-center
cd build-center
uv sync                       # backend deps
cd frontend && npm install && npm run build && cd ..
cp .env.example .env          # optional; SQLite works with no config
```

## Quick start

```bash
uv run python main.py         # applies migrations, then serves on http://0.0.0.0:5018
```

- Open `http://localhost:5018` and sign in with the default account **`admin` / `password123`** (created on first start — change the password after logging in, or set `BOOTSTRAP_ADMIN_*` before first start).
- **Add a Git credential** (optional, for private repos): Settings → Git 憑證 → choose GitHub / GitLab, paste a Personal Access Token.
- **Create a build**: pick Local path or Git URL, choose the mode, and submit. Logs stream live; the finished artifact is downloadable (or exported as a Docker image `.tar.gz`).

For a development frontend with hot reload, run `npm run dev` in `frontend/` (it proxies the API to `:5018`). A step-by-step walkthrough (in Traditional Chinese) is in [docs/quickstart.zh-TW.md](docs/quickstart.zh-TW.md).

## How it works

```
Console (React + antd)  ──HTTP / WebSocket──▶  FastAPI
                                                 │
   auth (JWT + RBAC) ── tasks ── monitoring      │
                                                 ▼
                          build dispatcher ──▶ Nuitka / npm / Docker workers
                                                 │  (git clone → uv sync → build)
                                                 ▼
                          artifacts + Docker image .tar.gz
```

- **Sources.** A build is either a *local path* (confined to `LOCAL_SOURCE_ROOTS`) or a *Git URL*. Git mode validates the URL (no `file://`, `ssh://`, localhost, link-local or credential-bearing URLs), clones at a specific branch/tag, and — for Python projects — bootstraps a `.venv` before building.
- **Token injection.** For a private repo the token for that host is looked up, decrypted, and injected into the clone URL only at subprocess-call time (`x-access-token:…@` for GitHub, `oauth2:…@` for GitLab); it is never persisted into the URL, logged unmasked, or returned by the API.
- **Concurrency.** `MAX_CONCURRENT_BUILDS` bounds simultaneous Nuitka compiles behind a semaphore; the process is intentionally single-worker because task state, WebSocket connections and the build semaphore live in process memory.
- **Cleanup.** A background loop deletes per-task workspaces and exported images by status and age, and (optionally) shrinks a successful build's workspace to just its output.

## Configuration

Everything is environment-driven. `.env.example` lists the essentials; `app/config.py` has the full set with defaults. The most important ones:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(empty → SQLite)* | A local SQLite file by default; set a `postgresql+asyncpg://…` DSN to use PostgreSQL. |
| `HOST` / `PORT` | `0.0.0.0` / `5018` | Bind address. |
| `JWT_SECRET_KEY` | auto | Session signing key; generated to `.env` on first start if empty. |
| `SETTINGS_ENCRYPTION_KEY` | auto | Encrypts stored Git tokens; generated to `.env` if empty. |
| `CORS_ORIGINS` | dev ports | CSV of browser origins allowed to call the API. |
| `GIT_WORKSPACE_DIR` | `./data/workspace` | Where clones and build workspaces live. |
| `LOCAL_SOURCE_ROOTS` | *(empty)* | CSV of absolute prefixes a local-source build may read; empty disables local mode. |
| `UV_BIN` / `PRE_BUILD_PATH` | auto | uv binary and subprocess PATH; auto-detected when empty. |
| `MAX_CONCURRENT_BUILDS` | `3` | Simultaneous Nuitka builds. |
| `TRUSTED_PROXY_IPS` | *(empty)* | Proxy IPs whose `X-Forwarded-For` may be trusted; empty is safest. |

## Git credentials

Access tokens for private repositories are managed in the console (Settings → Git 憑證), not in `.env`:

- One token per host, for **GitHub**, **GitLab**, or a self-hosted instance (any host).
- Tokens are encrypted at rest with `SETTINGS_ENCRYPTION_KEY` and never returned by the API — the UI shows only the last four characters.
- When a build clones a URL, the token whose host matches is selected automatically and injected with the provider's expected form.
- `GITLAB_TOKEN` in `.env` remains a fallback for GitLab-style hosts when no stored credential matches.

## Testing

```bash
uv run pytest -q tests/unit          # backend unit tests (DB-free)
cd frontend && npx vitest run        # frontend unit tests
```

## Security & trust model

Build Center runs untrusted build commands by design, so account approval is the real security boundary: anyone who can create a task can run code as the service user. Git URL validation, local-path confinement, subprocess argv-only calls, token masking and rate limiting are defence in depth, not a sandbox. Run it on an isolated host, front it with HTTPS, and keep `TRUSTED_PROXY_IPS` empty unless a proxy actually rewrites `X-Forwarded-For`.

## License

[MIT](LICENSE)
