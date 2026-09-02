import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'
import type {
  BuildStats,
  HistoryItem,
  TaskCreate,
  TaskResponse,
  RefreshTokenResponse,
  DetectedFrontendConfig,
  GitRefs,
  RepoDiagnosis,
  GitPreviewFrontend,
  ProjectAnalysis,
} from './types'

// Token storage utilities (synced with AuthContext)
const ACCESS_TOKEN_KEY = 'access_token'
const REFRESH_TOKEN_KEY = 'refresh_token'
const TOKEN_EXPIRY_KEY = 'token_expiry'

const getAccessToken = () =>
  sessionStorage.getItem(ACCESS_TOKEN_KEY) || localStorage.getItem(ACCESS_TOKEN_KEY)
const getRefreshToken = () =>
  sessionStorage.getItem(REFRESH_TOKEN_KEY) || localStorage.getItem(REFRESH_TOKEN_KEY)

const setTokens = (accessToken: string, refreshToken: string, expiresIn: number) => {
  const expiry = Date.now() + expiresIn * 1000
  // Preserve the storage location preference set at login (rememberMe →
  // localStorage, otherwise sessionStorage — both tokens live together)
  const storage = localStorage.getItem(REFRESH_TOKEN_KEY) ? localStorage : sessionStorage
  storage.setItem(ACCESS_TOKEN_KEY, accessToken)
  storage.setItem(REFRESH_TOKEN_KEY, refreshToken)
  storage.setItem(TOKEN_EXPIRY_KEY, expiry.toString())
}

const clearTokens = () => {
  for (const storage of [localStorage, sessionStorage]) {
    storage.removeItem(ACCESS_TOKEN_KEY)
    storage.removeItem(REFRESH_TOKEN_KEY)
    storage.removeItem(TOKEN_EXPIRY_KEY)
  }
}

// Create axios instance
const api = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
})

// Create separate instance for auth endpoints (no auto-refresh)
const authApi = axios.create({
  baseURL: '/auth',
  headers: {
    'Content-Type': 'application/json',
  },
})

/** Fired after a successful rotation so AuthContext can re-arm its timer. */
export const TOKENS_REFRESHED_EVENT = 'auth:tokens-refreshed'

// The one place a refresh happens.
//
// The backend rotates on every refresh and revokes the token it was handed,
// so two callers that each read the stored value a moment apart cannot both
// succeed: the second presents a token that was valid when it looked and is
// revoked by the time it arrives, gets a 401, and logs the user out of a
// session whose new token is sitting in storage. There were two callers —
// this interceptor on a 401, and AuthContext's timer two minutes before
// expiry — each with its own in-progress flag and no knowledge of the other.
//
// A call arriving while one is in flight now waits for that result instead of
// starting a competing rotation. This also replaces the queue the interceptor
// used to keep: concurrent 401s all await the same promise and all retry with
// the same new access token.
let refreshInFlight: Promise<string> | null = null

export const refreshTokens = (): Promise<string> => {
  if (refreshInFlight) return refreshInFlight

  const attempt = (async (): Promise<string> => {
    const refreshToken = getRefreshToken()
    if (!refreshToken) {
      throw new Error('No refresh token available')
    }

    const response = await authApi.post<RefreshTokenResponse>('/refresh', {
      refresh_token: refreshToken,
    })

    const { access_token, refresh_token: newRefreshToken, expires_in } = response.data
    setTokens(access_token, newRefreshToken, expires_in)

    // AuthContext arms its timer from the previous expiry. Without this it
    // would fire against a token this call already replaced — harmless once,
    // but it is the same needless extra rotation the shared flight exists to
    // avoid.
    window.dispatchEvent(
      new CustomEvent(TOKENS_REFRESHED_EVENT, { detail: { expiresIn: expires_in } })
    )

    return access_token
  })()

  refreshInFlight = attempt
  const clear = () => {
    if (refreshInFlight === attempt) refreshInFlight = null
  }
  attempt.then(clear, clear)

  return attempt
}


