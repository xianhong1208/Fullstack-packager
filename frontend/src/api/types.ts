// API types mirroring backend Pydantic schemas

export const TaskStatus = {
  PENDING: 'pending',
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
} as const

export type TaskStatus = (typeof TaskStatus)[keyof typeof TaskStatus]

export const ProjectType = {
  BACKEND_ONLY: 'backend_only',
  FRONTEND_ONLY: 'frontend_only',
  FULLSTACK: 'fullstack',
} as const

export type ProjectType = (typeof ProjectType)[keyof typeof ProjectType]

export const PackMode = {
  FULL: 'full',
  EXTERNAL: 'external',
} as const

export type PackMode = (typeof PackMode)[keyof typeof PackMode]

export const FrontendBuildTool = {
  NPM: 'npm',
  YARN: 'yarn',
  PNPM: 'pnpm',
  BUN: 'bun',
} as const

export type FrontendBuildTool = (typeof FrontendBuildTool)[keyof typeof FrontendBuildTool]

export const SourceType = {
  LOCAL: 'local',
  GIT: 'git',
} as const

export type SourceType = (typeof SourceType)[keyof typeof SourceType]

export type GitRefType = 'branch' | 'tag'

export interface GitRefs {
  branches: string[]
  tags: string[]
}

export type RepoDiagnosisStatus = 'ok' | 'warn' | 'missing'

export interface RepoDiagnosisCheck {
  key: string
  label: string
  status: RepoDiagnosisStatus
  path: string | null
}

export interface RepoDiagnosis {
  checks: RepoDiagnosisCheck[]
}

export interface GitPreviewFrontend {
  frontend_config: DetectedFrontendConfig
  // Map of .env* filename -> UTF-8 content (files > 100KB are omitted)
  env_files: Record<string, string>
  frontend_dir_exists: boolean
}

export interface BuildConfig {
  // Project Type
  project_type: ProjectType
  // Source selection
  source_type: SourceType
  git_url: string
  git_ref: string
  git_ref_type: GitRefType
  // Common
  project_path: string
  output_dir: string
  // Backend (for backend_only and fullstack)
  python_version: string
  entry_point: string
  output_name: string
  onefile: boolean
  extra_dirs: string
  data_dirs: string
  pack_mode: PackMode
  include_packages: string
  // PEP 735 dependency groups to enable during `uv sync` (e.g. ['rocm'])
  // Maps to `uv sync --group <name>` flags. Empty array = no --group flag.
  dependency_groups: string[]
  // Frontend (for frontend_only and fullstack)
  frontend_dir: string
  frontend_build_tool: FrontendBuildTool
  frontend_build_command: string
  frontend_output_dir: string
  frontend_env_filename: string
  frontend_env_content: string
  // Docker output
  docker_enabled: boolean
  // Docker settings (when docker_enabled)
  docker_image_name: string
  docker_base_image: string
  docker_expose_port: number
  docker_env_vars: string
  docker_custom_commands: string
  docker_install_node: boolean
  docker_api_proxy: string
  // nginx settings (frontend-only Docker image)
  nginx_client_max_body_size: string
  /** Idle timeout for the proxied backend — how long it may send nothing
   *  before nginx gives up. Not a cap on total request duration. */
  nginx_proxy_read_timeout: string
  /** Disable nginx buffering so SSE / chunked responses actually stream. */
  nginx_streaming?: boolean
  nginx_gzip: boolean
  // Nuitka performance
  nuitka_jobs: number
  // Nuitka optimization / diagnostics
  enable_anti_bloat: boolean
  include_package_data: string
  generate_report: boolean
  verify_after_build: boolean
}

// ===== Build result (preflight / artifact / verify) =====

export interface PreflightCheck {
  label: string
  passed: boolean
  detail: string
  critical: boolean
}

export interface ArtifactInfo {
  path: string
  size_bytes: number
  size_human: string
  file_count: number
  sha256: string | null
}

export type VerifyStatus = 'pass' | 'warn' | 'fail' | 'skipped'

export interface VerifyResult {
  status: VerifyStatus
  detail: string
  exit_code: number | null
}

export interface DiagnosisAction {
  label: string
  /** BuildConfig fields to change before rebuilding. Expressed as data so a
   *  new rule can offer a fix without any frontend change. */
  overrides: Record<string, unknown>
}

export interface Diagnosis {
  problem: string
  suggestion: string
  evidence: string
  /** Present only when the platform can apply the fix itself — most failures
   *  are in the user's own code and carry no action. */
  action?: DiagnosisAction | null
}

export interface BuildResult {
  preflight: PreflightCheck[]
  artifact: ArtifactInfo | null
  verify: VerifyResult | null
  report_file: string | null
  duration_seconds: number | null
  diagnosis: Diagnosis[]
  /** Error lines selected server-side and persisted with the record. Logs live
   *  only in memory and are evicted after an hour, so deriving them client-side
   *  produced nothing when revisiting an older failure — these survive. */
  error_lines?: string[]
  /** Build stage that was active when the failure happened. */
  failed_stage?: string | null
}

export interface TaskCreate {
  project_name: string
  config: BuildConfig
}

export interface TaskResponse {
  id: string
  user_name: string
  project_name: string
  python_version: string
  status: TaskStatus
  progress: number
  status_msg: string
  stage: string
  created_at: string
  updated_at: string | null
  config: BuildConfig
  logs: string[]
  result: BuildResult | null
}

