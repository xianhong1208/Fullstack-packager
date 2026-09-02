import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import {
  Form,
  Input,
  Select,
  Switch,
  Button,
  Row,
  Col,
  message,
  notification,
  Checkbox,
  Spin,
  Tag,
  Tooltip,
  InputNumber,
  Card,
  Divider,
  AutoComplete,
  Collapse,
  Steps,
  Radio,
  Segmented,
  Tour,
  type TourProps,
} from 'antd'
import {
  RocketOutlined,
  FolderOutlined,
  ReloadOutlined,
  CodeOutlined,
  GlobalOutlined,
  AppstoreOutlined,
  CloudServerOutlined,
  DownloadOutlined,
  ScanOutlined,
  SettingOutlined,
  ArrowLeftOutlined,
  ArrowRightOutlined,
  BranchesOutlined,
  TagOutlined,
  WarningOutlined,
  LinkOutlined,
} from '@ant-design/icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { taskApi } from '../api/client'
import type {
  BuildConfig,
  ProjectType,
  SourceType,
  GitRefs,
  ProjectAnalysis,
} from '../api/types'
import { useFormDraft } from '../hooks/useFormDraft'
import { ALL_STEPS, activeSteps, clampStepIndex, type StepDef, type StepKey } from './createTask/steps'
import { assembleTaskCreate } from './createTask/buildConfig'
import { findPythonMismatch, pickDefaultRef } from './createTask/pythonVersion'

const { Option } = Select

interface RebuildState {
  rebuild: boolean
  projectName: string
  config: BuildConfig
}