// Add auth token to all requests
api.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Handle 401 errors with automatic token refresh
api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean }

    // Don't retry if not 401 or already retried
    if (error.response?.status !== 401 || originalRequest._retry) {
      return Promise.reject(error)
    }

    // Check if we have a refresh token
    const refreshToken = getRefreshToken()
    if (!refreshToken) {
      clearTokens()
      window.location.href = '/login'
      return Promise.reject(error)
    }

    originalRequest._retry = true

    try {
      const accessToken = await refreshTokens()
      originalRequest.headers.Authorization = `Bearer ${accessToken}`
      return api(originalRequest)
    } catch (refreshError) {
      clearTokens()
      window.location.href = '/login'
      return Promise.reject(refreshError)
    }
  }
)

export const taskApi = {
  // Get server system info (CPU core count)
  getSystemInfo: async (): Promise<{
    cpu_count: number
    python_versions?: string[]
    git_workspace_dir?: string
  }> => {
    const response = await api.get<{
      cpu_count: number
      python_versions?: string[]
      git_workspace_dir?: string
    }>('/system-info')
    return response.data
  },

  // List directories in a path
  listDirectories: async (path: string): Promise<{ path: string; directories: string[] }> => {
    const response = await api.get<{ path: string; directories: string[] }>('/directories', {
      params: { path },
    })
    return response.data
  },

  // Analyze a local project → suggest which dirs to bundle (source/data)
  analyzeProject: async (path: string, entryPoint = 'main.py'): Promise<ProjectAnalysis> => {
    const response = await api.get<ProjectAnalysis>('/analyze-project', {
      params: { path, entry_point: entryPoint },
    })
    return response.data
  },

  // List PEP 735 dependency-group names from a local project's pyproject.toml
  listPyprojectGroups: async (path: string): Promise<{ groups: string[] }> => {
    const response = await api.get<{ groups: string[] }>('/pyproject-groups', {
      params: { path },
    })
    return response.data
  },

  // Detect frontend config from vite.config and package.json
  detectFrontendConfig: async (path: string): Promise<DetectedFrontendConfig> => {
    const response = await api.get<DetectedFrontendConfig>('/detect-frontend-config', {
      params: { path },
    })
    return response.data
  },

  // List .env* files in a directory
  listEnvFiles: async (path: string): Promise<{ path: string; files: string[] }> => {
    const response = await api.get<{ path: string; files: string[] }>('/list-env-files', {
      params: { path },
    })
    return response.data
  },

  // Read existing env file from frontend directory
  readEnvFile: async (path: string, filename: string): Promise<{ path: string; filename: string; content: string }> => {
    const response = await api.get<{ path: string; filename: string; content: string }>('/env-file', {
      params: { path, filename },
    })
    return response.data
  },

  // --- Git source mode ---
  // POST (not GET) is used so the URL never lands in nginx access logs
  // or browser history. The server injects its token; never send one.
  listGitRefs: async (git_url: string): Promise<GitRefs> => {
    const response = await api.post<GitRefs>('/git/refs', { git_url })
    return response.data
  },

  // Diagnose an already-cloned (or local) repo directory.
  diagnoseRepo: async (path: string): Promise<RepoDiagnosis> => {
    const response = await api.post<RepoDiagnosis>('/git/diagnose', { path })
    return response.data
  },

  // List top-level directories of a remote Git repo @ ref, without cloning
  // the working tree. Used to populate the extra_dirs/data_dirs pickers
  // in git mode.
  scanGitTree: async (
    git_url: string,
    git_ref: string,
    git_ref_type: 'branch' | 'tag',
  ): Promise<{ directories: string[]; dependency_groups: string[]; analysis?: ProjectAnalysis }> => {
    const response = await api.post<{ directories: string[]; dependency_groups: string[]; analysis?: ProjectAnalysis }>(
      '/git/scan-tree',
      {
        git_url,
        git_ref,
        git_ref_type,
      },
    )
    return response.data
  },

  // Shallow-clone a repo and return its frontend config + .env* file
  // contents. Used by CreateTask in git mode so the Frontend Settings card
  // can auto-detect build tool / output dir and let the user load env files
  // without touching a local path.
  previewGitFrontend: async (
    git_url: string,
    git_ref: string,
    git_ref_type: 'branch' | 'tag',
    frontend_dir: string,
  ): Promise<GitPreviewFrontend> => {
    const response = await api.post<GitPreviewFrontend>('/git/preview-frontend', {
      git_url,
      git_ref,
      git_ref_type,
      frontend_dir,
    })
    return response.data
  },

  // Create a new task
  create: async (data: TaskCreate): Promise<TaskResponse> => {
    const response = await api.post<TaskResponse>('/tasks', data)
    return response.data
  },

  // Get all active tasks
  getActive: async (): Promise<TaskResponse[]> => {
    const response = await api.get<TaskResponse[]>('/tasks')
    return response.data
  },

  // Get a specific task
  get: async (taskId: string): Promise<TaskResponse> => {
    const response = await api.get<TaskResponse>(`/tasks/${taskId}`)
    return response.data
  },

  // Cancel/delete a task
  cancel: async (taskId: string): Promise<void> => {
    await api.delete(`/tasks/${taskId}`)
  },

  // Get task logs
  getLogs: async (taskId: string): Promise<{ task_id: string; logs: string[] }> => {
    const response = await api.get<{ task_id: string; logs: string[] }>(`/tasks/${taskId}/logs`)
    return response.data
  },

  // Get history. `user_name` narrows to one user (view_all only; ignored
  // server-side for view_own callers). `limit` defaults high so a single
  // user's full history fits on one page.
  getHistory: async (params?: {
    user_name?: string
    limit?: number
    offset?: number
  }): Promise<HistoryItem[]> => {
    const response = await api.get<HistoryItem[]>('/history', {
      params: { limit: 1000, ...params },
    })
    return response.data
  },

  // Distinct usernames in history (view_all only) — for the user filter.
  listHistoryUsers: async (): Promise<string[]> => {
    const response = await api.get<string[]>('/history/users')
    return response.data
  },

  // Get a single history record
  getHistoryItem: async (taskId: string): Promise<HistoryItem> => {
    const response = await api.get<HistoryItem>(`/history/${taskId}`)
    return response.data
  },

  // Export history as CSV (optionally narrowed to one user, view_all only)
  exportHistory: async (user_name?: string): Promise<Blob> => {
    const response = await api.get('/history/export', {
      params: user_name ? { user_name } : undefined,
      responseType: 'blob',
    })
    return response.data
  },

  // Force-delete a git-source task's workspace on disk (history row stays).
  deleteTaskWorkspace: async (taskId: string): Promise<{ task_id: string; deleted: boolean }> => {
    const response = await api.delete<{ task_id: string; deleted: boolean }>(
      `/history/${taskId}/workspace`,
    )
    return response.data
  },

  // Build output download: exchange the session for a short-lived, single-
  // artifact ticket and return a URL the browser can fetch on its own.
  //
  // Deliberately NOT `responseType: 'blob'` — an artifact is often multiple GB,
  // and buffering it in page memory loses resume, progress and (on the largest
  // images) the tab. The caller passes this URL to triggerUrlDownload().
  //
  // Permission and ownership are checked when the ticket is minted AND again
  // when it is redeemed, so a revoked account cannot finish a download it
  // started.
  // Aggregated build outcomes for the dashboard. Scoped server-side the same
  // way as the history list, so the numbers always match the records the
  // caller can actually open.
  getBuildStats: async (days = 30): Promise<BuildStats> => {
    const response = await api.get<BuildStats>('/monitoring/build-stats', {
      params: { days },
    })
    return response.data
  },

  getOutputDownloadUrl: async (taskId: string): Promise<string> => {
    const response = await api.post<{ ticket: string; expires_in: number }>(
      `/history/${taskId}/download-ticket`,
    )
    return `/api/download/${encodeURIComponent(response.data.ticket)}`
  },
}

export default api