export interface HistoryItem {
  task_id: string
  user_name: string
  project_name: string
  python_version: string
  start_time: string
  end_time: string | null
  status: TaskStatus
  output_dir: string
  config?: BuildConfig
  result?: BuildResult | null
}

export interface DetectedFrontendConfig {
  detected: boolean
  build_tool: FrontendBuildTool | null
  build_command: string | null
  output_dir: string | null
  scripts: string[]
  has_vite_config: boolean
}

// ===== Project analysis (auto-suggest what to bundle) =====

export interface DirectoryClassification {
  name: string
  role: 'source' | 'data' | 'skip'
  reason: string
  imported_by_entry: boolean
  py_files: number
  data_files: number
}

export interface DetectedPython {
  /** Version read from the ABI tags on the project's compiled .so files, or
   *  null when there are none (pure Python) or they disagree. */
  version: string | null
  detail: string
  /** True when the .so files name conflicting versions — the venv itself is
   *  broken and no dropdown choice can compile it correctly. */
  inconsistent: boolean
}

export interface ProjectAnalysis {
  directories: DirectoryClassification[]
  entry_imports: string[]
  suggested_extra_dirs: string[]
  suggested_data_dirs: string[]
  detected_python?: DetectedPython
}

export interface WebSocketMessage {
  type: 'log' | 'status' | 'progress' | 'status_msg' | 'stage' | 'result' | 'error' | 'pong'
  task_id: string
  // Shape depends on `type`; consumers narrow + cast per case.
  data: unknown
  timestamp: string
}

// ===== Auth Types =====

export interface RoleSummary {
  id: number
  name: string
  display_name: string
}

export interface User {
  id: number
  username: string
  email?: string | null
  is_active: boolean
  created_at?: string | null
  last_login?: string | null
  role?: RoleSummary | null
  has_security_question: boolean
}

export interface TokenResponse {
  access_token: string
  refresh_token?: string | null
  token_type: string
  expires_in: number
  user: User
  permissions: string[]
}

export interface RefreshTokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export interface LoginRequest {
  username: string
  password: string
  remember_me: boolean
}

// ===== Security Settings Types =====

export interface SecurityQuestionSet {
  question: string
  answer: string
}

export interface ForgotPasswordResponse {
  has_security_question: boolean
  security_question?: string | null
  message: string
}

export interface LoginHistoryItem {
  id: number
  login_time: string
  ip_address?: string | null
  device_type?: string | null
  browser?: string | null
  os?: string | null
  success: boolean
  failure_reason?: string | null
}

export interface LoginHistoryList {
  items: LoginHistoryItem[]
  total: number
  limit: number
  offset: number
}

export interface Session {
  id: number
  created_at: string
  expires_at: string
  is_remember_me: boolean
  device_info?: string | null
  ip_address?: string | null
  is_current: boolean
}

export interface SessionList {
  sessions: Session[]
  total: number
}

// ===== User Management Types =====

export interface Permission {
  id: number
  code: string
  name: string
  description?: string | null
  category: string
}

export interface Role {
  id: number
  name: string
  display_name: string
  description?: string | null
  created_at: string
  permissions: Permission[]
  parent_role_id?: number | null
  is_system: boolean
  is_active: boolean
}

export interface RoleCreate {
  name: string
  display_name: string
  description?: string | null
  permission_codes: string[]
  parent_role_id?: number | null
}

export interface RoleUpdate {
  display_name?: string | null
  description?: string | null
  permission_codes?: string[] | null
  parent_role_id?: number | null
  is_active?: boolean | null
}

export interface UserListItem {
  id: number
  username: string
  email?: string | null
  is_active: boolean
  created_at: string
  last_login?: string | null
  role?: RoleSummary | null
}

export interface PermissionCategory {
  category: string
  permissions: Permission[]
}

// --- Build statistics (dashboard) ---

export interface BuildStatsSummary {
  total: number
  completed: number
  failed: number
  cancelled: number
  success_rate: number | null
  avg_duration_seconds: number | null
}

export interface BuildStatsDaily {
  day: string
  total: number
  completed: number
  success_rate: number | null
}

export interface ProjectHealth {
  project_name: string
  total: number
  completed: number
  failed: number
  success_rate: number | null
  /** Outcome of the most recent build. */
  last_status: string
  last_run: string | null
  /** Last few outcomes, newest first — a run strip shows patterns a
   *  percentage hides (three greens then a red vs. alternating). */
  recent: string[]
}

export interface FailureReason {
  problem: string
  count: number
}

export interface CompileTarget {
  version: string
  venv: string
  usable: boolean
  target: string | null
}

export interface BuildEnvironment {
  service_python: string
  targets: CompileTarget[]
  /** Versions offered in the UI whose venv no longer resolves. */
  degraded: string[]
}

export interface BuildStats {
  window_days: number
  scope: 'own' | 'all'
  summary: BuildStatsSummary
  daily: BuildStatsDaily[]
  projects: ProjectHealth[]
  top_failures: FailureReason[]
  /** Percent of failures that produced an explanation. Low = users retry blind. */
  diagnosis_coverage: number | null
  environment: BuildEnvironment
}
