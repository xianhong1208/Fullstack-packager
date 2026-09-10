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
  AutoComplete,
  Collapse,
  Radio,
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
  CheckOutlined,
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
  const [backendAdvKeys, setBackendAdvKeys] = useState<string[]>([])
  const [dockerAdvKeys, setDockerAdvKeys] = useState<string[]>([])

  // Rebuild state from History or TaskDetail navigation
  const rebuildState = location.state as RebuildState | null
  const isRebuild = rebuildState?.rebuild === true

  // Rebuild carries the original task's advanced values — open the relevant
  // advanced panels so they are visible rather than hidden in a collapsed section.
  useEffect(() => {
    if (!isRebuild || !rebuildState?.config) return
    const c = rebuildState.config
    if (c.nuitka_jobs || c.include_packages || c.extra_dirs || c.data_dirs) setBackendAdvKeys(['backend-advanced'])
    if (c.docker_env_vars || c.docker_custom_commands || c.docker_install_node) setDockerAdvKeys(['docker-advanced'])
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    message.success('Draft cleared')
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

  // Server-configured build workspace root (GIT_WORKSPACE_DIR); empty until fetched.
  const [workspaceDir, setWorkspaceDir] = useState<string>('')

  // Fetch CPU core count on mount
  useEffect(() => {
    taskApi.getSystemInfo().then((info) => {
      setCpuCount(info.cpu_count)
      if (info.python_versions?.length) setPythonVersions(info.python_versions)
      if (info.git_workspace_dir) setWorkspaceDir(info.git_workspace_dir)
    }).catch((e) => console.warn('Could not load system info; using defaults', e))
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
        message.success(`Loaded ${envFilename} from the Git repo`)
      } else {
        message.info(`${envFilename} not found in the Git repo`)
      }
      return
    }

    // Local mode: read from filesystem via the existing API.
    const fullPath = getFrontendFullPath()
    if (!fullPath) {
      message.warning('Enter the project path first')
      return
    }
    setLoadingEnv(true)
    try {
      const result = await taskApi.readEnvFile(fullPath, envFilename)
      form.setFieldValue('frontend_env_content', result.content)
      message.success(`Loaded ${envFilename} from the project`)
    } catch (err: unknown) {
      const error = err as { response?: { status?: number; data?: { detail?: string } } }
      if (error.response?.status === 404) {
        message.info(`${envFilename} not found in ${fullPath}`)
      } else {
        message.error(error.response?.data?.detail || 'Could not load the env file')
      }
    } finally {
      setLoadingEnv(false)
    }
  }

  const detectFrontendConfig = useCallback(async (silent = false) => {
    const path = form.getFieldValue('project_path')
    const frontendDir = form.getFieldValue('frontend_dir') || '.'
    if (!path) {
      if (!silent) message.warning('Enter the project path first')
      return
    }
    const fullPath = frontendDir === '.' ? path : `${path}/${frontendDir}`
    setDetectingConfig(true)
    try {
      const config = await taskApi.detectFrontendConfig(fullPath)
      if (!config.detected) {
        if (!silent) message.info('No frontend config detected in this directory')
        return
      }
      const updates: Record<string, string> = {}
      if (config.build_tool) updates.frontend_build_tool = config.build_tool
      if (config.build_command) updates.frontend_build_command = config.build_command
      if (config.output_dir) updates.frontend_output_dir = config.output_dir
      if (Object.keys(updates).length > 0) form.setFieldsValue(updates)
      if (!silent) {
        const parts: string[] = []
        if (config.has_vite_config) parts.push('Vite config')
        if (config.build_tool) parts.push(`tool: ${config.build_tool}`)
        if (config.build_command) parts.push(`command: ${config.build_command}`)
        if (config.output_dir) parts.push(`output: ${config.output_dir}`)
        message.success(`Detected ${parts.join(', ')}`)
      }
    } catch (err: unknown) {
      if (!silent) {
        const error = err as { response?: { status?: number; data?: { detail?: string } } }
        if (error.response?.status === 404) {
          message.info('Frontend directory not found')
        } else {
          message.error(error.response?.data?.detail || 'Could not detect the frontend config')
        }
      }
    } finally {
      setDetectingConfig(false)
    }
  }, [form])

  const createMutation = useMutation({
    mutationFn: taskApi.create,
    onSuccess: (task) => {
      message.success('Build started')
      // Draft has been consumed by a successful submit — clear it so the
      // next visit starts clean.
      clearDraftStorage()
      queryClient.invalidateQueries({ queryKey: ['activeTasks'] })
      window.history.replaceState({}, '')
      navigate(`/task/${task.id}`)
    },
    onError: (error: Error) => {
      notification.error({
        message: 'Could not start the build',
        description: error.message || 'Check your settings and try again',
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
      setDirError(error.response?.data?.detail || 'Could not load the directory list')
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
          message: 'Could not load branches or tags',
          description:
            e.response?.data?.detail
            ?? 'Check that the URL is correct and that GITLAB_TOKEN is set on the server.',
          btn: (
            <Button size="small" type="primary" onClick={() => { void fetchGitRefs(url) }}>
              Retry
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
        message: 'Could not scan the repo structure',
        description:
          e.response?.data?.detail
          ?? 'Check that the Git URL and branch or tag are correct, and that the server can access this repo.',
        btn: (
          <Button size="small" type="primary" onClick={() => { void handleScanGitTree() }}>
            Retry
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
        message: 'Could not detect the frontend config',
        description:
          e.response?.data?.detail
          ?? 'Check that the Git URL, branch or tag, and frontend directory are correct.',
        btn: (
          <Button size="small" type="primary" onClick={() => { void handlePreviewGitFrontend() }}>
            Retry
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
        <Tag color="cyan" style={{ marginInlineEnd: 4 }}>Code</Tag>
      ) : info.role === 'data' ? (
        <Tag color="green" style={{ marginInlineEnd: 4 }}>Data</Tag>
      ) : (
        <Tag style={{ marginInlineEnd: 4 }}>Skip</Tag>
      )
    return (
      <span className="ml-1 inline-flex items-center align-middle">
        <Tooltip title={info.reason}>{roleTag}</Tooltip>
        {info.imported_by_entry && (
          <Tooltip title={info.reason}>
            <Tag color="gold" style={{ marginInlineEnd: 0 }}>Used by entry point</Tag>
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

  // ── Section panel header (flat, hairline seam under the title) ──
  const SectionHeader = ({ icon, title, tone = 'cyber', action }: { icon: React.ReactNode; title: string; tone?: 'cyber' | 'matrix' | 'signal'; action?: React.ReactNode }) => (
    <div className="flex items-center justify-between mb-4 pb-3 border-b border-[var(--seam)]">
      <span className={`text-sm font-semibold flex items-center gap-2 text-${tone}-400`} style={{ fontFamily: 'var(--font-display)' }}>
        {icon}
        {title}
      </span>
      {action}
    </div>
  )

  // ── Summary row for confirmation page ──
  const SummaryRow = ({ label, value, mono, highlight }: { label: string; value: React.ReactNode; mono?: boolean; highlight?: boolean }) => (
    <div className="flex py-1.5 border-b border-[var(--seam)]">
      <span className="text-sm w-36 flex-shrink-0" style={{ color: 'var(--ink-faint)' }}>{label}</span>
      <span
        className={`text-sm ${highlight ? 'text-matrix-400 font-medium' : ''} ${mono ? 'font-mono' : ''}`}
        style={highlight ? undefined : { color: 'var(--ink)' }}
      >
        {value || '—'}
      </span>
    </div>
  )

  // ── Project type display helpers ──
  const typeLabels: Record<string, { icon: React.ReactNode; text: string }> = {
    backend_only: { icon: <CodeOutlined />, text: 'Backend' },
    frontend_only: { icon: <GlobalOutlined />, text: 'Frontend' },
    fullstack: { icon: <AppstoreOutlined />, text: 'Full stack' },
  }

  return (
    <div className="max-w-5xl mx-auto">
      {/* Page header — title + subtitle, form-mode toggle on the right, hairline seam under it */}
      <div className="pb-5 mb-6 border-b border-[var(--seam)] flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold" style={{ fontFamily: 'var(--font-display)', color: 'var(--ink)' }}>
            {isRebuild ? 'Rebuild task' : 'New build'}
          </h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--ink-muted)' }}>
            {isRebuild ? `Rebuilding "${rebuildState?.projectName}"` : 'Configure and start a new build.'}
          </p>
          {!isRebuild && draftSavedAt && (
            <div className="mt-2 flex items-center gap-3 text-xs" style={{ color: 'var(--ink-faint)' }}>
              <span>Draft saved automatically. It restores when you come back.</span>
              <button
                type="button"
                onClick={clearDraft}
                className="text-cyber-400 hover:text-cyber-300"
              >
                Clear draft
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Mobile step progress — slim indicator that replaces the rail below lg */}
      <div className="lg:hidden mb-5">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-semibold" style={{ fontFamily: 'var(--font-display)', color: 'var(--ink)' }}>
            {currentStepDef.title}
          </span>
          <span className="text-xs font-mono" style={{ color: 'var(--ink-faint)' }}>
            {currentStep + 1} / {activeStepList.length}
          </span>
        </div>
        <div
          className="flex gap-1.5"
          role="progressbar"
          aria-valuenow={currentStep + 1}
          aria-valuemin={1}
          aria-valuemax={activeStepList.length}
        >
          {activeStepList.map((s, i) => (
            <div
              key={s.key}
              className="h-1 flex-1 rounded-full transition-colors duration-150"
              style={{ background: i <= currentStep ? 'var(--color-cyber-500)' : 'var(--color-void-700)' }}
            />
          ))}
        </div>
      </div>

      {/* Two-column workspace: sticky step rail + focused content pane */}
      <div className="lg:grid lg:grid-cols-[232px_minmax(0,1fr)] lg:gap-8 lg:items-start">
        {/* ── Left: vertical step rail (sticky, desktop only) ── */}
        <aside className="hidden lg:block">
          <nav aria-label="Build steps" className="sticky top-6">
            <ol className="space-y-1">
              {activeStepList.map((s, i) => {
                const done = i < currentStep
                const active = i === currentStep
                const clickable = i <= currentStep
                return (
                  <li key={s.key}>
                    <button
                      type="button"
                      disabled={!clickable}
                      aria-current={active ? 'step' : undefined}
                      onClick={() => { if (clickable) setCurrentStep(i) }}
                      className={`w-full flex items-start gap-3 rounded-lg py-2.5 pl-3 pr-2 text-left border-l-2 transition-colors duration-150 focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyber-500 ${
                        active
                          ? 'bg-cyber-500/10 border-cyber-500'
                          : clickable
                          ? 'border-transparent cursor-pointer hover:bg-[rgba(231,236,242,0.04)]'
                          : 'border-transparent cursor-default'
                      }`}
                    >
                      <span
                        className="mt-0.5 flex-shrink-0 flex items-center justify-center rounded-full text-xs font-mono font-medium"
                        style={{
                          width: 24,
                          height: 24,
                          background: active
                            ? 'var(--color-cyber-500)'
                            : done
                            ? 'rgba(51, 196, 137, 0.14)'
                            : 'transparent',
                          border: active
                            ? '1px solid var(--color-cyber-400)'
                            : done
                            ? '1px solid rgba(51, 196, 137, 0.4)'
                            : '1px solid var(--color-void-600)',
                          color: active ? '#071018' : done ? 'var(--color-matrix-400)' : 'var(--ink-faint)',
                        }}
                      >
                        {done ? <CheckOutlined /> : i + 1}
                      </span>
                      <span className="min-w-0">
                        <span
                          className="block text-sm font-semibold leading-tight"
                          style={{
                            fontFamily: 'var(--font-display)',
                            color: active ? 'var(--ink)' : done ? 'var(--ink-muted)' : 'var(--ink-faint)',
                          }}
                        >
                          {s.title}
                        </span>
                        <span className="block text-xs mt-0.5 leading-snug" style={{ color: 'var(--ink-faint)' }}>
                          {s.short}
                        </span>
                      </span>
                    </button>
                  </li>
                )
              })}
            </ol>
          </nav>
        </aside>

        {/* ── Right: content pane ── */}
        <div className="min-w-0">
          {/* Focal step header — one dominant title + a muted description */}
          <div className="mb-6">
            <h2 className="text-2xl font-semibold" style={{ fontFamily: 'var(--font-display)', color: 'var(--ink)' }}>
              {currentStepDef.title}
            </h2>
            <p className="mt-1.5 text-sm" style={{ color: 'var(--ink-muted)', lineHeight: 1.65, maxWidth: '62ch' }}>
              {currentStepDef.description}
            </p>
          </div>

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
            <div className="glass-card p-6 mb-4">
            <h3 className="text-base font-semibold mb-4" style={{ color: 'var(--ink)' }}>What do you want to build?</h3>

            <Form.Item
              name="project_name"
              label={<span style={{ color: 'var(--ink-muted)' }}>Project name</span>}
              rules={[{ required: true, message: 'Give the project a name' }]}
            >
              <Input placeholder="my-project" className="input-field" size="large" />
            </Form.Item>

            <Form.Item
              name="project_type"
              label={<span style={{ color: 'var(--ink-muted)' }}>Project type</span>}
              rules={[{ required: true }]}
            >
              <Select size="large">
                <Option value="backend_only">
                  <CodeOutlined className="mr-2" />Backend (Python)
                </Option>
                <Option value="frontend_only">
                  <GlobalOutlined className="mr-2" />Frontend (React, Vue, and so on)
                </Option>
                <Option value="fullstack">
                  <AppstoreOutlined className="mr-2" />Full stack (frontend + backend)
                </Option>
              </Select>
            </Form.Item>

            {/* ── Source mode switcher ── */}
            <div ref={sourceModeRef}>
              <Form.Item
                name="source_type"
                label={<span style={{ color: 'var(--ink-muted)' }}>Source</span>}
                tooltip="Choose Local path to point at a project directory that already exists on the server, or Git URL to have the server clone a branch or tag for you."
                rules={[{ required: true }]}
              >
                <Radio.Group size="large" buttonStyle="solid">
                  <Radio.Button value="local">
                    <FolderOutlined /> Local path
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
                label={<span style={{ color: 'var(--ink-muted)' }}>Project path</span>}
                tooltip="Absolute path to the project on the server. The allowed roots are set by the operator (LOCAL_SOURCE_ROOTS)."
                validateTrigger={['onBlur', 'onChange']}
                rules={[
                  { required: true, message: 'Enter the project path' },
                  {
                    pattern: /^\//,
                    message: 'Enter an absolute path (starting with /)',
                  },
                ]}
              >
                <Input
                  placeholder="/path/to/your/project"
                  className="input-field"
                  size="large"
                />
              </Form.Item>
            )}

            </div>

            {/* ── Git URL mode ── */}
            {sourceType === 'git' && (
              <div className="glass-card p-5 mb-4">
                <SectionHeader icon={<LinkOutlined />} title="Git source" />
                <div ref={gitUrlRef}>
                  <Form.Item
                    name="git_url"
                    label={<span style={{ color: 'var(--ink-muted)' }}>Git URL</span>}
                    tooltip="The access token is injected server-side from .env — do not put credentials in the URL."
                    validateTrigger={['onBlur', 'onChange']}
                    rules={[
                      { required: true, message: 'Enter the Git URL' },
                      {
                        pattern: /^https?:\/\//,
                        message: 'Only http and https URLs are supported',
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
                      label={<span style={{ color: 'var(--ink-muted)' }}>Ref type</span>}
                    >
                      <Radio.Group
                        onChange={() => {
                          // Reset selection so the user consciously re-picks
                          form.setFieldValue('git_ref', '')
                        }}
                      >
                        <Radio.Button value="branch">
                          <BranchesOutlined /> Branch
                        </Radio.Button>
                        <Radio.Button value="tag">
                          <TagOutlined /> Tag
                        </Radio.Button>
                      </Radio.Group>
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={16}>
                    <div ref={refSelectRef}>
                      <Form.Item
                        name="git_ref"
                        label={
                          <span style={{ color: 'var(--ink-muted)' }}>
                            {gitRefType === 'tag' ? 'Tag' : 'Branch'}
                          </span>
                        }
                        tooltip={
                          gitRefType === 'tag'
                            ? 'A tag points at a fixed commit, so a later rebuild pulls the exact same code.'
                            : 'A branch keeps moving. Each rebuild pulls the latest commit on that branch.'
                        }
                        rules={[{ required: true, message: 'Pick or type a branch or tag' }]}
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
                              ? 'Enter a Git URL to list available refs, or type one'
                              : gitRefType === 'tag'
                              ? 'Pick a tag, or type one'
                              : 'Pick a branch, or type one'
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
                      <span style={{ color: 'var(--ink-muted)' }}>
                        <Spin size="small" />
                        <span className="ml-2">Scanning the repo structure…</span>
                      </span>
                    ) : currentPath.startsWith('git:') && directories.length > 0 ? (
                      <span className="text-matrix-400">
                        <ScanOutlined className="mr-1" />
                        Found {directories.length} directories. Pick the ones to bundle under Backend settings → Advanced below.
                      </span>
                    ) : currentPath.startsWith('git:') && directories.length === 0 ? (
                      <span style={{ color: 'var(--ink-muted)' }}>
                        <ScanOutlined className="mr-1" />
                        No selectable subdirectories in the repo root.
                      </span>
                    ) : (
                      <span style={{ color: 'var(--ink-faint)' }}>
                        <ScanOutlined className="mr-1" />
                        Once you pick a branch or tag, we scan for source and data directories to bundle, and read the pyproject dependency groups.
                      </span>
                    )}
                  </div>
                )}

                {/* Git mode hint */}
                <div className="text-xs" style={{ color: 'var(--ink-faint)', lineHeight: 1.6 }}>
                  <ScanOutlined className="mr-1" />
                  After the build starts, the server clones the repo into a per-task workspace{workspaceDir ? <> under <code style={{ color: 'var(--ink-muted)' }}>{workspaceDir}</code></> : null}.
                  When it finishes, open the task detail page to see the repo-structure diagnostics.
                  {hasBackend && (
                    <>
                      <br />
                      <ScanOutlined className="mr-1" />
                      When <code style={{ color: 'var(--ink-muted)' }}>pyproject.toml</code>, <code style={{ color: 'var(--ink-muted)' }}>uv.lock</code>, or <code style={{ color: 'var(--ink-muted)' }}>requirements.txt</code> is present, it runs <code style={{ color: 'var(--ink-muted)' }}>uv sync --frozen</code> to build the <code style={{ color: 'var(--ink-muted)' }}>.venv</code>.
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
          {/* ════ end Step: basic ════ */}

          {/* ════════════════════════════════════════════
              Step: Backend — Nuitka settings (only when hasBackend)
              ════════════════════════════════════════════ */}
          <div style={{ display: currentStepKey === 'backend' ? 'block' : 'none' }}>
            {hasBackend && (
              <div className="glass-card p-5 mb-4">
                <SectionHeader icon={<CodeOutlined />} title="Backend settings" />
                {/* Version mismatch is caught here rather than during the
                    build. The same ABI detection already ran in preflight, but
                    only after the user had filled in every step, queued, and
                    waited — one project submitted a version its own .so files
                    could not load 37 times that way. */}
                {pythonMismatch && (
                  <div className="p-4 mb-4 rounded-lg border-l-2 border-signal-400 bg-signal-500/8">
                    <div className="flex items-start gap-2">
                      <WarningOutlined style={{ color: 'var(--color-signal-400)', marginTop: 3 }} />
                      <div className="flex-1">
                        <div className="text-sm text-signal-400">
                          This project's packages were compiled with <b>Python {pythonMismatch.detected}</b>,
                          but you chose <b>Python {pythonMismatch.chosen}</b>.
                        </div>
                        <div className="text-xs mt-1" style={{ color: 'var(--ink-muted)' }}>
                          Built this way, the binary raises ModuleNotFoundError on startup because it can't load the project's own .so files. This is the most common cause of failure.
                        </div>
                        <div className="text-xs mt-1 font-mono break-all" style={{ color: 'var(--ink-faint)' }}>
                          {pythonMismatch.detail}
                        </div>
                        <Button
                          size="small"
                          className="mt-2"
                          onClick={() => form.setFieldValue('python_version', 'auto')}
                        >
                          Switch to auto-detect
                        </Button>
                      </div>
                    </div>
                  </div>
                )}
                {analysis?.detected_python?.inconsistent && (
                  <div className="p-4 mb-4 rounded-lg border-l-2 border-alert-400 bg-alert-500/8">
                    <div className="text-sm text-alert-400">
                      This project's .venv contains packages built for several different Python versions.
                    </div>
                    <div className="text-xs mt-1" style={{ color: 'var(--ink-muted)' }}>
                      Whichever version you pick, some packages will fail to load. Rebuild the .venv in the project directory (delete .venv, then run uv sync or pip install again), then come back to build.
                    </div>
                    <div className="text-xs mt-1 font-mono break-all" style={{ color: 'var(--ink-faint)' }}>
                      {analysis.detected_python.detail}
                    </div>
                  </div>
                )}

                <Row gutter={16}>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="python_version"
                      label={<span style={{ color: 'var(--ink-muted)' }}>Python version</span>}
                      rules={[{ required: true }]}
                      tooltip="Auto-detect reads the real Python version of the compiled packages (.so files) in the project's .venv and compiles to match — the most reliable way to avoid a version mismatch. Leave it on Auto unless you're sure."
                      extra={<span className="text-xs" style={{ color: 'var(--ink-faint)' }}>Auto compiles to match the .so files in the project's .venv (recommended). Choosing a version by hand warns you on a mismatch.</span>}
                    >
                      <Select>
                        <Option value="auto">Auto-detect (recommended)</Option>
                        {pythonVersions.map((v) => (
                          <Option key={v} value={v}>Python {v}</Option>
                        ))}
                      </Select>
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="entry_point"
                      label={<span style={{ color: 'var(--ink-muted)' }}>Entry point</span>}
                      tooltip="The main Python file to compile, relative to the project path."
                      validateTrigger={['onBlur', 'onChange']}
                      rules={[{ required: true }]}
                    >
                      <Input placeholder="main.py" className="input-field" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="output_name"
                      label={<span style={{ color: 'var(--ink-muted)' }}>Binary name</span>}
                      tooltip="Name of the compiled binary. Leave blank to derive it from the entry point."
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
                      label={<span style={{ color: 'var(--ink-muted)' }}>Nuitka pack mode</span>}
                      tooltip="External (recommended): third-party packages go in a separate libs/ directory and compile faster. Full: every dependency is packed into a single binary, but Nuitka takes a very long time — 8 hours or more."
                      extra={<span className="text-xs" style={{ color: 'var(--ink-faint)' }}>When in doubt use External — fast and reliable. Full bundles everything into one binary but compiles for a very long time.</span>}
                    >
                      <Select>
                        <Option value="external">External (recommended, libs/)</Option>
                        <Option value="full">Full (very long build, 8h+)</Option>
                      </Select>
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="onefile"
                      label={<span style={{ color: 'var(--ink-muted)' }}>Single-file binary</span>}
                      valuePropName="checked"
                    >
                      <Switch checkedChildren="On" unCheckedChildren="Off" />
                    </Form.Item>
                  </Col>
                </Row>

                {/* ── Dependency Group (PEP 735) — single-select from pyproject ── */}
                <Form.Item
                  name="dependency_group"
                  label={
                    <span style={{ color: 'var(--ink-muted)' }}>
                      Dependency group (uv <code className="text-cyber-400">--group</code>)
                    </span>
                  }
                  tooltip={
                    <div>
                      <div>Options are read from the <code>[dependency-groups]</code> section of pyproject.toml.</div>
                      <div className="mt-2">At build time this becomes <code>uv sync --group &lt;name&gt;</code>.</div>
                      <div className="mt-2">Typical use: pick the right PyTorch wheel per platform (rocm / cuda / cpu).</div>
                      <div className="mt-2" style={{ color: 'var(--ink-muted)' }}>Single-select. Leave blank to omit the --group flag (the default).</div>
                    </div>
                  }
                  extra={
                    <span className="text-xs" style={{ color: 'var(--ink-faint)' }}>
                      {depGroups.length > 0
                        ? `Read ${depGroups.length} groups from pyproject.toml. Pick one.`
                        : sourceType === 'git'
                        ? 'Pick a branch or tag to read this repo’s pyproject.toml groups.'
                        : 'Enter the project path to read the groups defined in pyproject.toml.'}
                    </span>
                  }
                >
                  <Select
                    allowClear
                    placeholder={
                      depGroups.length > 0
                        ? 'Pick a dependency group'
                        : 'No [dependency-groups] defined in this project’s pyproject.toml'
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
                      <span className="text-sm" style={{ color: 'var(--ink-muted)' }}>
                        <SettingOutlined className="mr-2" />Advanced: CPU parallelism, forced packages, extra directories
                      </span>
                    ),
                    children: (
                      <>
                        <Row gutter={16}>
                          <Col xs={24} md={8}>
                            <Form.Item
                              name="nuitka_jobs"
                              label={<span style={{ color: 'var(--ink-muted)' }}>CPU parallelism</span>}
                              tooltip="Number of parallel C-compile jobs. More is faster, but uses more memory."
                            >
                              <Select>
                                <Option value={0}>Auto</Option>
                                {cpuCount > 0 && (
                                  <Option value={cpuCount}>All cores ({cpuCount})</Option>
                                )}
                                {[1, 2, 4, 8, 16, 32, 64, 128]
                                  .filter((n) => n < cpuCount)
                                  .map((n) => (
                                    <Option key={n} value={n}>{n} cores</Option>
                                  ))}
                              </Select>
                            </Form.Item>
                          </Col>
                          <Col xs={24} md={16}>
                            <Form.Item
                              name="include_packages"
                              label={<span style={{ color: 'var(--ink-muted)' }}>Force-include packages</span>}
                              tooltip="Comma-separated. For packages Nuitka can't detect because they load dynamically or lazily."
                            >
                              <Input placeholder="e.g. litellm, openai" className="input-field" />
                            </Form.Item>
                          </Col>
                        </Row>

                        <Row gutter={16}>
                          <Col xs={24} md={12}>
                            <Form.Item
                              name="output_dir"
                              label={<span style={{ color: 'var(--ink-muted)' }}>Output directory</span>}
                              tooltip="Leave blank to use the default, 'dist'."
                            >
                              <Input placeholder="dist (default)" className="input-field" />
                            </Form.Item>
                          </Col>
                        </Row>

                        <div className="border-t border-[var(--seam)] my-3" />

                        {/* ── Nuitka compile optimization & diagnostics ── */}
                        <div className="mb-1 text-sm" style={{ color: 'var(--ink-muted)' }}>
                          <SettingOutlined className="mr-2" />Compile optimization and diagnostics
                        </div>
                        <div className="text-xs mb-3" style={{ color: 'var(--ink-faint)', lineHeight: 1.6 }}>
                          You can leave all three at their defaults. Reach for them to shrink the binary, or when the built binary can't find a file at runtime.
                        </div>
                        <Row gutter={16}>
                          <Col xs={24} md={8}>
                            <Form.Item
                              name="enable_anti_bloat"
                              label={<span style={{ color: 'var(--ink-muted)' }}>Shrink binary</span>}
                              valuePropName="checked"
                              tooltip="Enables Nuitka's anti-bloat plugin, which strips test code, docs, and dev-only imports bundled inside packages. Noticeable in Full mode; barely matters in External mode."
                              extra={<span className="text-xs" style={{ color: 'var(--ink-faint)' }}>Removes unused tests and docs to make the binary smaller (on by default).</span>}
                            >
                              <Switch checkedChildren="On" unCheckedChildren="Off" />
                            </Form.Item>
                          </Col>
                          <Col xs={24} md={8}>
                            <Form.Item
                              name="generate_report"
                              label={<span style={{ color: 'var(--ink-muted)' }}>Compilation report</span>}
                              valuePropName="checked"
                              tooltip="Writes compilation-report.xml to the output directory (included in the download). It lists whether each module was bundled — useful for debugging things missing at runtime."
                              extra={<span className="text-xs" style={{ color: 'var(--ink-faint)' }}>Use it to find what got left out when a package is missing.</span>}
                            >
                              <Switch checkedChildren="On" unCheckedChildren="Off" />
                            </Form.Item>
                          </Col>
                          <Col xs={24} md={8}>
                            <Form.Item
                              name="verify_after_build"
                              label={<span style={{ color: 'var(--ink-muted)' }}>Verify after build</span>}
                              valuePropName="checked"
                              tooltip="Launches the binary once after compiling to confirm it doesn't crash on startup for a missing package. A ModuleNotFoundError marks the task as failed and reports the missing module."
                              extra={<span className="text-xs" style={{ color: 'var(--ink-faint)' }}>Runs the binary once to confirm it starts (on by default, strongly recommended).</span>}
                            >
                              <Switch checkedChildren="On" unCheckedChildren="Off" />
                            </Form.Item>
                          </Col>
                        </Row>
                        <Form.Item
                          name="include_package_data"
                          label={<span style={{ color: 'var(--ink-muted)' }}>Force-include package data files</span>}
                          tooltip="Comma-separated package names. Bundles the non-.py data files inside a package (JSON, certificates, templates). Common ones (certifi, litellm, and so on) are detected automatically — list only the extras here."
                          extra={<span className="text-xs" style={{ color: 'var(--ink-faint)' }}>Only needed when a runtime FileNotFoundError is missing a data file that ships inside a package.</span>}
                        >
                          <Input placeholder="e.g. certifi, litellm" className="input-field" />
                        </Form.Item>

                        <div className="border-t border-[var(--seam)] my-3" />

                        {/* Extra Source Directories */}
                        <div className="mt-2">
                          <div className="flex items-center justify-between mb-3">
                            <label className="text-sm" style={{ color: 'var(--ink-muted)' }}>
                              <FolderOutlined className="mr-2" />Source code to bundle (Python)
                            </label>
                            {directories.length > 0 && (
                              <div className="flex items-center gap-2">
                                <button
                                  type="button"
                                  onClick={handleSelectAll}
                                  className="text-xs text-cyber-400 hover:text-cyber-300"
                                >
                                  {selectedDirs.length === directories.length ? 'Clear all' : 'Select all'}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => { if (sourceType === 'git') { void handleScanGitTree() } else { void fetchDirectories(projectPath || '') } }}
                                  className="p-1"
                                  style={{ color: 'var(--ink-muted)' }}
                                  title="Refresh"
                                >
                                  <ReloadOutlined />
                                </button>
                              </div>
                            )}
                          </div>
                          <div className="text-xs mb-2" style={{ color: 'var(--ink-faint)', lineHeight: 1.6 }}>
                            These directories are compiled into the binary as Python source (importable). Pick the package and module directories other than your entry point.
                          </div>
                          <div className="rounded-lg border border-[var(--seam)] bg-void-950 p-4 min-h-[60px]">
                            {(loadingDirs || scanningTree) ? (
                              <div className="flex items-center justify-center py-2">
                                <Spin size="small" />
                                <span className="ml-2 text-sm" style={{ color: 'var(--ink-muted)' }}>Loading…</span>
                              </div>
                            ) : dirError ? (
                              <div className="text-alert-400 text-sm py-2">{dirError}</div>
                            ) : directories.length === 0 ? (
                              <div className="text-sm py-2" style={{ color: 'var(--ink-faint)' }}>
                                {projectPath ? 'No directories found' : 'Enter the project path first'}
                              </div>
                            ) : (
                              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                                {directories.map((dir) => (
                                  <Checkbox
                                    key={dir}
                                    checked={selectedDirs.includes(dir)}
                                    onChange={(e) => handleDirToggle(dir, e.target.checked)}
                                  >
                                    <span className="text-sm">{dir}</span>
                                    {renderDirTags(dir)}
                                  </Checkbox>
                                ))}
                              </div>
                            )}
                          </div>
                          {selectedDirs.length > 0 && (
                            <div className="mt-2 text-xs" style={{ color: 'var(--ink-muted)' }}>
                              Selected: {selectedDirs.join(', ')}
                            </div>
                          )}
                        </div>

                        {/* Data Directories */}
                        <div className="mt-4">
                          <div className="flex items-center justify-between mb-3">
                            <label className="text-sm" style={{ color: 'var(--ink-muted)' }}>
                              <FolderOutlined className="mr-2" />Data directories (copied as-is next to the binary)
                            </label>
                          </div>
                          <div className="text-xs mb-2" style={{ color: 'var(--ink-faint)', lineHeight: 1.6 }}>
                            These directories aren't compiled in — they're copied unchanged next to the binary after the build. Good for static files, templates, and config.
                          </div>
                          <div className="rounded-lg border border-[var(--seam)] bg-void-950 p-4 min-h-[60px]">
                            {(loadingDirs || scanningTree) ? (
                              <div className="flex items-center justify-center py-2">
                                <Spin size="small" />
                              </div>
                            ) : directories.length === 0 ? (
                              <div className="text-sm py-2" style={{ color: 'var(--ink-faint)' }}>Enter the project path first</div>
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
                                  >
                                    <span className="text-sm">{dir}</span>
                                    {renderDirTags(dir)}
                                  </Checkbox>
                                ))}
                              </div>
                            )}
                          </div>
                          {selectedDataDirs.length > 0 && (
                            <div className="mt-2 text-xs" style={{ color: 'var(--ink-muted)' }}>
                              Selected: {selectedDataDirs.join(', ')}
                            </div>
                          )}
                        </div>
                      </>
                    ),
                  }]}
                />
              </div>
            )}
          </div>
          {/* ════ end Step: backend ════ */}

          {/* ════════════════════════════════════════════
              Step: Frontend (only when showFrontendSettings)
              ════════════════════════════════════════════ */}
          <div style={{ display: currentStepKey === 'frontend' ? 'block' : 'none' }}>
            {showFrontendSettings && (
              <div className="glass-card p-5 mb-4">
                <SectionHeader
                  icon={<GlobalOutlined />}
                  title="Frontend settings"
                  tone="matrix"
                  action={
                    <Button
                      size="small"
                      icon={<ScanOutlined />}
                      loading={sourceType === 'git' ? previewingFrontend : detectingConfig}
                      onClick={() => {
                        if (sourceType === 'git') { void handlePreviewGitFrontend() }
                        else { void detectFrontendConfig(false) }
                      }}
                    >
                      Auto-detect
                    </Button>
                  }
                />
                <Row gutter={16}>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="frontend_dir"
                      label={<span style={{ color: 'var(--ink-muted)' }}>Frontend directory</span>}
                      tooltip="Use '.' for the project root. If the frontend lives in a subdirectory, enter its name, e.g. 'frontend'."
                    >
                      <Input placeholder=". (project root)" className="input-field" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="frontend_build_tool"
                      label={<span style={{ color: 'var(--ink-muted)' }}>Build tool</span>}
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
                      label={<span style={{ color: 'var(--ink-muted)' }}>Build command</span>}
                      tooltip="e.g. build, build:prod"
                    >
                      <Input placeholder="build" className="input-field" />
                    </Form.Item>
                  </Col>
                </Row>
                <Row gutter={16}>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="frontend_output_dir"
                      label={<span style={{ color: 'var(--ink-muted)' }}>Output directory</span>}
                      tooltip="Where the frontend build lands, e.g. dist."
                    >
                      <Input placeholder="dist" className="input-field" />
                    </Form.Item>
                  </Col>
                </Row>

                <div className="border-t border-[var(--seam)] my-4" />
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm" style={{ color: 'var(--ink-muted)' }}>Frontend build env (.env)</span>
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
                      Scan
                    </Button>
                    <Button
                      size="small"
                      icon={<DownloadOutlined />}
                      loading={loadingEnv}
                      onClick={handleLoadEnv}
                    >
                      {sourceType === 'git' ? 'Load from Git' : 'Load from project'}
                    </Button>
                  </div>
                </div>
                <Row gutter={16}>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="frontend_env_filename"
                      label={<span style={{ color: 'var(--ink-muted)' }}>Env filename</span>}
                      tooltip="e.g. .env.production (takes priority over .env at build time)."
                      validateTrigger={['onBlur', 'onChange']}
                      rules={[{ pattern: /^\.env(\.\w+)*$/, message: 'Must be .env or .env.*' }]}
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
                  label={<span style={{ color: 'var(--ink-muted)' }}>Env content</span>}
                  tooltip="Written into the frontend directory before the build. One KEY=VALUE per line."
                >
                  <Input.TextArea
                    rows={4}
                    placeholder={"VITE_API_BASE_URL=https://api.example.com\nVITE_APP_TITLE=My App"}
                    className="input-field"
                    style={{ fontFamily: 'var(--font-mono)' }}
                  />
                </Form.Item>
              </div>
            )}
          </div>
          {/* ════ end Step: frontend ════ */}

          {/* ════════════════════════════════════════════
              Step: Docker — output decision + image settings
              ════════════════════════════════════════════ */}
          <div style={{ display: currentStepKey === 'docker' ? 'block' : 'none' }}>
            <h3 className="text-base font-semibold mb-2" style={{ color: 'var(--ink)' }}>Build a Docker image?</h3>
            <p className="text-sm mb-6" style={{ color: 'var(--ink-muted)' }}>
              {hasBackend
                ? 'The backend is compiled with Nuitka on the host first, then packed into a slim Docker image.'
                : 'The frontend is built, placed in a Docker container, and served by Nginx.'}
            </p>

            <div className="glass-card flex items-center gap-6 p-6">
              <CloudServerOutlined style={{ fontSize: 32, color: dockerEnabled ? 'var(--color-cyber-400)' : 'var(--ink-faint)' }} />
              <div className="flex-1">
                <div className="font-medium" style={{ color: 'var(--ink)' }}>Docker image output</div>
                <div className="text-sm mt-1" style={{ color: 'var(--ink-muted)' }}>
                  {dockerEnabled
                    ? 'The build is exported as a Docker image (.tar.gz).'
                    : 'Only local files are produced — no Docker image.'}
                </div>
              </div>
              <Form.Item name="docker_enabled" valuePropName="checked" style={{ marginBottom: 0 }}>
                <Switch
                  checkedChildren="On"
                  unCheckedChildren="Off"
                  style={{ transform: 'scale(1.3)' }}
                />
              </Form.Item>
            </div>

            {/* Docker Settings — shown when enabled */}
            {dockerEnabled && (
              <div className="glass-card p-5 mt-6">
                <SectionHeader icon={<CloudServerOutlined />} title="Docker settings" />
                <Row gutter={16}>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="docker_image_name"
                      label={<span style={{ color: 'var(--ink-muted)' }}>Image name</span>}
                      tooltip="e.g. myapp:latest"
                    >
                      <Input placeholder="myapp:latest" className="input-field" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="docker_base_image"
                      label={<span style={{ color: 'var(--ink-muted)' }}>Base image</span>}
                    >
                      <Input placeholder="python:3.13-slim" className="input-field" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item
                      name="docker_expose_port"
                      label={<span style={{ color: 'var(--ink-muted)' }}>Exposed port</span>}
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
                          label={<span style={{ color: 'var(--ink-muted)' }}>API proxy URL</span>}
                          tooltip="Nginx reverse-proxies /api to this URL."
                        >
                          <Input placeholder="http://192.168.1.10:5011" className="input-field" />
                        </Form.Item>
                      </Col>
                    </Row>

                    <div className="mb-1 text-sm" style={{ color: 'var(--ink-muted)' }}>
                      <SettingOutlined className="mr-2" />Nginx settings
                    </div>
                    <div className="text-xs mb-3" style={{ color: 'var(--ink-faint)', lineHeight: 1.6 }}>
                      A frontend-only Docker image is served by Nginx. These are the knobs people tweak most often — the defaults are fine.
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
                              Do I need to adjust the timeout? (open for how to decide)
                            </span>
                          ),
                          children: (
                            <div className="text-xs" style={{ color: 'var(--ink-muted)', lineHeight: 1.8 }}>
                              <div className="mb-2">
                                The timeout measures <b style={{ color: 'var(--ink)' }}>the longest single silence from the backend</b>,
                                not how long the request runs in total. Every byte received resets the clock.
                              </div>
                              <table className="w-full" style={{ borderCollapse: 'collapse' }}>
                                <tbody>
                                  {[
                                    ['SSE / token-streaming LLM', 'No change — every token resets the timer'],
                                    ['Speech-to-text, one-shot LLM', 'Raise it only if the worst case could exceed the default'],
                                    ['WebSocket with heartbeat', 'No change — the heartbeat resets the timer'],
                                    ['WebSocket without heartbeat', 'Raise it — must exceed the longest quiet period'],
                                    ['Large file upload', 'No change — streaming mode keeps data flowing'],
                                  ].map(([k, v]) => (
                                    <tr key={k}>
                                      <td
                                        className="pr-3 align-top"
                                        style={{ color: 'var(--ink)', borderTop: '1px solid var(--seam)', padding: '4px 8px 4px 0', whiteSpace: 'nowrap' }}
                                      >
                                        {k}
                                      </td>
                                      <td style={{ borderTop: '1px solid var(--seam)', padding: '4px 0' }}>
                                        {v}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                              <div className="mt-2 text-signal-400">
                                ⚠ If there's another company Nginx in front of the container, that layer has its own timeout.
                                Changing only this one does nothing — every layer in the chain applies, and the smallest wins.
                                If it still drops at around 60 seconds after a rebuild, it's the outer layer.
                              </div>
                              <div className="mt-1" style={{ color: 'var(--ink-faint)' }}>
                                The most reliable fix is to have the backend send data periodically while it works (SSE: a ": keepalive" line every 10 seconds), which satisfies every proxy layer at once.
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
                          label={<span style={{ color: 'var(--ink-muted)' }}>Max upload size</span>}
                          tooltip="nginx client_max_body_size. 0 = unlimited. e.g. 50m, 1g, 0"
                          extra={<span className="text-xs" style={{ color: 'var(--ink-faint)' }}>Raise this when uploads hit a 413. 0 = unlimited.</span>}
                          validateTrigger={['onBlur', 'onChange']}
                          rules={[{ pattern: /^\d+[kKmMgG]?$/, message: 'e.g. 50m / 1g / 0' }]}
                        >
                          <Input placeholder="0 (unlimited)" className="input-field" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={8}>
                        <Form.Item
                          name="nginx_proxy_read_timeout"
                          label={<span style={{ color: 'var(--ink-muted)' }}>Max backend silence</span>}
                          tooltip="nginx proxy_read_timeout / proxy_send_timeout. This is an idle timeout, not a cap on total request length."
                          extra={
                            <span className="text-xs" style={{ color: 'var(--ink-faint)' }}>
                              How long the backend can send nothing before Nginx gives up — not a cap on total request time.
                              Raise it for slow-to-respond APIs like speech-to-text or one-shot LLMs, or you'll get a 504.
                            </span>
                          }
                          validateTrigger={['onBlur', 'onChange']}
                          rules={[{ pattern: /^\d+[smhdSMHD]?$/, message: 'e.g. 300s / 10m' }]}
                        >
                          <Input placeholder="300s" className="input-field" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={8}>
                        <Form.Item
                          name="nginx_streaming"
                          label={<span style={{ color: 'var(--ink-muted)' }}>Streaming mode</span>}
                          valuePropName="checked"
                          tooltip="Turns off Nginx request/response buffering (proxy_buffering off)."
                          extra={
                            <span className="text-xs" style={{ color: 'var(--ink-faint)' }}>
                              Keep this on for token-streamed LLM output (SSE) and long audio uploads.
                              Off, Nginx buffers the whole response before sending, and streaming stops working.
                            </span>
                          }
                        >
                          <Switch checkedChildren="On" unCheckedChildren="Off" />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={16}>
                      <Col xs={24} md={8}>
                        <Form.Item
                          name="nginx_gzip"
                          label={<span style={{ color: 'var(--ink-muted)' }}>gzip compression</span>}
                          valuePropName="checked"
                          tooltip="Enables gzip for CSS/JS/JSON/SVG to cut transfer size."
                          extra={<span className="text-xs" style={{ color: 'var(--ink-faint)' }}>Compresses static assets (recommended on).</span>}
                        >
                          <Switch checkedChildren="On" unCheckedChildren="Off" />
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
                      <span className="text-sm" style={{ color: 'var(--ink-muted)' }}>
                        <SettingOutlined className="mr-2" />Advanced: Node.js, custom RUN commands, environment variables
                      </span>
                    ),
                    children: (
                      <>
                        <Row gutter={16}>
                          <Col xs={24} md={8}>
                            <Form.Item
                              name="docker_install_node"
                              label={<span style={{ color: 'var(--ink-muted)' }}>Install Node.js</span>}
                              valuePropName="checked"
                              tooltip="Installs Node.js 20.x inside the Docker image."
                            >
                              <Switch checkedChildren="On" unCheckedChildren="Off" />
                            </Form.Item>
                          </Col>
                        </Row>
                        <Form.Item
                          name="docker_env_vars"
                          label={<span style={{ color: 'var(--ink-muted)' }}>Runtime Docker ENV</span>}
                          tooltip="One KEY=VALUE per line. Lines starting with # are skipped."
                        >
                          <Input.TextArea
                            rows={3}
                            placeholder={"GOOGLE_API_KEY=xxxx\nMY_SECRET=yyyy"}
                            className="input-field"
                            style={{ fontFamily: 'var(--font-mono)' }}
                          />
                        </Form.Item>
                        <Form.Item
                          name="docker_custom_commands"
                          label={<span style={{ color: 'var(--ink-muted)' }}>Custom commands</span>}
                          tooltip="One command per line. Only apt-get, pip, npm, mkdir, chmod, and similar are allowed."
                        >
                          <Input.TextArea
                            rows={3}
                            placeholder={"apt-get update && apt-get install -y --no-install-recommends curl"}
                            className="input-field"
                            style={{ fontFamily: 'var(--font-mono)' }}
                          />
                        </Form.Item>
                      </>
                    ),
                  }]}
                />
              </div>
            )}
          </div>

          {/* ════════════════════════════════════════════
              Step: Review — confirmation summary
              ════════════════════════════════════════════ */}
          <div style={{ display: currentStepKey === 'review' ? 'block' : 'none' }}>
            <h3 className="text-base font-semibold mb-2" style={{ color: 'var(--ink)' }}>Review your build</h3>
            <p className="text-sm mb-6" style={{ color: 'var(--ink-muted)' }}>Check everything below before you start the build.</p>

            {/* Project Info Summary */}
            <div className="glass-card p-5 mb-4">
              <SectionHeader icon={<AppstoreOutlined />} title="Project" />
              <SummaryRow label="Name" value={form.getFieldValue('project_name')} />
              <SummaryRow label="Type" value={projectType && typeLabels[projectType] ? typeLabels[projectType].text : projectType} />
              {sourceType === 'git' ? (
                <>
                  <SummaryRow label="Source" value="Git URL" highlight />
                  <SummaryRow label="Git URL" value={form.getFieldValue('git_url')} mono />
                  <SummaryRow
                    label={form.getFieldValue('git_ref_type') === 'tag' ? 'Tag' : 'Branch'}
                    value={form.getFieldValue('git_ref')}
                    mono
                  />
                </>
              ) : (
                <>
                  <SummaryRow label="Source" value="Local path" />
                  <SummaryRow label="Project path" value={form.getFieldValue('project_path')} mono />
                </>
              )}
              {/* output_dir is a Nuitka/backend concept and only editable in
                  the backend step; frontend-only builds use frontend_output_dir
                  instead, so keep this row out of their summary. */}
              {hasBackend && (
                <SummaryRow label="Output directory" value={form.getFieldValue('output_dir') || 'dist'} mono />
              )}
            </div>

            {/* Backend Summary */}
            {hasBackend && (
              <div className="glass-card p-5 mb-4">
                <SectionHeader icon={<CodeOutlined />} title="Backend (Nuitka)" />
                <SummaryRow label="Python version" value={form.getFieldValue('python_version')} />
                <SummaryRow label="Entry point" value={form.getFieldValue('entry_point')} mono />
                <SummaryRow label="Binary name" value={form.getFieldValue('output_name') || (form.getFieldValue('entry_point') || 'main').replace(/\.py$/, '')} mono />
                <SummaryRow label="Nuitka mode" value={form.getFieldValue('pack_mode') === 'external' ? 'External (libs/, recommended)' : 'Full (very long build, 8h+)'} />
                <SummaryRow label="Single-file binary" value={form.getFieldValue('onefile') ? 'On' : 'Off'} />
                <SummaryRow label="Shrink binary" value={form.getFieldValue('enable_anti_bloat') ? 'On (anti-bloat)' : 'Off'} />
                <SummaryRow label="Verify after build" value={form.getFieldValue('verify_after_build') ? 'On (runs the binary once)' : 'Off'} highlight={form.getFieldValue('verify_after_build')} />
                {form.getFieldValue('generate_report') && (
                  <SummaryRow label="Compilation report" value="compilation-report.xml will be generated" />
                )}
                {form.getFieldValue('include_package_data') && (
                  <SummaryRow label="Package data files" value={form.getFieldValue('include_package_data')} mono />
                )}
                {(form.getFieldValue('nuitka_jobs') ?? 0) > 0 && (
                  <SummaryRow label="CPU parallelism" value={`${form.getFieldValue('nuitka_jobs')} cores`} />
                )}
                {form.getFieldValue('include_packages') && (
                  <SummaryRow label="Force-included packages" value={form.getFieldValue('include_packages')} mono />
                )}
                {(() => {
                  const group = form.getFieldValue('dependency_group') as string | undefined
                  return group ? (
                    <SummaryRow
                      label="Dependency group"
                      value={`--group ${group}`}
                      mono
                      highlight
                    />
                  ) : null
                })()}
                {selectedDirs.length > 0 && (
                  <SummaryRow label="Bundled source code" value={selectedDirs.join(', ')} mono />
                )}
                {selectedDataDirs.length > 0 && (
                  <SummaryRow label="Data directories" value={selectedDataDirs.join(', ')} mono />
                )}
              </div>
            )}

            {/* Frontend Summary */}
            {showFrontendSettings && (
              <div className="glass-card p-5 mb-4">
                <SectionHeader icon={<GlobalOutlined />} title="Frontend" tone="matrix" />
                <SummaryRow label="Frontend directory" value={form.getFieldValue('frontend_dir') || '.'} mono />
                <SummaryRow label="Build tool" value={form.getFieldValue('frontend_build_tool') || 'npm'} />
                <SummaryRow label="Build command" value={`${form.getFieldValue('frontend_build_tool') || 'npm'} run ${form.getFieldValue('frontend_build_command') || 'build'}`} mono />
                <SummaryRow label="Output directory" value={form.getFieldValue('frontend_output_dir') || 'dist'} mono />
                {form.getFieldValue('frontend_env_content') && (
                  <SummaryRow label="Env filename" value={form.getFieldValue('frontend_env_filename') || '.env'} mono />
                )}
                {form.getFieldValue('frontend_env_content') && (
                  <div className="flex py-1.5">
                    <span className="text-sm w-36 flex-shrink-0" style={{ color: 'var(--ink-faint)' }}>Env content</span>
                    <pre className="text-sm font-mono whitespace-pre-wrap m-0 flex-1 bg-void-950 rounded px-2 py-1" style={{ color: 'var(--ink)', maxHeight: 120, overflow: 'auto' }}>
                      {form.getFieldValue('frontend_env_content')}
                    </pre>
                  </div>
                )}
              </div>
            )}

            {/* Docker Summary */}
            <div className="glass-card p-5 mb-4">
              <SectionHeader icon={<CloudServerOutlined />} title="Docker" />
              <SummaryRow label="Docker enabled" value={dockerEnabled ? 'Yes' : 'No'} highlight={dockerEnabled} />
              {dockerEnabled && (
                <>
                  <SummaryRow label="Image name" value={form.getFieldValue('docker_image_name') || '(auto)'} mono />
                  <SummaryRow label="Base image" value={form.getFieldValue('docker_base_image') || 'python:3.13-slim'} mono />
                  <SummaryRow label="Exposed port" value={form.getFieldValue('docker_expose_port') || 8000} />
                  {projectType === 'frontend_only' && form.getFieldValue('docker_api_proxy') && (
                    <SummaryRow label="API proxy" value={form.getFieldValue('docker_api_proxy')} mono />
                  )}
                  {projectType === 'frontend_only' && (
                    <>
                      <SummaryRow
                        label="Max upload"
                        value={(form.getFieldValue('nginx_client_max_body_size') || '0') === '0'
                          ? '0 (unlimited)'
                          : form.getFieldValue('nginx_client_max_body_size')}
                        mono
                      />
                      <SummaryRow label="Proxy timeout" value={form.getFieldValue('nginx_proxy_read_timeout') || '300s'} mono />
                      <SummaryRow label="Streaming mode" value={form.getFieldValue('nginx_streaming') === false ? 'Off (Nginx buffers the whole response)' : 'On'} />
                      <SummaryRow label="gzip compression" value={form.getFieldValue('nginx_gzip') ? 'On' : 'Off'} />
                    </>
                  )}
                  {form.getFieldValue('docker_install_node') && (
                    <SummaryRow label="Install Node.js" value="Yes" />
                  )}
                  {form.getFieldValue('docker_env_vars') && (
                    <div className="flex py-1.5">
                      <span className="text-sm w-36 flex-shrink-0" style={{ color: 'var(--ink-faint)' }}>ENV variables</span>
                      <pre className="text-sm font-mono whitespace-pre-wrap m-0 flex-1 bg-void-950 rounded px-2 py-1" style={{ color: 'var(--ink)', maxHeight: 100, overflow: 'auto' }}>
                        {form.getFieldValue('docker_env_vars')}
                      </pre>
                    </div>
                  )}
                  {form.getFieldValue('docker_custom_commands') && (
                    <div className="flex py-1.5">
                      <span className="text-sm w-36 flex-shrink-0" style={{ color: 'var(--ink-faint)' }}>Custom commands</span>
                      <pre className="text-sm font-mono whitespace-pre-wrap m-0 flex-1 bg-void-950 rounded px-2 py-1" style={{ color: 'var(--ink)', maxHeight: 100, overflow: 'auto' }}>
                        {form.getFieldValue('docker_custom_commands')}
                      </pre>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          {/* ════════════════════════════════════════════
              Sticky footer action bar — Back (ghost) + exactly one primary
              ════════════════════════════════════════════ */}
          <div
            className="sticky bottom-0 z-10 mt-8 py-4 flex items-center justify-between gap-4 border-t border-[var(--seam)]"
            style={{ background: 'var(--color-void-950)' }}
          >
            <div className="min-w-0 truncate text-xs" style={{ color: 'var(--ink-faint)' }}>
              {!isLastStep && activeStepList[currentStep + 1] ? (
                <>
                  Next: <span className="text-cyber-400">{activeStepList[currentStep + 1].title}</span>
                </>
              ) : null}
            </div>
            <div className="flex items-center gap-3 flex-shrink-0">
              {currentStep > 0 && (
                <Button
                  htmlType="button"
                  size="large"
                  icon={<ArrowLeftOutlined />}
                  onClick={goBack}
                  style={{ height: 44 }}
                >
                  Back
                </Button>
              )}
              {!isLastStep ? (
                <Button
                  type="primary"
                  htmlType="button"
                  size="large"
                  onClick={validateAndNext}
                  className="btn-cyber"
                  style={{ height: 44, minWidth: 148 }}
                >
                  Next <ArrowRightOutlined />
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
                  style={{ height: 44, minWidth: 164 }}
                  onClick={handleManualSubmit}
                >
                  {isRebuild ? 'Start rebuild' : 'Start build'}
                </Button>
              )}
            </div>
          </div>
        </Form>
        </div>
      </div>

      {/* First-visit tour */}
      <Tour
        open={tourOpen}
        onClose={closeTour}
        steps={
          [
            {
              title: 'Choose a source',
              description:
                'Use a local path that already exists on the server, or paste a GitLab HTTP(S) URL and let the server clone it for you.',
              target: () => sourceModeRef.current,
            },
            {
              title: 'Enter the Git URL',
              description:
                'In Git URL mode, paste the repo\'s HTTPS URL and the server fetches the available branches and tags. The access token is set server-side — don\'t put it in the URL.',
              target: () => gitUrlRef.current,
            },
            {
              title: 'Pick a branch or tag',
              description:
                'A branch keeps moving over time; a tag points at a fixed version. You can also type a name that isn\'t in the list.',
              target: () => refSelectRef.current,
            },
          ] as TourProps['steps']
        }
      />
    </div>
  )
}