export default function CreateTask() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const [form] = Form.useForm()
  const [currentStep, setCurrentStep] = useState(0)
  // Simple vs Advanced form mode. Simple (default) keeps the advanced
  // collapse panels folded so newcomers only see the core fields; Advanced
  // expands everything at once. Users can still fold/unfold individual
  // panels manually — the mode toggle only sets their initial state.
  const [simpleMode, setSimpleMode] = useState(true)
  const [backendAdvKeys, setBackendAdvKeys] = useState<string[]>([])
  const [dockerAdvKeys, setDockerAdvKeys] = useState<string[]>([])
  useEffect(() => {
    setBackendAdvKeys(simpleMode ? [] : ['backend-advanced'])
    setDockerAdvKeys(simpleMode ? [] : ['docker-advanced'])
  }, [simpleMode])

  // Rebuild state from History or TaskDetail navigation
  const rebuildState = location.state as RebuildState | null
  const isRebuild = rebuildState?.rebuild === true

  // Rebuild carries the original task's advanced values — start in Advanced
  // mode so they're visible rather than hidden behind a collapsed panel.
  useEffect(() => {
    if (isRebuild) setSimpleMode(false)
  }, [isRebuild])

  // ── Draft persistence ──────────────────────────────────────────────
  // Extracted to useFormDraft: the rule that the env content must never be
  // written to localStorage is a security property, and it is now pinned by
  // tests rather than by a comment next to a `delete`.
  const {
    savedAt: draftSavedAt,
    scheduleSave: scheduleDraftSave,
    clear: clearDraftStorage,
  } = useFormDraft(form, isRebuild)

  const clearDraft = useCallback(() => {
    clearDraftStorage()
    message.success('已清除草稿')
  }, [clearDraftStorage])

  // Directory selection state
  const [directories, setDirectories] = useState<string[]>([])
  const [selectedDirs, setSelectedDirs] = useState<string[]>([])
  const [selectedDataDirs, setSelectedDataDirs] = useState<string[]>([])
  const [loadingDirs, setLoadingDirs] = useState(false)
  const [dirError, setDirError] = useState<string | null>(null)
  const [currentPath, setCurrentPath] = useState<string>('')

  // Auto project analysis — classifies each scanned directory as
  // source/data/skip and suggests what to bundle. Populated best-effort
  // in local mode (taskApi.analyzeProject) and git mode (scanGitTree's
  // optional `.analysis`). Never blocks the plain directory listing.
  const [analysis, setAnalysis] = useState<ProjectAnalysis | null>(null)
  // Guards the one-time pre-check of recommended dirs so re-scans never
  // clobber the user's later manual edits.
  const analysisPrefilledRef = useRef(false)

  // CPU core count from server
  const [cpuCount, setCpuCount] = useState<number>(0)
  // Compile targets actually available on the server (detected from the
  // per-version Nuitka venvs) — falls back to a static list until loaded
  const [pythonVersions, setPythonVersions] = useState<string[]>(['3.14', '3.13', '3.12'])
  const [workspaceDir, setWorkspaceDir] = useState<string>('/media/disk1/Build_workspace')

  // Watch form values for conditional rendering
  const projectType = Form.useWatch('project_type', form) as ProjectType
  const pythonVersion = Form.useWatch('python_version', form) as string
  const entryPoint = Form.useWatch('entry_point', form) as string
  const dockerEnabled = Form.useWatch('docker_enabled', form) as boolean
  const sourceType = Form.useWatch('source_type', form) as SourceType | undefined
  const gitUrl = Form.useWatch('git_url', form) as string | undefined
  const gitRef = Form.useWatch('git_ref', form) as string | undefined

  // A mismatch worth warning about needs BOTH a confident detection and an
  // explicit choice that contradicts it. "auto" is never a mismatch — it is
  // the fix — and a project with no compiled extensions has nothing to
  // contradict, so staying quiet there avoids crying wolf on pure-Python code.
  // Decision logic lives in ./createTask/pythonVersion with tests. The
  // asymmetry it encodes matters: a false alarm teaches people to dismiss the
  // warning, taking the real ones with it.
  const pythonMismatch = useMemo(
    () => findPythonMismatch(analysis?.detected_python, pythonVersion),
    [analysis, pythonVersion],
  )
  const gitRefType = Form.useWatch('git_ref_type', form) as 'branch' | 'tag' | undefined
  const frontendDirWatch = Form.useWatch('frontend_dir', form) as string | undefined

  // Git refs state
  const [gitRefs, setGitRefs] = useState<GitRefs>({ branches: [], tags: [] })
  const [loadingRefs, setLoadingRefs] = useState(false)
  const [refsFetchedFor, setRefsFetchedFor] = useState<string>('')

  // Git tree scan state (for populating extra_dirs/data_dirs grids in git mode)
  const [scanningTree, setScanningTree] = useState(false)
  const [treeScannedFor, setTreeScannedFor] = useState<string>('')

  // Git frontend preview state (for populating Frontend Settings card
  // auto-detect + env file picker in git mode).
  const [previewingFrontend, setPreviewingFrontend] = useState(false)
  const [frontendPreviewedFor, setFrontendPreviewedFor] = useState<string>('')
  // In git mode, env file content cannot be read from disk on demand —
  // we cache the contents returned by the preview endpoint here so the
  // "Load from project" button becomes an instant local lookup.
  const [gitEnvFilesContent, setGitEnvFilesContent] = useState<Record<string, string>>({})

  // Track the previously seen source_type so we can distinguish
  // "initial render" from "user-initiated mode switch". The ref is
  // set on first observation and never reset, so the dependency
  // useEffect below only runs its reset path on REAL transitions.
  const prevSourceTypeRef = useRef<SourceType | undefined>(undefined)

  // Tour state — shown once per browser via localStorage flag
  const TOUR_STORAGE_KEY = 'bc_tour_createtask_v1'
  const [tourOpen, setTourOpen] = useState(false)
  const sourceModeRef = useRef<HTMLDivElement>(null)
  const gitUrlRef = useRef<HTMLDivElement>(null)
  const refSelectRef = useRef<HTMLDivElement>(null)

  const [loadingEnv, setLoadingEnv] = useState(false)
  const [detectingConfig, setDetectingConfig] = useState(false)
  const [envFiles, setEnvFiles] = useState<string[]>([])
  const [loadingEnvFiles, setLoadingEnvFiles] = useState(false)

  // PEP 735 dependency-group names parsed from the project's pyproject.toml.
  // Populated from /pyproject-groups (local mode) or the scan-tree response
  // (git mode). The picker is single-select and offers only these names.
  const [depGroups, setDepGroups] = useState<string[]>([])
  const [loadingDepGroups, setLoadingDepGroups] = useState(false)

  const hasBackend = projectType === 'backend_only' || projectType === 'fullstack'
  const showFrontendSettings = projectType === 'frontend_only' || projectType === 'fullstack'

  // Fetch CPU core count on mount
  useEffect(() => {
    taskApi.getSystemInfo().then((info) => {
      setCpuCount(info.cpu_count)
      if (info.python_versions?.length) setPythonVersions(info.python_versions)
      if (info.git_workspace_dir) setWorkspaceDir(info.git_workspace_dir)
    }).catch((e) => console.warn('系統資訊載入失敗，改用預設值', e))
  }, [])

  // Restore selected directories from rebuild config
  useEffect(() => {
    if (isRebuild && rebuildState?.config) {
      const extraDirs = rebuildState.config.extra_dirs
      if (extraDirs) {
        setSelectedDirs(extraDirs.split(',').map((d) => d.trim()).filter(Boolean))
      }
      const dataDirs = rebuildState.config.data_dirs
      if (dataDirs) {
        setSelectedDataDirs(dataDirs.split(',').map((d) => d.trim()).filter(Boolean))
      }
    }
  }, [isRebuild, rebuildState])

  // Auto-sync docker_base_image when python_version changes.
  // "auto" is NOT a Docker tag — `python:auto-slim` doesn't exist and would make
  // `docker build` fail on pull. A Nuitka standalone binary bundles its own
  // Python runtime, so the base image only supplies system libs; when the user
  // picks auto-detect we can't know the real X.Y here (the backend resolves it),
  // so fall back to a valid default tag instead of interpolating "auto".
  useEffect(() => {
    if (!pythonVersion) return
    const current = form.getFieldValue('docker_base_image') as string || ''
    if (!current || /^python:[\d.]+-slim$/.test(current)) {
      const tag = pythonVersion === 'auto' ? '3.13' : pythonVersion
      form.setFieldValue('docker_base_image', `python:${tag}-slim`)
    }
  }, [pythonVersion, form])

  const getFrontendFullPath = useCallback(() => {
    const path = form.getFieldValue('project_path')
    const frontendDir = form.getFieldValue('frontend_dir') || '.'
    if (!path) return null
    return frontendDir === '.' ? path : `${path}/${frontendDir}`
  }, [form])

  const fetchEnvFiles = useCallback(async () => {
    const fullPath = getFrontendFullPath()
    if (!fullPath) return
    setLoadingEnvFiles(true)
    try {
      const result = await taskApi.listEnvFiles(fullPath)
      setEnvFiles(result.files)
    } catch {
      setEnvFiles([])
    } finally {
      setLoadingEnvFiles(false)
    }
  }, [getFrontendFullPath])

  // Local mode: read PEP 735 dependency-groups from the project's pyproject.toml.
  // A clear-on-fetch keeps a stale selection from a previous path from leaking in.
  const fetchPyprojectGroups = useCallback(async (path: string) => {
    if (!path || !path.trim()) {
      setDepGroups([])
      return
    }
    setLoadingDepGroups(true)
    try {
      const result = await taskApi.listPyprojectGroups(path.trim())
      setDepGroups(result.groups)
      // Drop a selected group that no longer exists in the new project.
      const current = form.getFieldValue('dependency_group') as string | undefined
      if (current && !result.groups.includes(current)) {
        form.setFieldValue('dependency_group', undefined)
      }
    } catch {
      setDepGroups([])
    } finally {
      setLoadingDepGroups(false)
    }
  }, [form])

  const handleLoadEnv = async () => {
    const envFilename = form.getFieldValue('frontend_env_filename') || '.env'

    // In git mode, env file contents were already fetched in
    // handlePreviewGitFrontend — look them up from the cache instead of
    // hitting a (nonexistent) local filesystem path.
    if (form.getFieldValue('source_type') === 'git') {
      const cached = gitEnvFilesContent[envFilename]
      if (cached !== undefined) {
        form.setFieldValue('frontend_env_content', cached)
        message.success(`已從 Git repo 載入 ${envFilename}`)
      } else {
        message.info(`Git repo 中找不到 ${envFilename}`)
      }
      return
    }

    // Local mode: read from filesystem via the existing API.
    const fullPath = getFrontendFullPath()
    if (!fullPath) {
      message.warning('請先輸入專案路徑')
      return
    }
    setLoadingEnv(true)
    try {
      const result = await taskApi.readEnvFile(fullPath, envFilename)
      form.setFieldValue('frontend_env_content', result.content)
      message.success(`已從專案載入 ${envFilename}`)
    } catch (err: unknown) {
      const error = err as { response?: { status?: number; data?: { detail?: string } } }
      if (error.response?.status === 404) {
        message.info(`${fullPath} 內找不到 ${envFilename}`)
      } else {
        message.error(error.response?.data?.detail || '載入 env 檔失敗')
      }
    } finally {
      setLoadingEnv(false)
    }
  }

  const detectFrontendConfig = useCallback(async (silent = false) => {
    const path = form.getFieldValue('project_path')
    const frontendDir = form.getFieldValue('frontend_dir') || '.'
    if (!path) {
      if (!silent) message.warning('請先輸入專案路徑')
      return
    }
    const fullPath = frontendDir === '.' ? path : `${path}/${frontendDir}`
    setDetectingConfig(true)
    try {
      const config = await taskApi.detectFrontendConfig(fullPath)
      if (!config.detected) {
        if (!silent) message.info('這個目錄內沒有偵測到前端設定')
        return
      }
      const updates: Record<string, string> = {}
      if (config.build_tool) updates.frontend_build_tool = config.build_tool
      if (config.build_command) updates.frontend_build_command = config.build_command
      if (config.output_dir) updates.frontend_output_dir = config.output_dir
      if (Object.keys(updates).length > 0) form.setFieldsValue(updates)
      if (!silent) {
        const parts: string[] = []
        if (config.has_vite_config) parts.push('Vite 設定')
        if (config.build_tool) parts.push(`工具：${config.build_tool}`)
        if (config.build_command) parts.push(`指令：${config.build_command}`)
        if (config.output_dir) parts.push(`輸出：${config.output_dir}`)
        message.success(`偵測結果：${parts.join('、')}`)
      }
    } catch (err: unknown) {
      if (!silent) {
        const error = err as { response?: { status?: number; data?: { detail?: string } } }
        if (error.response?.status === 404) {
          message.info('找不到前端目錄')
        } else {
          message.error(error.response?.data?.detail || '偵測前端設定失敗')
        }
      }
    } finally {
      setDetectingConfig(false)
    }
  }, [form])

  const createMutation = useMutation({
    mutationFn: taskApi.create,
    onSuccess: (task) => {
      message.success('任務建立成功！')
      // Draft has been consumed by a successful submit — clear it so the
      // next visit starts clean.
      clearDraftStorage()
      queryClient.invalidateQueries({ queryKey: ['activeTasks'] })
      window.history.replaceState({}, '')
      navigate(`/task/${task.id}`)
    },
    onError: (error: Error) => {
      notification.error({
        message: '建立任務失敗',
        description: error.message || '請檢查設定後再試一次',
        duration: 6,
      })
    },
  })

  const fetchDirectories = useCallback(async (path: string) => {
    if (!path || path.trim() === '') {
      setDirectories([])
      setDirError(null)
      setAnalysis(null)
      return
    }
    setLoadingDirs(true)
    setDirError(null)
    try {
      const result = await taskApi.listDirectories(path.trim())
      setDirectories(result.directories)
      setCurrentPath(path.trim())
      setSelectedDirs((prev) => prev.filter((d) => result.directories.includes(d)))
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } }
      setDirError(error.response?.data?.detail || '載入目錄清單失敗')
      setDirectories([])
    } finally {
      setLoadingDirs(false)
    }
    // Best-effort project analysis — runs after the dir list is settled and
    // never blocks it. On any error we simply clear the analysis so the UI
    // falls back to unlabelled checkboxes.
    try {
      const entry = (form.getFieldValue('entry_point') as string) || 'main.py'
      const result = await taskApi.analyzeProject(path.trim(), entry)
      setAnalysis(result)
    } catch {
      setAnalysis(null)
    }
  }, [form])

  const projectPath = Form.useWatch('project_path', form)

  useEffect(() => {
    // Local-mode only: auto-scan the server path
    if (sourceType === 'git') return
    const timer = setTimeout(() => {
      if (projectPath && projectPath !== currentPath) {
        fetchDirectories(projectPath)
        const pt = form.getFieldValue('project_type') as ProjectType
        if (pt === 'backend_only' || pt === 'fullstack') {
          fetchPyprojectGroups(projectPath)
        }
        if (pt === 'frontend_only' || pt === 'fullstack') {
          detectFrontendConfig(true)
          fetchEnvFiles()
        }
      }
    }, 500)
    return () => clearTimeout(timer)
  }, [projectPath, currentPath, fetchDirectories, detectFrontendConfig, fetchEnvFiles, fetchPyprojectGroups, form, sourceType])

  // Git mode: debounce-fetch branches/tags when the git_url changes.
  const fetchGitRefs = useCallback(
    async (url: string) => {
      if (!url || !url.trim()) return
      setLoadingRefs(true)
      try {
        const refs = await taskApi.listGitRefs(url.trim())
        setGitRefs(refs)
        setRefsFetchedFor(url.trim())
        // Auto-select a sensible default ref
        // main → master → first, and never over an explicit choice: refs are
        // refetched when the URL is edited, and clobbering the selection would
        // silently build something other than what is shown on screen.
        const preferred = pickDefaultRef(refs, form.getFieldValue('git_ref') as string)
        if (preferred) {
          form.setFieldValue('git_ref', preferred)
          form.setFieldValue('git_ref_type', 'branch')
        }
      } catch (err: unknown) {
        const e = err as { response?: { status?: number; data?: { detail?: string } } }
        setGitRefs({ branches: [], tags: [] })
        notification.error({
          message: '取得分支 / 標籤失敗',
          description:
            e.response?.data?.detail
            ?? '請確認 URL 是否正確，以及伺服器端的 GITLAB_TOKEN 是否已設定。',
          btn: (
            <Button size="small" type="primary" onClick={() => { void fetchGitRefs(url) }}>
              重試
            </Button>
          ),
          duration: 0,
        })
      } finally {
        setLoadingRefs(false)
      }
    },
    [form],
  )

  useEffect(() => {
    if (sourceType !== 'git') return
    const url = (gitUrl || '').trim()
    if (!url || url === refsFetchedFor) return
    const timer = setTimeout(() => { fetchGitRefs(url) }, 500)
    return () => clearTimeout(timer)
  }, [gitUrl, sourceType, refsFetchedFor, fetchGitRefs])

  // Git mode only: scan the remote repo tree to populate the Extra /
  // Data directory pickers. Shares the `directories` state with local
  // mode so the existing checkbox grid just works.
  //
  // Called automatically from a debounced effect when (url, ref, refType)
  // stabilise — see below. Success feedback is handled by the inline status
  // indicator, not by a toast, to keep the UI quiet on every ref change.
  const handleScanGitTree = useCallback(async () => {
    const url = (form.getFieldValue('git_url') as string || '').trim()
    const ref = (form.getFieldValue('git_ref') as string || '').trim()
    const refType = (form.getFieldValue('git_ref_type') as 'branch' | 'tag') || 'branch'
    if (!url || !ref) return
    setScanningTree(true)
    setDirError(null)
    try {
      const result = await taskApi.scanGitTree(url, ref, refType)
      setDirectories(result.directories)
      setAnalysis(result.analysis ?? null)
      setCurrentPath(`git:${url}@${ref}`)
      setTreeScannedFor(`${url}|${refType}|${ref}`)
      // Trim any stale selections that are no longer present in the scan
      setSelectedDirs((prev) => prev.filter((d) => result.directories.includes(d)))
      setSelectedDataDirs((prev) => prev.filter((d) => result.directories.includes(d)))
      // pyproject dependency-groups parsed from the remote repo (git-mode parity)
      setDepGroups(result.dependency_groups)
      const currentGroup = form.getFieldValue('dependency_group') as string | undefined
      if (currentGroup && !result.dependency_groups.includes(currentGroup)) {
        form.setFieldValue('dependency_group', undefined)
      }
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      notification.error({
        message: '掃描 Repo 結構失敗',
        description:
          e.response?.data?.detail
          ?? '請確認 Git URL 與分支 / 標籤是否正確，以及伺服器是否有權限存取此 repo。',
        btn: (
          <Button size="small" type="primary" onClick={() => { void handleScanGitTree() }}>
            重試
          </Button>
        ),
        duration: 0,
      })
    } finally {
      setScanningTree(false)
    }
  }, [form])

  // Auto-scan when the user has filled in URL + ref, debounced so that
  // rapid branch switching doesn't fire multiple requests. Only runs when
  // the project actually has a backend (extra_dirs/data_dirs are backend-only).
  useEffect(() => {
    if (sourceType !== 'git') return
    if (!hasBackend) return
    const url = (gitUrl || '').trim()
    const ref = (gitRef || '').trim()
    const refType = gitRefType || 'branch'
    if (!url || !ref) return
    const key = `${url}|${refType}|${ref}`
    if (key === treeScannedFor) return
    const timer = setTimeout(() => { void handleScanGitTree() }, 800)
    return () => clearTimeout(timer)
  }, [sourceType, hasBackend, gitUrl, gitRef, gitRefType, treeScannedFor, handleScanGitTree])

  // Git mode only: shallow-clone the repo and populate frontend config +
  // env file contents. Runs in parallel with scan_tree above, so fullstack
  // projects trigger both and backend-only projects trigger only scan_tree.
  const handlePreviewGitFrontend = useCallback(async () => {
    const url = (form.getFieldValue('git_url') as string || '').trim()
    const ref = (form.getFieldValue('git_ref') as string || '').trim()
    const refType = (form.getFieldValue('git_ref_type') as 'branch' | 'tag') || 'branch'
    const feDir = ((form.getFieldValue('frontend_dir') as string) || '.').trim()
    if (!url || !ref) return
    setPreviewingFrontend(true)
    try {
      const result = await taskApi.previewGitFrontend(url, ref, refType, feDir)
      // Auto-populate frontend fields from the detected config. Mirrors
      // the local-mode `detectFrontendConfig` auto-apply behaviour.
      const cfg = result.frontend_config
      if (cfg.detected) {
        const updates: Record<string, string> = {}
        if (cfg.build_tool) updates.frontend_build_tool = cfg.build_tool
        if (cfg.build_command) updates.frontend_build_command = cfg.build_command
        if (cfg.output_dir) updates.frontend_output_dir = cfg.output_dir
        if (Object.keys(updates).length > 0) form.setFieldsValue(updates)
      }
      // Env files list + content cache
      const fileNames = Object.keys(result.env_files).sort()
      setEnvFiles(fileNames)
      setGitEnvFilesContent(result.env_files)
      setFrontendPreviewedFor(`${url}|${refType}|${ref}|${feDir}`)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      notification.error({
        message: '偵測前端設定失敗',
        description:
          e.response?.data?.detail
          ?? '請確認 Git URL、分支 / 標籤、以及前端目錄是否正確。',
        btn: (
          <Button size="small" type="primary" onClick={() => { void handlePreviewGitFrontend() }}>
            重試
          </Button>
        ),
        duration: 0,
      })
    } finally {
      setPreviewingFrontend(false)
    }
  }, [form])

  useEffect(() => {
    if (sourceType !== 'git') return
    if (!showFrontendSettings) return
    const url = (gitUrl || '').trim()
    const ref = (gitRef || '').trim()
    if (!url || !ref) return
    const refType = gitRefType || 'branch'
    const feDir = (frontendDirWatch || '.').trim()
    const key = `${url}|${refType}|${ref}|${feDir}`
    if (key === frontendPreviewedFor) return
    const timer = setTimeout(() => { void handlePreviewGitFrontend() }, 800)
    return () => clearTimeout(timer)
  }, [
    sourceType,
    showFrontendSettings,
    gitUrl,
    gitRef,
    gitRefType,
    frontendDirWatch,
    frontendPreviewedFor,
    handlePreviewGitFrontend,
  ])

  // First-visit Tour — skipped if the user has already seen it.
  useEffect(() => {
    if (isRebuild) return // don't interrupt users returning to rebuild
    if (typeof window === 'undefined') return
    if (!localStorage.getItem(TOUR_STORAGE_KEY)) {
      // Delay slightly so form has rendered and refs can be measured
      const timer = setTimeout(() => setTourOpen(true), 400)
      return () => clearTimeout(timer)
    }
  }, [isRebuild])

  // Reset directory-related state when the user flips between source
  // modes mid-form, so stale selections / listings from the previous
  // mode don't leak into the new one. The ref guard skips the very
  // first observation (which is the form's initial value, not a user
  // action) and the rebuild effect above populates its own state.
  useEffect(() => {
    if (sourceType === undefined) return
    if (prevSourceTypeRef.current === undefined) {
      prevSourceTypeRef.current = sourceType
      return
    }
    if (prevSourceTypeRef.current === sourceType) return
    prevSourceTypeRef.current = sourceType

    // Real user-initiated switch — clear anything that was tied to the
    // previous mode's repo / path.
    setDirectories([])
    setSelectedDirs([])
    setSelectedDataDirs([])
    setCurrentPath('')
    setDirError(null)
    setAnalysis(null)
    analysisPrefilledRef.current = false
    setGitRefs({ branches: [], tags: [] })
    setRefsFetchedFor('')
    setTreeScannedFor('')
    setFrontendPreviewedFor('')
    setGitEnvFilesContent({})
    setEnvFiles([])
    setDepGroups([])
    form.setFieldValue('dependency_group', undefined)
  }, [sourceType, form])

  // Pre-check the recommended directories on the FIRST analysis only, for a
  // fresh new task (never rebuild — that carries its own selections) and only
  // when the user hasn't manually chosen anything yet. The ref guard means a
  // later re-scan will never overwrite the user's manual edits.
  useEffect(() => {
    if (isRebuild) return
    if (!analysis) return
    if (analysisPrefilledRef.current) return
    if (directories.length === 0) return
    analysisPrefilledRef.current = true
    if (selectedDirs.length === 0) {
      const extra = analysis.suggested_extra_dirs.filter((d) => directories.includes(d))
      if (extra.length > 0) setSelectedDirs(extra)
    }
    if (selectedDataDirs.length === 0) {
      const data = analysis.suggested_data_dirs.filter((d) => directories.includes(d))
      if (data.length > 0) setSelectedDataDirs(data)
    }
  }, [analysis, isRebuild, directories, selectedDirs.length, selectedDataDirs.length])

  // Render the auto-analysis suggestion tags for a directory. Graceful: shows
  // nothing when there's no classification entry for the directory.
  const renderDirTags = useCallback((dir: string) => {
    const info = analysis?.directories.find((d) => d.name === dir)
    if (!info) return null
    const roleTag =
      info.role === 'source' ? (
        <Tag color="cyan" style={{ marginInlineEnd: 4 }}>程式碼</Tag>
      ) : info.role === 'data' ? (
        <Tag color="green" style={{ marginInlineEnd: 4 }}>資料</Tag>
      ) : (
        <Tag style={{ marginInlineEnd: 4 }}>略過</Tag>
      )
    return (
      <span className="ml-1 inline-flex items-center align-middle">
        <Tooltip title={info.reason}>{roleTag}</Tooltip>
        {info.imported_by_entry && (
          <Tooltip title={info.reason}>
            <Tag color="gold" style={{ marginInlineEnd: 0 }}>主程式有用到</Tag>
          </Tooltip>
        )}
      </span>
    )
  }, [analysis])

  const closeTour = useCallback(() => {
    setTourOpen(false)
    try { localStorage.setItem(TOUR_STORAGE_KEY, '1') } catch { /* ignore */ }
  }, [])

  const handleManualSubmit = () => {
    // Assembly lives in ./createTask/buildConfig with per-field tests: every
    // field falls back to a default, so a mistake here does not raise — it
    // silently builds with settings the user never chose.
    createMutation.mutate(
      assembleTaskCreate({
        values: form.getFieldsValue(true),
        selectedDirs,
        selectedDataDirs,
      }),
    )
  }

  const handleDirToggle = (dir: string, checked: boolean) => {
    if (checked) {
      setSelectedDirs((prev) => [...prev, dir])
    } else {
      setSelectedDirs((prev) => prev.filter((d) => d !== dir))
    }
  }

  const handleSelectAll = () => {
    if (selectedDirs.length === directories.length) {
      setSelectedDirs([])
    } else {
      setSelectedDirs([...directories])
    }
  }

  // ── Step navigation (key-based, dynamic per project type) ──
  // Step definitions and the project-type filter live in ./createTask/steps
  // with tests: a wrongly hidden step takes its required fields with it and
  // the build silently runs on defaults nobody chose.
  const activeStepList = useMemo(() => activeSteps(projectType), [projectType])

  const stepItems = useMemo(
    () => activeStepList.map((s) => ({ title: s.title })),
    [activeStepList],
  )

  const currentStepKey: StepKey = activeStepList[currentStep]?.key ?? 'basic'
  const currentStepDef: StepDef = activeStepList[currentStep] ?? ALL_STEPS[0]
  const isLastStep = currentStep === activeStepList.length - 1

  // Clamp currentStep when project type changes mid-flow (e.g. switch from
  // fullstack → backend_only after passing the frontend step).
  useEffect(() => {
    const clamped = clampStepIndex(currentStep, activeStepList)
    if (clamped !== currentStep) setCurrentStep(clamped)
  }, [activeStepList, currentStep])

  const validateAndNext = async () => {
    try {
      switch (currentStepKey) {
        case 'basic': {
          const common = ['project_name', 'project_type', 'source_type']
          const currentSource = (form.getFieldValue('source_type') || 'local') as SourceType
          const sourceSpecific =
            currentSource === 'git' ? ['git_url', 'git_ref'] : ['project_path']
          await form.validateFields([...common, ...sourceSpecific])
          break
        }
        case 'backend':
          await form.validateFields(['python_version', 'entry_point'])
          break
        // frontend / docker / review: no required fields here
      }
      if (currentStep < activeStepList.length - 1) {
        setCurrentStep((s) => s + 1)
      }
    } catch {
      // Validation failed — form will show error messages
    }
  }

  const goBack = () => setCurrentStep((s) => Math.max(0, s - 1))

  // Rebuild advanced panel keys
  const rebuildAdvancedKeys: string[] = []
  if (isRebuild && rebuildState?.config) {
    const c = rebuildState.config
    if (c.nuitka_jobs || c.include_packages || c.extra_dirs || c.data_dirs) {
      rebuildAdvancedKeys.push('backend-advanced')
    }
    if (c.docker_env_vars || c.docker_custom_commands || c.docker_install_node) {
      rebuildAdvancedKeys.push('docker-advanced')
    }
  }

  // ── Shared styles ──
  const cardStyle = { background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(100,100,100,0.3)' }
  const cardHeaderStyle = { borderBottom: '1px solid rgba(100,100,100,0.3)' }
  const dividerStyle = { borderColor: 'rgba(100,100,100,0.3)', margin: '12px 0' }

  // ── Summary row for confirmation page ──
  const SummaryRow = ({ label, value, mono, highlight }: { label: string; value: React.ReactNode; mono?: boolean; highlight?: boolean }) => (
    <div className="flex py-1.5" style={{ borderBottom: '1px solid rgba(100,100,100,0.15)' }}>
      <span className="text-gray-500 text-sm w-32 flex-shrink-0">{label}</span>
      <span className={`text-sm ${highlight ? 'text-matrix-400 font-medium' : 'text-gray-300'} ${mono ? 'font-mono' : ''}`}>
        {value || '—'}
      </span>
    </div>
  )

  // ── Project type display helpers ──
  const typeLabels: Record<string, { icon: React.ReactNode; text: string }> = {
    backend_only: { icon: <CodeOutlined />, text: '後端' },
    frontend_only: { icon: <GlobalOutlined />, text: '前端' },
    fullstack: { icon: <AppstoreOutlined />, text: '全端' },
  }

  return (
    <div className="max-w-4xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-white" style={{ fontFamily: 'var(--font-display)' }}>
          {isRebuild ? '重新打包任務' : '建立新任務'}
        </h1>
        <p className="text-gray-400 mt-1">
          {isRebuild ? `正在重新打包「${rebuildState?.projectName}」` : '設定並啟動新的打包任務'}
        </p>
        {!isRebuild && draftSavedAt && (
          <div className="mt-2 flex items-center gap-3 text-xs text-gray-500">
            <span>已自動保存草稿(離開後再回來會自動還原)</span>
            <button
              type="button"
              onClick={clearDraft}
              className="text-cyber-400 hover:text-cyber-300"
            >
              清除草稿
            </button>
          </div>
        )}
      </div>

      {/* Steps indicator + form mode toggle */}
      <div className="mb-6 flex items-center justify-between gap-4 flex-wrap">
        <div className="flex-1 min-w-[280px]">
          <Steps current={currentStep} items={stepItems} size="small" />
        </div>
        <Tooltip title="簡易:只顯示核心欄位,進階選項預設收合。進階:一次展開全部設定。隨時可切換,也可手動點開個別區塊。">
          <Segmented
            value={simpleMode ? 'simple' : 'advanced'}
            onChange={(v) => setSimpleMode(v === 'simple')}
            options={[
              { label: '簡易', value: 'simple' },
              { label: '進階', value: 'advanced' },
            ]}
          />
        </Tooltip>
      </div>

      {/* Per-step hint banner — keeps user oriented at all times */}
      <div
        className="mb-4 p-3 rounded-lg flex items-start gap-3"
        style={{
          background: 'rgba(34, 211, 238, 0.05)',
          border: '1px solid rgba(34, 211, 238, 0.25)',
        }}
      >
        <SettingOutlined style={{ color: '#f09a5e', fontSize: 16, marginTop: 2 }} />
        <div className="flex-1">
          <div className="text-cyber-300 text-sm font-medium">
            步驟 {currentStep + 1} / {activeStepList.length}:{currentStepDef.title}
          </div>
          <div className="text-gray-400 text-xs mt-0.5" style={{ lineHeight: 1.6 }}>
            {currentStepDef.description}
          </div>
        </div>
      </div>

      <div className="glass-card p-6">
        <Form
          form={form}
          layout="vertical"
          onValuesChange={scheduleDraftSave}
          onFinish={() => { /* disabled — use manual submit only */ }}
          initialValues={isRebuild && rebuildState ? {
            project_name: rebuildState.projectName,
            ...rebuildState.config,
            // Fall back for historical tasks that predate git support.
            // Spread comes first so these only fill when the config lacks them.
            source_type: rebuildState.config.source_type ?? 'local',
            git_url: rebuildState.config.git_url ?? '',
            git_ref: rebuildState.config.git_ref ?? '',
            git_ref_type: rebuildState.config.git_ref_type ?? 'branch',
            // Picker is single-select; collapse the historical array to its
            // first entry. The config still round-trips as a list on submit.
            dependency_group: rebuildState.config.dependency_groups?.[0],
            // Fall back for tasks that predate the Nuitka optimization fields.
            enable_anti_bloat: rebuildState.config.enable_anti_bloat ?? true,
            include_package_data: rebuildState.config.include_package_data ?? '',
            generate_report: rebuildState.config.generate_report ?? false,
            verify_after_build: rebuildState.config.verify_after_build ?? true,
            nginx_client_max_body_size: rebuildState.config.nginx_client_max_body_size ?? '0',
            nginx_proxy_read_timeout: rebuildState.config.nginx_proxy_read_timeout ?? '300s',
            nginx_streaming: rebuildState.config.nginx_streaming ?? true,
            nginx_gzip: rebuildState.config.nginx_gzip ?? true,
          } : {
            project_type: 'backend_only',
            source_type: 'local',
            git_url: '',
            git_ref: '',
            git_ref_type: 'branch',
            python_version: 'auto',
            entry_point: 'main.py',
            output_dir: '',
            onefile: true,
            pack_mode: 'external',
            include_packages: '',
            dependency_group: undefined,
            frontend_dir: '.',
            frontend_build_tool: 'npm',
            frontend_build_command: 'build',
            frontend_output_dir: 'dist',
            frontend_env_filename: '.env',
            frontend_env_content: '',
            docker_base_image: 'python:3.13-slim',
            docker_expose_port: 8000,
            docker_custom_commands: '',
            docker_enabled: false,
            docker_install_node: false,
            docker_api_proxy: '',
            nginx_client_max_body_size: '0',
            nginx_proxy_read_timeout: '300s',
            nginx_streaming: true,
            nginx_gzip: true,
            nuitka_jobs: 0,
            enable_anti_bloat: true,
            include_package_data: '',
            generate_report: false,
            verify_after_build: true,
          }}
        >

          {/* ════════════════════════════════════════════
              Step: Basic Info — project name, type, source
              ════════════════════════════════════════════ */}
          <div style={{ display: currentStepKey === 'basic' ? 'block' : 'none' }}>
            <h3 className="text-lg font-medium text-white mb-4">你要打包什麼專案?</h3>

            <Form.Item
              name="project_name"
              label={<span className="text-gray-300">專案名稱</span>}
              rules={[{ required: true, message: '必填欄位' }]}
            >
              <Input placeholder="我的專案" className="input-field" size="large" />
            </Form.Item>

            <Form.Item
              name="project_type"
              label={<span className="text-gray-300">專案類型</span>}
              rules={[{ required: true }]}
            >
              <Select size="large">
                <Option value="backend_only">
                  <CodeOutlined className="mr-2" />後端 (Python)
                </Option>
                <Option value="frontend_only">
                  <GlobalOutlined className="mr-2" />前端 (React / Vue 等)
                </Option>
                <Option value="fullstack">
                  <AppstoreOutlined className="mr-2" />全端 (前後端整合)
                </Option>
              </Select>
            </Form.Item>

            {/* ── Source mode switcher ── */}
            <div ref={sourceModeRef}>
              <Form.Item
                name="source_type"
                label={<span className="text-gray-300">來源方式</span>}
                tooltip="選擇「本機路徑」直接指定伺服器上已存在的專案目錄，或選「Git URL」由系統自動 clone 指定的 branch/tag"
                rules={[{ required: true }]}
              >
                <Radio.Group size="large" buttonStyle="solid">
                  <Radio.Button value="local">
                    <FolderOutlined /> 本機路徑
                  </Radio.Button>
                  <Radio.Button value="git">
                    <LinkOutlined /> Git URL
                  </Radio.Button>
                </Radio.Group>
              </Form.Item>
            </div>

            {/* ── Local path mode ── */}
            {sourceType !== 'git' && (
              <Form.Item
                name="project_path"
                label={<span className="text-gray-300">專案路徑</span>}
                tooltip="伺服器上的絕對路徑，需位於 /media/disk0/ 或 /media/disk1/ 之下"
                validateTrigger={['onBlur', 'onChange']}
                rules={[
                  { required: true, message: '請輸入專案路徑' },
                  {
                    pattern: /^\/media\/disk[01]\//,
                    message: '路徑必須以 /media/disk0/ 或 /media/disk1/ 開頭',
                  },
                ]}
              >
                <Input
                  placeholder="/media/disk0/Tony/my-project"
                  className="input-field"
                  size="large"
                />
              </Form.Item>
            )}

            {/* ── Git URL mode ── */}
            {sourceType === 'git' && (
              <Card
                size="small"
                title={
                  <span className="text-cyber-400">
                    <LinkOutlined className="mr-2" />
                    Git 來源
                  </span>
                }
                className="mb-4"
                style={cardStyle}
                styles={{ header: cardHeaderStyle }}
              >
                <div ref={gitUrlRef}>
                  <Form.Item
                    name="git_url"
                    label={<span className="text-gray-300">Git URL</span>}
                    tooltip="Token 由伺服器端從 .env 注入，請不要將帳號密碼寫在網址裡"
                    validateTrigger={['onBlur', 'onChange']}
                    rules={[
                      { required: true, message: '請輸入 Git URL' },
                      {
                        pattern: /^https?:\/\//,
                        message: '僅支援 http 或 https 協定',
                      },
                    ]}
                  >
                    <Input
                      placeholder="https://gitlab.example.com/group/project.git"
                      className="input-field"
                      size="large"
                      suffix={loadingRefs ? <Spin size="small" /> : null}
                    />
                  </Form.Item>
                </div>

                <Row gutter={16}>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="git_ref_type"
                      label={<span className="text-gray-300">類型</span>}
                    >
                      <Radio.Group
                        onChange={() => {
                          // Reset selection so the user consciously re-picks
                          form.setFieldValue('git_ref', '')
                        }}
                      >
                        <Radio.Button value="branch">
                          <BranchesOutlined /> 分支
                        </Radio.Button>
                        <Radio.Button value="tag">
                          <TagOutlined /> 標籤
                        </Radio.Button>
                      </Radio.Group>
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={16}>
                    <div ref={refSelectRef}>
                      <Form.Item
                        name="git_ref"
                        label={
                          <span className="text-gray-300">
                            {gitRefType === 'tag' ? '標籤' : '分支'}
                          </span>
                        }
                        tooltip={
                          gitRefType === 'tag'
                            ? '標籤代表固定的 commit。即使未來重新打包仍會抓到相同內容'
                            : '分支會隨時更新。每次 rebuild 會抓取該 branch 當下的最新 commit'
                        }
                        rules={[{ required: true, message: '請選擇或輸入一個分支 / 標籤' }]}
                      >
                        {/* AutoComplete = single-select combobox: pick from
                            the fetched list OR type a custom ref freely.
                            Previously a <Select mode="tags"> which gave the
                            wrong multi-select feel. */}
                        <AutoComplete
                          className="input-field"
                          size="large"
                          allowClear
                          placeholder={
                            gitRefs.branches.length === 0 && gitRefs.tags.length === 0
                              ? '輸入 Git URL 後會自動列出可用項目，也可手動輸入'
                              : gitRefType === 'tag'
                              ? '選擇標籤（或手動輸入）'
                              : '選擇分支（或手動輸入）'
                          }
                          options={(gitRefType === 'tag' ? gitRefs.tags : gitRefs.branches).map(
                            (name) => ({ label: name, value: name }),
                          )}
                          filterOption={(input, option) =>
                            (option?.value as string)?.toLowerCase().includes(input.toLowerCase())
                          }
                          notFoundContent={loadingRefs ? <Spin size="small" /> : null}
                        />
                      </Form.Item>
                    </div>
                  </Col>
                </Row>

                {/* Auto-scanned repo tree status (for extra_dirs / data_dirs) */}
                {hasBackend && (
                  <div className="mb-3 text-xs" style={{ lineHeight: 1.6 }}>
                    {scanningTree ? (
                      <span className="text-gray-400">
                        <Spin size="small" />
                        <span className="ml-2">正在偵測 Repo 目錄結構...</span>
                      </span>
                    ) : currentPath.startsWith('git:') && directories.length > 0 ? (
                      <span className="text-matrix-400">
                        <ScanOutlined className="mr-1" />
                        已自動掃描到 {directories.length} 個目錄，請到下方「後端設定 → 進階設定」勾選
                      </span>
                    ) : currentPath.startsWith('git:') && directories.length === 0 ? (
                      <span className="text-gray-400">
                        <ScanOutlined className="mr-1" />
                        Repo 根目錄內沒有可選的子目錄
                      </span>
                    ) : (
                      <span className="text-gray-500">
                        <ScanOutlined className="mr-1" />
                        選好分支 / 標籤後會自動偵測「要打包的 source code」與「資料目錄」的可選項目,並讀取 pyproject 依賴群組
                      </span>
                    )}
                  </div>
                )}

                {/* Git mode hint */}
                <div className="text-gray-500 text-xs" style={{ lineHeight: 1.6 }}>
                  <ScanOutlined className="mr-1" />
                  伺服器會在任務啟動後 clone 至 <code className="text-gray-400">{workspaceDir}/&lt;task_id&gt;</code>
                  ，完成後可於任務詳情頁查看「Repo 結構診斷」
                  {hasBackend && (
                    <>
                      <br />
                      <ScanOutlined className="mr-1" />
                      偵測到 <code className="text-gray-400">pyproject.toml</code> / <code className="text-gray-400">uv.lock</code> / <code className="text-gray-400">requirements.txt</code> 時會自動執行 <code className="text-gray-400">uv sync --frozen</code> 建立 <code className="text-gray-400">.venv</code>
                    </>
                  )}
                </div>
              </Card>
            )}
          </div>
          {/* ════ end Step: basic ════ */}

          {/* ════════════════════════════════════════════
              Step: Backend — Nuitka settings (only when hasBackend)
              ════════════════════════════════════════════ */}
          <div style={{ display: currentStepKey === 'backend' ? 'block' : 'none' }}>
            {hasBackend && (
              <Card
                size="small"
                title={<span className="text-cyber-400"><CodeOutlined className="mr-2" />後端設定</span>}
                className="mb-4"
                style={cardStyle}
                styles={{ header: cardHeaderStyle }}
              >
                {/* Version mismatch is caught here rather than during the
                    build. The same ABI detection already ran in preflight, but
                    only after the user had filled in every step, queued, and
                    waited — one project submitted a version its own .so files
                    could not load 37 times that way. */}
                {pythonMismatch && (
                  <div
                    className="glass-card p-4 mb-4"
                    style={{
                      borderLeft: '3px solid #fbbf24',
                      background: 'rgba(251, 191, 36, 0.06)',
                    }}
                  >
                    <div className="flex items-start gap-2">
                      <WarningOutlined style={{ color: '#fbbf24', marginTop: 3 }} />
                      <div className="flex-1">
                        <div className="text-sm text-amber-200">
                          這個專案的套件是用 <b>Python {pythonMismatch.detected}</b> 編譯的，
                          但你選了 <b>Python {pythonMismatch.chosen}</b>。
                        </div>
                        <div className="text-xs text-gray-400 mt-1">
                          照這樣打包，執行檔會在啟動時出現 ModuleNotFoundError —— 它載入不了專案自己的
                          .so 檔。這是目前最常見的失敗原因。
                        </div>
                        <div className="text-xs text-gray-600 mt-1 font-mono break-all">
                          {pythonMismatch.detail}
                        </div>
                        <Button
                          size="small"
                          className="mt-2"
                          onClick={() => form.setFieldValue('python_version', 'auto')}
                        >
                          改用自動偵測
                        </Button>
                      </div>
                    </div>
                  </div>
                )}
                {analysis?.detected_python?.inconsistent && (
                  <div
                    className="glass-card p-4 mb-4"
                    style={{
                      borderLeft: '3px solid #f87171',
                      background: 'rgba(239, 68, 68, 0.06)',
                    }}
                  >
                    <div className="text-sm text-alert-400">
                      這個專案的 .venv 內含多個不同 Python 版本的套件
                    </div>
                    <div className="text-xs text-gray-400 mt-1">
                      無論選哪個版本都會有一部分套件載入失敗。請在專案目錄重建 .venv
                      （刪掉 .venv 後重新 uv sync 或 pip install），再回來打包。
                    </div>
                    <div className="text-xs text-gray-600 mt-1 font-mono break-all">
                      {analysis.detected_python.detail}
                    </div>
                  </div>
                )}

                <Row gutter={16}>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="python_version"
                      label={<span className="text-gray-300">Python 版本</span>}
                      rules={[{ required: true }]}
                      tooltip="「自動偵測」會讀取專案 .venv 內已編譯套件(.so)的真實 Python 版本來決定編譯器版本 — 這是最可靠的方式,避免版本錯配。除非你很確定,否則建議用自動。"
                      extra={<span className="text-xs text-gray-500">自動 = 依專案 .venv 的實際 .so 版本編譯(建議);手動指定會在版本不符時警告</span>}
                    >
                      <Select>
                        <Option value="auto">自動偵測(建議)</Option>
                        {pythonVersions.map((v) => (
                          <Option key={v} value={v}>Python {v}</Option>
                        ))}
                      </Select>
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="entry_point"
                      label={<span className="text-gray-300">進入點</span>}
                      tooltip="要編譯的主 Python 檔案，相對於專案路徑"
                      validateTrigger={['onBlur', 'onChange']}
                      rules={[{ required: true }]}
                    >
                      <Input placeholder="main.py" className="input-field" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="output_name"
                      label={<span className="text-gray-300">二進位執行檔</span>}
                      tooltip="編譯後產生的二進位執行檔名稱。留空則從進入點自動推導"
                    >
                      <Input
                        placeholder={entryPoint ? entryPoint.replace(/\.py$/, '') : 'main'}
                        className="input-field"
                      />
                    </Form.Item>
                  </Col>
                </Row>

                <Row gutter={16}>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="pack_mode"
                      label={<span className="text-gray-300">Nuitka 打包模式</span>}
                      tooltip="External（建議）：第三方套件另外放在 libs/ 目錄,編譯較快；Full：所有依賴打包進單一執行檔,但 Nuitka 編譯時間非常長,8hr 起跳"
                      extra={<span className="text-xs text-gray-500">不確定就用 External(快又穩)。Full 會全部包成一顆,但編譯極久</span>}
                    >
                      <Select>
                        <Option value="external">External（建議,libs/）</Option>
                        <Option value="full">Full（打包時間非常長,8hr 起跳）</Option>
                      </Select>
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="onefile"
                      label={<span className="text-gray-300">單檔執行檔</span>}
                      valuePropName="checked"
                    >
                      <Switch checkedChildren="是" unCheckedChildren="否" />
                    </Form.Item>
                  </Col>
                </Row>

                {/* ── Dependency Group (PEP 735) — single-select from pyproject ── */}
                <Form.Item
                  name="dependency_group"
                  label={
                    <span className="text-gray-300">
                      依賴群組（uv <code className="text-cyber-400">--group</code>）
                    </span>
                  }
                  tooltip={
                    <div>
                      <div>選項自動讀取自 pyproject.toml 的 <code>[dependency-groups]</code> 段</div>
                      <div className="mt-2">執行時會變成 <code>uv sync --group &lt;name&gt;</code></div>
                      <div className="mt-2">典型用法:選不同平台的 PyTorch wheel(rocm / cuda / cpu)</div>
                      <div className="mt-2 text-gray-400">只能單選;留空 = 不帶 --group flag(預設行為)</div>
                    </div>
                  }
                  extra={
                    <span className="text-xs text-gray-500">
                      {depGroups.length > 0
                        ? `已從 pyproject.toml 讀取到 ${depGroups.length} 個群組,只能擇一`
                        : sourceType === 'git'
                        ? '選好分支 / 標籤後會自動讀取此 repo 的 pyproject.toml 群組'
                        : '輸入專案路徑後會自動讀取 pyproject.toml 內定義的群組'}
                    </span>
                  }
                >
                  <Select
                    allowClear
                    placeholder={
                      depGroups.length > 0
                        ? '選擇一個依賴群組'
                        : '此專案的 pyproject.toml 沒有定義 [dependency-groups]'
                    }
                    style={{ width: '100%' }}
                    loading={loadingDepGroups}
                    disabled={depGroups.length === 0}
                    notFoundContent={loadingDepGroups ? <Spin size="small" /> : null}
                    options={depGroups.map((g) => ({ label: g, value: g }))}
                  />
                </Form.Item>

                {/* Advanced Backend Settings — default open on dedicated Backend step */}
                <Collapse
                  ghost
                  activeKey={backendAdvKeys}
                  onChange={(keys) => setBackendAdvKeys(keys as string[])}
                  items={[{
                    key: 'backend-advanced',
                    forceRender: true,
                    label: (
                      <span className="text-gray-300 text-sm">
                        <SettingOutlined className="mr-2" />進階設定:CPU 並行、強制納入套件、額外目錄
                      </span>
                    ),
                    children: (
                      <>
                        <Row gutter={16}>
                          <Col xs={24} md={8}>
                            <Form.Item
                              name="nuitka_jobs"
                              label={<span className="text-gray-300">CPU 並行數</span>}
                              tooltip="C 編譯的並行 job 數。越多越快，但記憶體用量也會增加"
                            >
                              <Select>
                                <Option value={0}>自動</Option>
                                {cpuCount > 0 && (
                                  <Option value={cpuCount}>全部核心 ({cpuCount})</Option>
                                )}
                                {[1, 2, 4, 8, 16, 32, 64, 128]
                                  .filter((n) => n < cpuCount)
                                  .map((n) => (
                                    <Option key={n} value={n}>{n} 核</Option>
                                  ))}
                              </Select>
                            </Form.Item>
                          </Col>
                          <Col xs={24} md={16}>
                            <Form.Item
                              name="include_packages"
                              label={<span className="text-gray-300">強制納入套件</span>}
                              tooltip="以逗號分隔；用於 Nuitka 偵測不到的動態 / 延遲載入套件"
                            >
                              <Input placeholder="例如：litellm, openai" className="input-field" />
                            </Form.Item>
                          </Col>
                        </Row>

                        <Row gutter={16}>
                          <Col xs={24} md={12}>
                            <Form.Item
                              name="output_dir"
                              label={<span className="text-gray-300">輸出目錄</span>}
                              tooltip="留空則使用預設 'dist'"
                            >
                              <Input placeholder="dist（預設）" className="input-field" />
                            </Form.Item>
                          </Col>
                        </Row>

                        <Divider style={dividerStyle} />

                        {/* ── Nuitka compile optimization & diagnostics ── */}
                        <div className="mb-1 text-gray-300 text-sm">
                          <SettingOutlined className="mr-2" />編譯最佳化與診斷
                        </div>
                        <div className="text-xs text-gray-500 mb-3" style={{ lineHeight: 1.6 }}>
                          這三個都可以不用動,維持預設即可。想縮小體積、或遇到「打包後執行時找不到檔案」時再來調。
                        </div>
                        <Row gutter={16}>
                          <Col xs={24} md={8}>
                            <Form.Item
                              name="enable_anti_bloat"
                              label={<span className="text-gray-300">縮小體積</span>}
                              valuePropName="checked"
                              tooltip="開啟 Nuitka 的 anti-bloat 外掛,自動移除套件夾帶的測試碼 / 文件 / 開發用 import。主要對 Full 模式有感,External 模式影響很小。"
                              extra={<span className="text-xs text-gray-500">移除沒用到的測試 / 文件,讓檔案更小(預設開)</span>}
                            >
                              <Switch checkedChildren="開" unCheckedChildren="關" />
                            </Form.Item>
                          </Col>
                          <Col xs={24} md={8}>
                            <Form.Item
                              name="generate_report"
                              label={<span className="text-gray-300">產生編譯報告</span>}
                              valuePropName="checked"
                              tooltip="輸出 compilation-report.xml(放在輸出目錄,會一起包進下載檔)。列出每個模組有沒有被包進去,debug「執行時少東西」很有用。"
                              extra={<span className="text-xs text-gray-500">遇到缺套件時,用它查誰被漏掉</span>}
                            >
                              <Switch checkedChildren="開" unCheckedChildren="關" />
                            </Form.Item>
                          </Col>
                          <Col xs={24} md={8}>
                            <Form.Item
                              name="verify_after_build"
                              label={<span className="text-gray-300">打包後驗證</span>}
                              valuePropName="checked"
                              tooltip="編譯完成後自動啟動一次 binary,確認不會一開機就因為缺套件掛掉。偵測到 ModuleNotFoundError 會把任務標記為失敗並貼出缺的模組。"
                              extra={<span className="text-xs text-gray-500">編完跑跑看,確認真的能啟動(預設開,強烈建議)</span>}
                            >
                              <Switch checkedChildren="開" unCheckedChildren="關" />
                            </Form.Item>
                          </Col>
                        </Row>
                        <Form.Item
                          name="include_package_data"
                          label={<span className="text-gray-300">強制納入套件「資料檔」</span>}
                          tooltip="以逗號分隔的套件名稱。把套件內的非 .py 資料檔(json / 憑證 / 模板)一起打包。常見的(certifi、litellm 等)系統會自動偵測,這裡只填額外的。"
                          extra={<span className="text-xs text-gray-500">執行時噴 FileNotFoundError、且缺的是某套件內建資料檔時才需要</span>}
                        >
                          <Input placeholder="例如:certifi, litellm" className="input-field" />
                        </Form.Item>

                        <Divider style={dividerStyle} />

                        {/* Extra Source Directories */}
                        <div className="mt-2">
                          <div className="flex items-center justify-between mb-3">
                            <label className="text-gray-300 text-sm">
                              <FolderOutlined className="mr-2" />要一起打包的 source code(Python 程式碼)
                            </label>
                            {directories.length > 0 && (
                              <div className="flex items-center gap-2">
                                <button
                                  type="button"
                                  onClick={handleSelectAll}
                                  className="text-xs text-cyber-400 hover:text-cyber-300"
                                >
                                  {selectedDirs.length === directories.length ? '取消全選' : '全選'}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => fetchDirectories(projectPath || '')}
                                  className="text-gray-400 hover:text-cyber-400 p-1"
                                  title="重新整理"
                                >
                                  <ReloadOutlined />
                                </button>
                              </div>
                            )}
                          </div>
                          <div className="text-xs text-gray-500 mb-2" style={{ lineHeight: 1.6 }}>
                            這裡選的目錄會被當成 Python 原始碼一起編進執行檔(import 得到)。主程式以外的套件 / 模組目錄選這裡。
                          </div>
                          <div className="rounded-lg border border-gray-700 bg-void-800/50 p-4 min-h-[60px]">
                            {loadingDirs ? (
                              <div className="flex items-center justify-center py-2">
                                <Spin size="small" />
                                <span className="ml-2 text-gray-400 text-sm">載入中...</span>
                              </div>
                            ) : dirError ? (
                              <div className="text-alert-400 text-sm py-2">{dirError}</div>
                            ) : directories.length === 0 ? (
                              <div className="text-gray-500 text-sm py-2">
                                {projectPath ? '找不到任何目錄' : '請先輸入專案路徑'}
                              </div>
                            ) : (
                              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                                {directories.map((dir) => (
                                  <Checkbox
                                    key={dir}
                                    checked={selectedDirs.includes(dir)}
                                    onChange={(e) => handleDirToggle(dir, e.target.checked)}
                                    className="text-gray-300"
                                  >
                                    <span className="text-sm">{dir}</span>
                                    {renderDirTags(dir)}
                                  </Checkbox>
                                ))}
                              </div>
                            )}
                          </div>
                          {selectedDirs.length > 0 && (
                            <div className="mt-2 text-xs text-gray-400">
                              已選擇：{selectedDirs.join(', ')}
                            </div>
                          )}
                        </div>

                        {/* Data Directories */}
                        <div className="mt-4">
                          <div className="flex items-center justify-between mb-3">
                            <label className="text-gray-300 text-sm">
                              <FolderOutlined className="mr-2" />資料目錄(會原樣複製到執行檔旁邊的資料夾)
                            </label>
                          </div>
                          <div className="text-xs text-gray-500 mb-2" style={{ lineHeight: 1.6 }}>
                            這裡選的目錄不會編進執行檔,而是打包後原封不動複製到執行檔旁邊。適合靜態檔、樣板、設定檔等。
                          </div>
                          <div className="rounded-lg border border-gray-700 bg-void-800/50 p-4 min-h-[60px]">
                            {loadingDirs ? (
                              <div className="flex items-center justify-center py-2">
                                <Spin size="small" />
                              </div>
                            ) : directories.length === 0 ? (
                              <div className="text-gray-500 text-sm py-2">請先輸入專案路徑</div>
                            ) : (
                              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                                {directories.map((dir) => (
                                  <Checkbox
                                    key={`data-${dir}`}
                                    checked={selectedDataDirs.includes(dir)}
                                    onChange={(e) => {
                                      if (e.target.checked) {
                                        setSelectedDataDirs((prev) => [...prev, dir])
                                      } else {
                                        setSelectedDataDirs((prev) => prev.filter((d) => d !== dir))
                                      }
                                    }}
                                    className="text-gray-300"
                                  >
                                    <span className="text-sm">{dir}</span>
                                    {renderDirTags(dir)}
                                  </Checkbox>
                                ))}
                              </div>
                            )}
                          </div>
                          {selectedDataDirs.length > 0 && (
                            <div className="mt-2 text-xs text-gray-400">
                              已選擇：{selectedDataDirs.join(', ')}
                            </div>
                          )}
                        </div>
                      </>
                    ),
                  }]}
                />
              </Card>
            )}
          </div>
          {/* ════ end Step: backend ════ */}

          {/* ════════════════════════════════════════════
              Step: Frontend (only when showFrontendSettings)
              ════════════════════════════════════════════ */}
          <div style={{ display: currentStepKey === 'frontend' ? 'block' : 'none' }}>
            {showFrontendSettings && (
              <Card
                size="small"
                title={
                  <div className="flex items-center justify-between w-full">
                    <span className="text-matrix-400"><GlobalOutlined className="mr-2" />前端設定</span>
                    <Button
                      size="small"
                      icon={<ScanOutlined />}
                      loading={sourceType === 'git' ? previewingFrontend : detectingConfig}
                      onClick={() => {
                        if (sourceType === 'git') { void handlePreviewGitFrontend() }
                        else { void detectFrontendConfig(false) }
                      }}
                    >
                      自動偵測
                    </Button>
                  </div>
                }
                className="mb-4"
                style={cardStyle}
                styles={{ header: cardHeaderStyle }}
              >
                <Row gutter={16}>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="frontend_dir"
                      label={<span className="text-gray-300">前端目錄</span>}
                      tooltip="「.」代表專案根目錄；若前端在子目錄則填「frontend」等"
                    >
                      <Input placeholder=". (根目錄)" className="input-field" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="frontend_build_tool"
                      label={<span className="text-gray-300">建置工具</span>}
                    >
                      <Select>
                        <Option value="npm">npm</Option>
                        <Option value="yarn">yarn</Option>
                        <Option value="pnpm">pnpm</Option>
                        <Option value="bun">bun</Option>
                      </Select>
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="frontend_build_command"
                      label={<span className="text-gray-300">建置指令</span>}
                      tooltip="例如：build、build:prod"
                    >
                      <Input placeholder="build" className="input-field" />
                    </Form.Item>
                  </Col>
                </Row>
                <Row gutter={16}>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="frontend_output_dir"
                      label={<span className="text-gray-300">輸出目錄</span>}
                      tooltip="前端建置產出目錄，例如 dist"
                    >
                      <Input placeholder="dist" className="input-field" />
                    </Form.Item>
                  </Col>
                </Row>

                <Divider style={dividerStyle} />
                <div className="flex items-center justify-between mb-2">
                  <span className="text-gray-300 text-sm">前端建置環境變數 (.env)</span>
                  <div className="flex gap-2">
                    <Button
                      size="small"
                      icon={<ScanOutlined />}
                      loading={sourceType === 'git' ? previewingFrontend : loadingEnvFiles}
                      onClick={() => {
                        if (sourceType === 'git') { void handlePreviewGitFrontend() }
                        else { void fetchEnvFiles() }
                      }}
                    >
                      掃描
                    </Button>
                    <Button
                      size="small"
                      icon={<DownloadOutlined />}
                      loading={loadingEnv}
                      onClick={handleLoadEnv}
                    >
                      {sourceType === 'git' ? '從 Git 載入' : '從專案載入'}
                    </Button>
                  </div>
                </div>
                <Row gutter={16}>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="frontend_env_filename"
                      label={<span className="text-gray-300">Env 檔名</span>}
                      tooltip="例如 .env.production（建置時會優先於 .env）"
                      validateTrigger={['onBlur', 'onChange']}
                      rules={[{ pattern: /^\.env(\.\w+)*$/, message: '必須是 .env 或 .env.*' }]}
                    >
                      <AutoComplete
                        placeholder=".env"
                        className="input-field"
                        options={envFiles.map((f) => ({ value: f, label: f }))}
                        filterOption={(input, option) =>
                          (option?.value as string)?.toLowerCase().includes(input.toLowerCase())
                        }
                      />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item
                  name="frontend_env_content"
                  label={<span className="text-gray-300">Env 內容</span>}
                  tooltip="建置前會寫入前端目錄。每行一組 KEY=VALUE。"
                >
                  <Input.TextArea
                    rows={4}
                    placeholder={"VITE_API_BASE_URL=https://api.example.com\nVITE_APP_TITLE=My App"}
                    className="input-field"
                    style={{ fontFamily: 'monospace' }}
                  />
                </Form.Item>
              </Card>
            )}
          </div>
          {/* ════ end Step: frontend ════ */}

          {/* ════════════════════════════════════════════
              Step: Docker — output decision + image settings
              ════════════════════════════════════════════ */}
          <div style={{ display: currentStepKey === 'docker' ? 'block' : 'none' }}>
            <h3 className="text-lg font-medium text-white mb-2">要打包成 Docker image 嗎?</h3>
            <p className="text-gray-400 text-sm mb-6">
              {hasBackend
                ? '後端會先用 Nuitka 在主機上編譯，再打包進精簡的 Docker image。'
                : '前端會建置後放進 Docker container，以 Nginx 提供服務。'}
            </p>

            <div
              className="flex items-center gap-6 p-6 rounded-xl"
              style={{ background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(100,100,100,0.3)' }}
            >
              <CloudServerOutlined style={{ fontSize: 32, color: dockerEnabled ? '#a855f7' : '#6b7280' }} />
              <div className="flex-1">
                <div className="text-white font-medium">Docker Image 輸出</div>
                <div className="text-gray-400 text-sm mt-1">
                  {dockerEnabled
                    ? '打包結果會輸出為 Docker image（.tar.gz）'
                    : '只會輸出本機檔案，不會包成 Docker image'}
                </div>
              </div>
              <Form.Item name="docker_enabled" valuePropName="checked" style={{ marginBottom: 0 }}>
                <Switch
                  checkedChildren="是"
                  unCheckedChildren="否"
                  style={{ transform: 'scale(1.3)' }}
                />
              </Form.Item>
            </div>

            {/* Docker Settings — shown when enabled */}
            {dockerEnabled && (
              <Card
                size="small"
                title={<span className="text-purple-400"><CloudServerOutlined className="mr-2" />Docker 設定</span>}
                className="mt-6"
                style={cardStyle}
                styles={{ header: cardHeaderStyle }}
              >
                <Row gutter={16}>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="docker_image_name"
                      label={<span className="text-gray-300">Image 名稱</span>}
                      tooltip="例如：myapp:latest"
                    >
                      <Input placeholder="myapp:latest" className="input-field" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="docker_base_image"
                      label={<span className="text-gray-300">基底 Image</span>}
                    >
                      <Input placeholder="python:3.13-slim" className="input-field" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="docker_expose_port"
                      label={<span className="text-gray-300">對外連接埠</span>}
                    >
                      <InputNumber min={1} max={65535} style={{ width: '100%' }} placeholder="8000" />
                    </Form.Item>
                  </Col>
                </Row>

                {projectType === 'frontend_only' && (
                  <>
                    <Row gutter={16}>
                      <Col xs={24} md={16}>
                        <Form.Item
                          name="docker_api_proxy"
                          label={<span className="text-gray-300">API Proxy 網址</span>}
                          tooltip="Nginx 會把 /api 反向代理至這個網址"
                        >
                          <Input placeholder="http://192.168.1.10:5011" className="input-field" />
                        </Form.Item>
                      </Col>
                    </Row>

                    <div className="mb-1 text-gray-300 text-sm">
                      <SettingOutlined className="mr-2" />nginx 設定
                    </div>
                    <div className="text-xs text-gray-500 mb-3" style={{ lineHeight: 1.6 }}>
                      純前端的 Docker 是用 nginx 提供服務,以下是常被調整的幾個旋鈕,維持預設即可。
                    </div>

                    {/* The timeout is the setting people get wrong, and the
                        reason is that its name suggests a request duration cap
                        when it actually measures upstream silence. A rule of
                        thumb here is cheaper than another 504 investigation. */}
                    <Collapse
                      ghost
                      size="small"
                      className="mb-3"
                      items={[
                        {
                          key: 'timeout-help',
                          label: (
                            <span className="text-xs text-cyber-300">
                              我需要調整逾時嗎?(點開看判斷方式)
                            </span>
                          ),
                          children: (
                            <div className="text-xs text-gray-400" style={{ lineHeight: 1.8 }}>
                              <div className="mb-2">
                                逾時算的是「<b className="text-gray-300">後端最長一段沉默有多久</b>」,
                                不是請求總共跑多久。每收到一點資料就會重新計時。
                              </div>
                              <table className="w-full" style={{ borderCollapse: 'collapse' }}>
                                <tbody>
                                  {[
                                    ['SSE / 逐字串流的 LLM', '不用調 — 每個 token 都會重置計時器'],
                                    ['語音辨識、一次性回傳的 LLM', '只有當最壞情況可能超過預設值才要調大'],
                                    ['WebSocket 有心跳', '不用調 — 心跳會重置計時器'],
                                    ['WebSocket 沒有心跳', '要調 — 必須大於最長的靜默期'],
                                    ['大檔上傳', '不用調 — 串流模式已讓資料持續流動'],
                                  ].map(([k, v]) => (
                                    <tr key={k}>
                                      <td
                                        className="text-gray-300 pr-3 align-top"
                                        style={{ borderTop: '1px solid rgba(255,255,255,0.06)', padding: '4px 8px 4px 0', whiteSpace: 'nowrap' }}
                                      >
                                        {k}
                                      </td>
                                      <td style={{ borderTop: '1px solid rgba(255,255,255,0.06)', padding: '4px 0' }}>
                                        {v}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                              <div className="mt-2 text-amber-300">
                                ⚠ 如果容器外面還有一層公司的 nginx,那一層有它自己的逾時設定。
                                只改這裡是沒有用的 —— 鏈上任何一層都會生效,以最小的為準。
                                若重建後仍在約 60 秒斷掉,就是外層那道。
                              </div>
                              <div className="mt-1 text-gray-500">
                                最穩的做法是讓後端在等待期間定期送出資料(SSE 每 10 秒送一行
                                「: keepalive」),這樣每一層代理都會同時被滿足。
                              </div>
                            </div>
                          ),
                        },
                      ]}
                    />
                    <Row gutter={16}>
                      <Col xs={24} md={8}>
                        <Form.Item
                          name="nginx_client_max_body_size"
                          label={<span className="text-gray-300">上傳大小上限</span>}
                          tooltip="nginx client_max_body_size。0 = 不限制。例:50m、1g、0"
                          extra={<span className="text-xs text-gray-500">上傳檔案出現 413 時調這個。0 = 不限</span>}
                          validateTrigger={['onBlur', 'onChange']}
                          rules={[{ pattern: /^\d+[kKmMgG]?$/, message: '格式例:50m / 1g / 0' }]}
                        >
                          <Input placeholder="0(不限)" className="input-field" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={8}>
                        <Form.Item
                          name="nginx_proxy_read_timeout"
                          label={<span className="text-gray-300">後端最久可以「不出聲」多久</span>}
                          tooltip="nginx proxy_read_timeout / proxy_send_timeout。這是閒置逾時,不是請求總長度上限"
                          extra={
                            <span className="text-xs text-gray-500">
                              這是「後端多久沒回傳任何資料」就放棄,不是請求總時間上限。
                              語音辨識、一次性回傳的 LLM 這類「算很久才吐結果」的 API 要調大,
                              否則會收到 504。
                            </span>
                          }
                          validateTrigger={['onBlur', 'onChange']}
                          rules={[{ pattern: /^\d+[smhdSMHD]?$/, message: '格式例:300s / 10m' }]}
                        >
                          <Input placeholder="300s" className="input-field" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={8}>
                        <Form.Item
                          name="nginx_streaming"
                          label={<span className="text-gray-300">串流模式</span>}
                          valuePropName="checked"
                          tooltip="關閉 nginx 的請求/回應緩衝(proxy_buffering off)"
                          extra={
                            <span className="text-xs text-gray-500">
                              LLM 逐字輸出(SSE)、長音檔上傳請保持開啟。
                              關閉的話 nginx 會等後端整包回完才送出,串流效果會消失。
                            </span>
                          }
                        >
                          <Switch checkedChildren="開" unCheckedChildren="關" />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={16}>
                      <Col xs={24} md={8}>
                        <Form.Item
                          name="nginx_gzip"
                          label={<span className="text-gray-300">gzip 壓縮</span>}
                          valuePropName="checked"
                          tooltip="對 CSS/JS/JSON/SVG 開啟 gzip,減少傳輸量"
                          extra={<span className="text-xs text-gray-500">靜態資源壓縮(建議開)</span>}
                        >
                          <Switch checkedChildren="開" unCheckedChildren="關" />
                        </Form.Item>
                      </Col>
                    </Row>
                  </>
                )}

                <Collapse
                  ghost
                  activeKey={dockerAdvKeys}
                  onChange={(keys) => setDockerAdvKeys(keys as string[])}
                  items={[{
                    key: 'docker-advanced',
                    forceRender: true,
                    label: (
                      <span className="text-gray-300 text-sm">
                        <SettingOutlined className="mr-2" />進階設定:Node.js、自訂 RUN 指令、環境變數
                      </span>
                    ),
                    children: (
                      <>
                        <Row gutter={16}>
                          <Col xs={24} md={8}>
                            <Form.Item
                              name="docker_install_node"
                              label={<span className="text-gray-300">安裝 Node.js</span>}
                              valuePropName="checked"
                              tooltip="在 Docker image 內安裝 Node.js 20.x"
                            >
                              <Switch checkedChildren="是" unCheckedChildren="否" />
                            </Form.Item>
                          </Col>
                        </Row>
                        <Form.Item
                          name="docker_env_vars"
                          label={<span className="text-gray-300">執行期 Docker ENV</span>}
                          tooltip="每行一組 KEY=VALUE。以 # 開頭的行會被略過。"
                        >
                          <Input.TextArea
                            rows={3}
                            placeholder={"GOOGLE_API_KEY=xxxx\nMY_SECRET=yyyy"}
                            className="input-field"
                            style={{ fontFamily: 'monospace' }}
                          />
                        </Form.Item>
                        <Form.Item
                          name="docker_custom_commands"
                          label={<span className="text-gray-300">自訂指令</span>}
                          tooltip="每行一個指令。只允許 apt-get、pip、npm、mkdir、chmod 等。"
                        >
                          <Input.TextArea
                            rows={3}
                            placeholder={"apt-get update && apt-get install -y --no-install-recommends curl"}
                            className="input-field"
                            style={{ fontFamily: 'monospace' }}
                          />
                        </Form.Item>
                      </>
                    ),
                  }]}
                />
              </Card>
            )}
          </div>

          {/* ════════════════════════════════════════════
              Step: Review — confirmation summary
              ════════════════════════════════════════════ */}
          <div style={{ display: currentStepKey === 'review' ? 'block' : 'none' }}>
            <h3 className="text-lg font-medium text-white mb-4">確認打包設定</h3>
            <p className="text-gray-400 text-sm mb-6">請在啟動打包前確認以下所有設定。</p>

            {/* Project Info Summary */}
            <Card
              size="small"
              title={<span className="text-cyber-400"><AppstoreOutlined className="mr-2" />專案</span>}
              className="mb-4"
              style={cardStyle}
              styles={{ header: cardHeaderStyle }}
            >
              <SummaryRow label="專案名稱" value={form.getFieldValue('project_name')} />
              <SummaryRow label="專案類型" value={projectType && typeLabels[projectType] ? typeLabels[projectType].text : projectType} />
              {sourceType === 'git' ? (
                <>
                  <SummaryRow label="來源" value="Git URL" highlight />
                  <SummaryRow label="Git 網址" value={form.getFieldValue('git_url')} mono />
                  <SummaryRow
                    label={form.getFieldValue('git_ref_type') === 'tag' ? '標籤' : '分支'}
                    value={form.getFieldValue('git_ref')}
                    mono
                  />
                </>
              ) : (
                <>
                  <SummaryRow label="來源" value="本機路徑" />
                  <SummaryRow label="專案路徑" value={form.getFieldValue('project_path')} mono />
                </>
              )}
              {/* output_dir is a Nuitka/backend concept and only editable in
                  the backend step; frontend-only builds use frontend_output_dir
                  instead, so keep this row out of their summary. */}
              {hasBackend && (
                <SummaryRow label="輸出目錄" value={form.getFieldValue('output_dir') || 'dist'} mono />
              )}
            </Card>

            {/* Backend Summary */}
            {hasBackend && (
              <Card
                size="small"
                title={<span className="text-cyber-400"><CodeOutlined className="mr-2" />後端 (Nuitka)</span>}
                className="mb-4"
                style={cardStyle}
                styles={{ header: cardHeaderStyle }}
              >
                <SummaryRow label="Python 版本" value={form.getFieldValue('python_version')} />
                <SummaryRow label="進入點" value={form.getFieldValue('entry_point')} mono />
                <SummaryRow label="二進位執行檔" value={form.getFieldValue('output_name') || (form.getFieldValue('entry_point') || 'main').replace(/\.py$/, '')} mono />
                <SummaryRow label="Nuitka 模式" value={form.getFieldValue('pack_mode') === 'external' ? 'External (libs/,建議)' : 'Full (打包時間非常長,8hr 起跳)'} />
                <SummaryRow label="單檔執行檔" value={form.getFieldValue('onefile') ? '是' : '否'} />
                <SummaryRow label="縮小體積" value={form.getFieldValue('enable_anti_bloat') ? '開 (anti-bloat)' : '關'} />
                <SummaryRow label="打包後驗證" value={form.getFieldValue('verify_after_build') ? '開(編完自動跑跑看)' : '關'} highlight={form.getFieldValue('verify_after_build')} />
                {form.getFieldValue('generate_report') && (
                  <SummaryRow label="編譯報告" value="會產生 compilation-report.xml" />
                )}
                {form.getFieldValue('include_package_data') && (
                  <SummaryRow label="納入套件資料檔" value={form.getFieldValue('include_package_data')} mono />
                )}
                {(form.getFieldValue('nuitka_jobs') ?? 0) > 0 && (
                  <SummaryRow label="CPU 並行數" value={`${form.getFieldValue('nuitka_jobs')} 核`} />
                )}
                {form.getFieldValue('include_packages') && (
                  <SummaryRow label="強制納入套件" value={form.getFieldValue('include_packages')} mono />
                )}
                {(() => {
                  const group = form.getFieldValue('dependency_group') as string | undefined
                  return group ? (
                    <SummaryRow
                      label="依賴群組"
                      value={`--group ${group}`}
                      mono
                      highlight
                    />
                  ) : null
                })()}
                {selectedDirs.length > 0 && (
                  <SummaryRow label="打包的 source code" value={selectedDirs.join(', ')} mono />
                )}
                {selectedDataDirs.length > 0 && (
                  <SummaryRow label="資料目錄" value={selectedDataDirs.join(', ')} mono />
                )}
              </Card>
            )}

            {/* Frontend Summary */}
            {showFrontendSettings && (
              <Card
                size="small"
                title={<span className="text-matrix-400"><GlobalOutlined className="mr-2" />前端</span>}
                className="mb-4"
                style={cardStyle}
                styles={{ header: cardHeaderStyle }}
              >
                <SummaryRow label="前端目錄" value={form.getFieldValue('frontend_dir') || '.'} mono />
                <SummaryRow label="建置工具" value={form.getFieldValue('frontend_build_tool') || 'npm'} />
                <SummaryRow label="建置指令" value={`${form.getFieldValue('frontend_build_tool') || 'npm'} run ${form.getFieldValue('frontend_build_command') || 'build'}`} mono />
                <SummaryRow label="輸出目錄" value={form.getFieldValue('frontend_output_dir') || 'dist'} mono />
                {form.getFieldValue('frontend_env_content') && (
                  <SummaryRow label="Env 檔名" value={form.getFieldValue('frontend_env_filename') || '.env'} mono />
                )}
                {form.getFieldValue('frontend_env_content') && (
                  <div className="flex py-1.5">
                    <span className="text-gray-500 text-sm w-32 flex-shrink-0">Env 內容</span>
                    <pre className="text-gray-300 text-sm font-mono whitespace-pre-wrap m-0 flex-1 bg-black/20 rounded px-2 py-1" style={{ maxHeight: 120, overflow: 'auto' }}>
                      {form.getFieldValue('frontend_env_content')}
                    </pre>
                  </div>
                )}
              </Card>
            )}

            {/* Docker Summary */}
            <Card
              size="small"
              title={<span className="text-purple-400"><CloudServerOutlined className="mr-2" />Docker</span>}
              className="mb-4"
              style={cardStyle}
              styles={{ header: cardHeaderStyle }}
            >
              <SummaryRow label="啟用 Docker" value={dockerEnabled ? '是' : '否'} highlight={dockerEnabled} />
              {dockerEnabled && (
                <>
                  <SummaryRow label="Image 名稱" value={form.getFieldValue('docker_image_name') || '(自動)'} mono />
                  <SummaryRow label="基底 Image" value={form.getFieldValue('docker_base_image') || 'python:3.13-slim'} mono />
                  <SummaryRow label="對外連接埠" value={form.getFieldValue('docker_expose_port') || 8000} />
                  {projectType === 'frontend_only' && form.getFieldValue('docker_api_proxy') && (
                    <SummaryRow label="API Proxy" value={form.getFieldValue('docker_api_proxy')} mono />
                  )}
                  {projectType === 'frontend_only' && (
                    <>
                      <SummaryRow
                        label="上傳上限"
                        value={(form.getFieldValue('nginx_client_max_body_size') || '0') === '0'
                          ? '0(不限)'
                          : form.getFieldValue('nginx_client_max_body_size')}
                        mono
                      />
                      <SummaryRow label="Proxy 逾時" value={form.getFieldValue('nginx_proxy_read_timeout') || '300s'} mono />
                      <SummaryRow label="串流模式" value={form.getFieldValue('nginx_streaming') === false ? '關(nginx 會緩衝整包回應)' : '開'} />
                      <SummaryRow label="gzip 壓縮" value={form.getFieldValue('nginx_gzip') ? '開' : '關'} />
                    </>
                  )}
                  {form.getFieldValue('docker_install_node') && (
                    <SummaryRow label="安裝 Node.js" value="是" />
                  )}
                  {form.getFieldValue('docker_env_vars') && (
                    <div className="flex py-1.5">
                      <span className="text-gray-500 text-sm w-32 flex-shrink-0">ENV 變數</span>
                      <pre className="text-gray-300 text-sm font-mono whitespace-pre-wrap m-0 flex-1 bg-black/20 rounded px-2 py-1" style={{ maxHeight: 100, overflow: 'auto' }}>
                        {form.getFieldValue('docker_env_vars')}
                      </pre>
                    </div>
                  )}
                  {form.getFieldValue('docker_custom_commands') && (
                    <div className="flex py-1.5">
                      <span className="text-gray-500 text-sm w-32 flex-shrink-0">自訂指令</span>
                      <pre className="text-gray-300 text-sm font-mono whitespace-pre-wrap m-0 flex-1 bg-black/20 rounded px-2 py-1" style={{ maxHeight: 100, overflow: 'auto' }}>
                        {form.getFieldValue('docker_custom_commands')}
                      </pre>
                    </div>
                  )}
                </>
              )}
            </Card>
          </div>

          {/* ════════════════════════════════════════════
              Navigation Buttons
              ════════════════════════════════════════════ */}
          {/* Next-step preview helper text — keeps user oriented */}
          {!isLastStep && activeStepList[currentStep + 1] && (
            <div className="mt-6 text-xs text-gray-500 text-right">
              下一步:<span className="text-cyber-400">{activeStepList[currentStep + 1].title}</span>
              <span className="text-gray-600 ml-2">({activeStepList[currentStep + 1].description})</span>
            </div>
          )}
          <div className="flex gap-3 mt-3">
            {currentStep > 0 && (
              <Button
                htmlType="button"
                size="large"
                icon={<ArrowLeftOutlined />}
                onClick={goBack}
                style={{ height: 48 }}
              >
                上一步
              </Button>
            )}

            <div className="flex-1">
              {!isLastStep ? (
                <Button
                  type="primary"
                  htmlType="button"
                  size="large"
                  onClick={validateAndNext}
                  className="btn-cyber"
                  style={{ width: '100%', height: 48 }}
                >
                  下一步 <ArrowRightOutlined />
                </Button>
              ) : (
                <Button
                  type="primary"
                  htmlType="button"
                  loading={createMutation.isPending}
                  disabled={createMutation.isPending}
                  size="large"
                  icon={<RocketOutlined />}
                  className="btn-cyber"
                  style={{ width: '100%', height: 48 }}
                  onClick={handleManualSubmit}
                >
                  {isRebuild ? '開始重新打包' : '開始打包'}
                </Button>
              )}
            </div>
          </div>
        </Form>
      </div>

      {/* First-visit tour — in Traditional Chinese */}
      <Tour
        open={tourOpen}
        onClose={closeTour}
        steps={
          [
            {
              title: '選擇來源方式',
              description:
                '可以使用伺服器上既有的本機路徑，也可以直接貼上 GitLab 的 HTTP(S) 網址讓系統幫你 clone。',
              target: () => sourceModeRef.current,
            },
            {
              title: '輸入 Git URL',
              description:
                '選擇 Git URL 模式後，貼上 repo 的 HTTPS 網址，系統會自動取得可用的分支與標籤。Token 由伺服器端設定，請勿寫在網址裡。',
              target: () => gitUrlRef.current,
            },
            {
              title: '選擇分支或標籤',
              description:
                '分支會隨時間更新，標籤代表固定版本。也可以直接輸入清單裡沒有的名稱。',
              target: () => refSelectRef.current,
            },
          ] as TourProps['steps']
        }
      />
    </div>
  )
}
