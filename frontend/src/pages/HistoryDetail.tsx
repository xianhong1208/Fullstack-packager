import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Spin, Button, Tag, Tooltip, Descriptions, message, Modal } from 'antd'
import {
  ArrowLeftOutlined,
  RedoOutlined,
  DownloadOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudServerOutlined,
  DesktopOutlined,
  AppstoreOutlined,
  DockerOutlined,
  CopyOutlined,
  DeleteOutlined,
  ExclamationCircleOutlined,
  WarningOutlined,
  FileZipOutlined,
} from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { taskApi } from '../api/client'
import type { ProjectType } from '../api/types'
import { triggerUrlDownload } from '../utils/download'
import LogViewer from '../components/LogViewer'
import { STATUS_META, VERIFY_META } from '../utils/statusMeta'
import { extractErrorLines } from '../utils/diagnose'
import { getErrorDetail } from '../utils/errors'

const projectTypeLabels: Record<ProjectType, { label: string; color: string; icon: React.ReactNode }> = {
  backend_only: { label: 'Backend', color: 'blue', icon: <CloudServerOutlined /> },
  frontend_only: { label: 'Frontend', color: 'green', icon: <DesktopOutlined /> },
  fullstack: { label: 'Full-stack', color: 'purple', icon: <AppstoreOutlined /> },
}

// Relative time in the GitHub-Actions register ("3 minutes ago").
const timeAgo = (iso: string): string => {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} minute${m === 1 ? '' : 's'} ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} hour${h === 1 ? '' : 's'} ago`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d} day${d === 1 ? '' : 's'} ago`
  return new Date(iso).toLocaleDateString()
}

export default function HistoryDetail() {
  const { taskId } = useParams<{ taskId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [downloading, setDownloading] = useState(false)
  const [deletingWorkspace, setDeletingWorkspace] = useState(false)

  const { data: record, isLoading } = useQuery({
    queryKey: ['history', taskId],
    queryFn: () => taskApi.getHistoryItem(taskId!),
    enabled: !!taskId,
  })

  // Build log for this record. Logs live in the task manager's memory and are
  // evicted a while after completion, so older records may return none — in
  // that case the failure diagnosis (persisted in result) still carries the
  // reason. Empty logs → the log panel just isn't shown.
  const { data: logsData } = useQuery({
    queryKey: ['history-logs', taskId],
    queryFn: () => taskApi.getLogs(taskId!),
    enabled: !!taskId,
  })
  const logs = logsData?.logs ?? []

  // Server-configured workspace root (GIT_WORKSPACE_DIR); empty until fetched.
  const [workspaceDir, setWorkspaceDir] = useState<string>('')
  useEffect(() => {
    taskApi.getSystemInfo().then((info) => {
      if (info.git_workspace_dir) setWorkspaceDir(info.git_workspace_dir)
    }).catch((e) => console.warn('Failed to load system info; falling back to the default value.', e))
  }, [])

  const handleDeleteWorkspace = () => {
    if (!record) return
    Modal.confirm({
      title: 'Delete this workspace?',
      icon: <ExclamationCircleOutlined style={{ color: '#f27d7d' }} />,
      content: (
        <div>
          <p>
            This removes the task's workspace on the server{workspaceDir ? <> (<code>{workspaceDir}/{record.task_id}</code>)</> : ''} — the cloned source and its <code>.venv</code>.
          </p>
          {record.status === 'completed' && !record.config?.docker_enabled && (
            <p className="text-orange-400">
              ⚠️ This is a non-Docker completed run — once deleted, its output <b>can no longer be
              downloaded</b>.
            </p>
          )}
          <p className="text-gray-400 text-sm mt-2">
            The history record is kept, so you can still find this run in History.
          </p>
        </div>
      ),
      okText: 'Delete',
      okButtonProps: { danger: true },
      cancelText: 'Cancel',
      onOk: async () => {
        setDeletingWorkspace(true)
        try {
          await taskApi.deleteTaskWorkspace(record.task_id)
          message.success('Workspace deleted.')
          queryClient.invalidateQueries({ queryKey: ['history'] })
        } catch (err: unknown) {
          const e = err as { response?: { data?: { detail?: string } } }
          message.error(e.response?.data?.detail || 'Could not delete the workspace. Please try again.')
        } finally {
          setDeletingWorkspace(false)
        }
      },
    })
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Spin size="large" />
      </div>
    )
  }

  if (!record) {
    return (
      <div className="text-center py-20">
        <h2 className="text-xl mb-4" style={{ color: 'var(--ink-muted)' }}>
          We couldn't find this run.
        </h2>
        <button onClick={() => navigate('/history')} className="btn-cyber">
          Back to history
        </button>
      </div>
    )
  }

  const status = STATUS_META[record.status]
  const config = record.config
  const projectType = config ? projectTypeLabels[config.project_type] : null

  const formatDuration = (start: string, end: string | null): string => {
    if (!end) return '-'
    const ms = new Date(end).getTime() - new Date(start).getTime()
    if (ms < 0) return '-'
    const seconds = Math.floor(ms / 1000)
    if (seconds < 60) return `${seconds}s`
    const minutes = Math.floor(seconds / 60)
    const remainSeconds = seconds % 60
    if (minutes < 60) return `${minutes}m ${remainSeconds}s`
    const hours = Math.floor(minutes / 60)
    const remainMinutes = minutes % 60
    return `${hours}h ${remainMinutes}m`
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    message.success('Copied to clipboard.')
  }

  return (
    <div>
      {/* Run header — status glyph + run title + a muted meta line, GitHub-Actions style. */}
      <div className="flex items-start gap-3 mb-6">
        <button
          onClick={() => navigate('/history')}
          aria-label="Back to history"
          className="p-2 rounded-lg mt-0.5 transition-colors"
          style={{ color: 'var(--ink-muted)' }}
        >
          <ArrowLeftOutlined className="text-lg" />
        </button>

        <span
          className="mt-0.5 flex-shrink-0"
          style={{ color: status.color, fontSize: 22, display: 'inline-flex' }}
        >
          {status.icon}
        </span>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1
              className="text-xl font-semibold"
              style={{ color: 'var(--ink)', fontFamily: 'var(--font-display)' }}
            >
              {record.project_name}
            </h1>
            <span
              className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium uppercase"
              style={{ backgroundColor: status.bgColor, color: status.color }}
            >
              {status.label}
            </span>
          </div>

          <div
            className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm mt-1"
            style={{ color: 'var(--ink-faint)' }}
          >
            {projectType && <span>{projectType.label}</span>}
            {config?.docker_enabled && (
              <>
                <span aria-hidden>·</span>
                <span>Docker</span>
              </>
            )}
            <span aria-hidden>·</span>
            <span>Triggered by {record.user_name}</span>
            <span aria-hidden>·</span>
            <span>Started {timeAgo(record.start_time)}</span>
            <span aria-hidden>·</span>
            <span className="font-mono">{formatDuration(record.start_time, record.end_time)}</span>
            <Tooltip title="Copy full ID">
              <span
                className="font-mono ml-1 cursor-pointer hover:text-cyber-300 inline-flex items-center gap-1"
                onClick={() => copyToClipboard(record.task_id)}
              >
                #{record.task_id.slice(0, 8)}
                <CopyOutlined />
              </span>
            </Tooltip>
          </div>
        </div>

        {/* Primary actions */}
        <div className="flex items-center gap-2 flex-shrink-0">
          {config && (
            <Button
              icon={<RedoOutlined />}
              onClick={() =>
                navigate('/create', {
                  state: {
                    rebuild: true,
                    projectName: record.project_name,
                    config: record.config,
                  },
                })
              }
            >
              Rebuild
            </Button>
          )}
          {record.status === 'completed' && (
            <Button
              type="primary"
              icon={<DownloadOutlined />}
              loading={downloading}
              onClick={async () => {
                setDownloading(true)
                try {
                  triggerUrlDownload(await taskApi.getOutputDownloadUrl(record.task_id))
                  message.success('Download started.')
                } catch (err: unknown) {
                  message.error(getErrorDetail(err, 'Could not download the output. Please try again.'))
                } finally {
                  setDownloading(false)
                }
              }}
            >
              Download output
            </Button>
          )}
          {config?.source_type === 'git' &&
            record.status !== 'running' &&
            record.status !== 'pending' && (
              <Button
                danger
                icon={<DeleteOutlined />}
                loading={deletingWorkspace}
                onClick={handleDeleteWorkspace}
              >
                Delete workspace
              </Button>
            )}
        </div>
      </div>

      {/* Failure diagnosis — diagnosis is persisted in result so it shows
          even for old records whose logs were already evicted. */}
      {record.status === 'failed' && (
        <div className="glass-card p-5 mb-6 border-l-2 border-alert-500 bg-alert-500/5">
          <div className="flex items-center gap-2 mb-3">
            <CloseCircleOutlined className="text-alert-400" />
            <h3 className="text-base font-medium text-alert-400">Build failed</h3>
          </div>
          {record.result?.diagnosis && record.result.diagnosis.length > 0 ? (
            <div className="space-y-3 mb-3">
              {record.result.diagnosis.map((d, i) => (
                <div key={i} className="rounded-lg p-3 bg-void-950">
                  <div className="text-sm text-alert-400 font-medium mb-1">⚠ {d.problem}</div>
                  <div className="text-sm text-cyber-200">Suggested fix: {d.suggestion}</div>
                  {d.evidence && (
                    <code
                      className="block text-xs mt-1.5 font-mono break-all"
                      style={{ color: 'var(--ink-faint)' }}
                    >
                      {d.evidence}
                    </code>
                  )}
                  {/* Knowing the cause is only half of it. When the fix is a
                      config change the platform can make, offer it here —
                      otherwise the user has to re-enter the whole form, which
                      is how the same wrong value gets submitted repeatedly. */}
                  {d.action && config && (
                    <Button
                      size="small"
                      type="primary"
                      className="mt-2"
                      onClick={() =>
                        navigate('/create', {
                          state: {
                            rebuild: true,
                            projectName: record.project_name,
                            config: { ...config, ...d.action!.overrides },
                          },
                        })
                      }
                    >
                      {d.action.label}
                    </Button>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs" style={{ color: 'var(--ink-faint)' }}>
              This run left no automatic diagnosis. Check the log below for details.
            </p>
          )}
          {(() => {
            // Prefer the server-persisted lines: build logs are evicted from
            // memory about an hour after a task finishes, so deriving them from
            // `logs` here returned nothing for exactly the case this panel
            // exists for — coming back later to understand an old failure.
            const errLines = record.result?.error_lines?.length
              ? record.result.error_lines
              : extractErrorLines(logs)
            return errLines.length > 0 ? (
              <div>
                <div
                  className="text-xs mb-1.5 uppercase tracking-wider"
                  style={{ color: 'var(--ink-faint)' }}
                >
                  Key error lines
                </div>
                <div className="rounded-lg p-3 font-mono text-xs space-y-1 bg-void-950">
                  {errLines.map((l, i) => (
                    <div key={i} className="text-alert-400 break-all">
                      {l}
                    </div>
                  ))}
                </div>
              </div>
            ) : null
          })()}
        </div>
      )}

      {/* Build result summary (preflight / artifact / verify) */}
      {record.result && (
        <div className="glass-card p-5 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-base font-medium" style={{ color: 'var(--ink)' }}>
              Build result
            </h3>
            {record.result.verify && (
              <span
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
                style={{
                  backgroundColor: VERIFY_META[record.result.verify.status].bg,
                  color: VERIFY_META[record.result.verify.status].color,
                }}
              >
                {VERIFY_META[record.result.verify.status].icon}
                {VERIFY_META[record.result.verify.status].label}
              </span>
            )}
          </div>

          {record.result.verify?.detail && (
            <div
              className="text-sm mb-4 px-3 py-2 rounded-lg"
              style={{
                background: VERIFY_META[record.result.verify.status].bg,
                color: VERIFY_META[record.result.verify.status].color,
                lineHeight: 1.6,
              }}
            >
              {record.result.verify.detail}
            </div>
          )}

          {record.result.artifact && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
              <div className="rounded-lg p-3 bg-void-950">
                <div className="text-xs mb-1" style={{ color: 'var(--ink-faint)' }}>
                  <FileZipOutlined className="mr-1" />
                  Artifact size
                </div>
                <div className="text-lg font-semibold text-cyber-300">
                  {record.result.artifact.size_human}
                </div>
              </div>
              <div className="rounded-lg p-3 bg-void-950">
                <div className="text-xs mb-1" style={{ color: 'var(--ink-faint)' }}>
                  File count
                </div>
                <div className="text-lg font-semibold" style={{ color: 'var(--ink)' }}>
                  {record.result.artifact.file_count}
                </div>
              </div>
              {record.result.artifact.sha256 && (
                <div className="rounded-lg p-3 col-span-2 bg-void-950">
                  <div className="text-xs mb-1" style={{ color: 'var(--ink-faint)' }}>
                    SHA-256 (main binary)
                  </div>
                  <Tooltip title="Click to copy the full hash">
                    <code
                      className="text-xs font-mono break-all cursor-pointer hover:text-cyber-300"
                      style={{ color: 'var(--ink-muted)' }}
                      onClick={() => copyToClipboard(record.result?.artifact?.sha256 || '')}
                    >
                      {record.result.artifact.sha256.slice(0, 32)}…
                    </code>
                  </Tooltip>
                </div>
              )}
            </div>
          )}

          {record.result.preflight && record.result.preflight.length > 0 && (
            <div>
              <div
                className="text-xs mb-2 uppercase tracking-wider"
                style={{ color: 'var(--ink-faint)' }}
              >
                Preflight checks
              </div>
              <div className="space-y-1.5">
                {record.result.preflight.map((c) => (
                  <div key={c.label} className="flex items-start gap-2 text-sm">
                    <span
                      className={
                        c.passed ? 'text-matrix-400' : c.critical ? 'text-alert-400' : 'text-signal-400'
                      }
                      style={{ marginTop: 2 }}
                    >
                      {c.passed ? (
                        <CheckCircleOutlined />
                      ) : c.critical ? (
                        <CloseCircleOutlined />
                      ) : (
                        <WarningOutlined />
                      )}
                    </span>
                    <span className="w-28 flex-shrink-0" style={{ color: 'var(--ink)' }}>
                      {c.label}
                    </span>
                    <span
                      className="text-xs flex-1"
                      style={{ color: 'var(--ink-faint)', lineHeight: 1.6 }}
                    >
                      {c.detail}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {record.result.report_file && (
            <div className="text-xs mt-4" style={{ color: 'var(--ink-faint)' }}>
              A build report was generated:{' '}
              <code style={{ color: 'var(--ink-muted)' }}>{record.result.report_file}</code> (included
              in the download).
            </div>
          )}
        </div>
      )}

      {/* Full build log (when still available in memory) — the focus. */}
      {logs.length > 0 && (
        <div className="mb-6">
          <LogViewer logs={logs} title="Build log" height={360} />
        </div>
      )}

      {/* Run details + build configuration (secondary panel) */}
      <div className="glass-card p-5">
        {/* Run details */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-3 text-sm mb-5">
          <div>
            <div className="text-xs mb-0.5" style={{ color: 'var(--ink-faint)' }}>
              Started
            </div>
            <div style={{ color: 'var(--ink)' }}>
              {new Date(record.start_time).toLocaleString()}
            </div>
          </div>
          <div>
            <div className="text-xs mb-0.5" style={{ color: 'var(--ink-faint)' }}>
              Finished
            </div>
            <div style={{ color: 'var(--ink)' }}>
              {record.end_time ? new Date(record.end_time).toLocaleString() : '-'}
            </div>
          </div>
          <div>
            <div className="text-xs mb-0.5" style={{ color: 'var(--ink-faint)' }}>
              Duration
            </div>
            <div className="font-mono text-cyber-400">
              {formatDuration(record.start_time, record.end_time)}
            </div>
          </div>
          {record.output_dir && (
            <div className="min-w-0">
              <div className="text-xs mb-0.5" style={{ color: 'var(--ink-faint)' }}>
                Output path
              </div>
              <Tooltip title={record.output_dir}>
                <div
                  className="font-mono text-xs truncate cursor-pointer"
                  style={{ color: 'var(--ink)' }}
                  onClick={() => copyToClipboard(record.output_dir)}
                >
                  {record.output_dir}
                </div>
              </Tooltip>
            </div>
          )}
        </div>

        <div className="border-t pt-5" style={{ borderColor: 'var(--seam)' }}>
          <h3
            className="text-sm font-medium uppercase tracking-wider mb-4"
            style={{ color: 'var(--ink-muted)' }}
          >
            Build configuration
          </h3>

          {config ? (
            <div className="space-y-4">
              {/* Type Tags */}
              <div className="flex items-center gap-3 flex-wrap">
                {projectType && (
                  <Tag icon={projectType.icon} color={projectType.color} style={{ fontSize: 14, padding: '2px 10px' }}>
                    {projectType.label}
                  </Tag>
                )}
                {config.docker_enabled && (
                  <Tag icon={<DockerOutlined />} color="#2496ED" style={{ fontSize: 14, padding: '2px 10px' }}>
                    Docker
                  </Tag>
                )}
                <Tag color={config.pack_mode === 'full' ? 'orange' : 'cyan'} style={{ fontSize: 14, padding: '2px 10px' }}>
                  Pack: {config.pack_mode === 'full' ? 'Full' : 'External'}
                </Tag>
                {config.onefile && (
                  <Tag color="default" style={{ fontSize: 14, padding: '2px 10px' }}>
                    One-file
                  </Tag>
                )}
              </div>

              {/* Common Settings */}
              <Descriptions
                column={2}
                size="small"
                labelStyle={{ color: 'var(--ink-faint)', fontSize: 13 }}
                contentStyle={{ color: 'var(--ink)', fontSize: 13 }}
              >
                {config.source_type === 'git' ? (
                  <>
                    <Descriptions.Item label="Source" span={2}>
                      <span className="text-cyber-300">Git URL</span>
                    </Descriptions.Item>
                    <Descriptions.Item label="Git URL" span={2}>
                      <code style={{ fontSize: 12 }} className="text-cyber-300">{config.git_url}</code>
                    </Descriptions.Item>
                    <Descriptions.Item
                      label={config.git_ref_type === 'tag' ? 'Tag' : 'Branch'}
                      span={2}
                    >
                      <code style={{ fontSize: 12 }}>{config.git_ref}</code>
                    </Descriptions.Item>
                    <Descriptions.Item label="Workspace" span={2}>
                      <code style={{ fontSize: 11, color: 'var(--ink-faint)' }}>
                        {workspaceDir ? `${workspaceDir}/${record.task_id}` : record.task_id}
                      </code>
                    </Descriptions.Item>
                  </>
                ) : (
                  <Descriptions.Item label="Project path" span={2}>
                    <code style={{ fontSize: 12 }} className="text-cyber-300">{config.project_path}</code>
                  </Descriptions.Item>
                )}
                <Descriptions.Item label="Output dir">
                  <code style={{ fontSize: 12 }}>{config.output_dir}</code>
                </Descriptions.Item>
              </Descriptions>

              {/* Backend Settings */}
              {(config.project_type === 'backend_only' || config.project_type === 'fullstack') && (
                <div className="border-t pt-3" style={{ borderColor: 'var(--seam)' }}>
                  <h4 className="text-xs font-medium text-cyber-500 uppercase tracking-wider mb-3">
                    <CloudServerOutlined className="mr-1" /> Backend
                  </h4>
                  <Descriptions
                    column={2}
                    size="small"
                    labelStyle={{ color: 'var(--ink-faint)', fontSize: 13 }}
                    contentStyle={{ color: 'var(--ink)', fontSize: 13 }}
                  >
                    <Descriptions.Item label="Python version">v{config.python_version}</Descriptions.Item>
                    <Descriptions.Item label="Entry point">
                      <code style={{ fontSize: 12 }}>{config.entry_point}</code>
                    </Descriptions.Item>
                    {config.output_name && (
                      <Descriptions.Item label="Binary name">
                        <code style={{ fontSize: 12 }}>{config.output_name}</code>
                      </Descriptions.Item>
                    )}
                    <Descriptions.Item label="CPU jobs">
                      {config.nuitka_jobs > 0 ? `${config.nuitka_jobs} cores` : 'Auto'}
                    </Descriptions.Item>
                    {config.include_packages && (
                      <Descriptions.Item label="Included packages" span={2}>
                        <code style={{ fontSize: 12 }}>{config.include_packages}</code>
                      </Descriptions.Item>
                    )}
                    {config.extra_dirs && (
                      <Descriptions.Item label="Bundled source" span={2}>
                        <code style={{ fontSize: 12 }}>{config.extra_dirs}</code>
                      </Descriptions.Item>
                    )}
                    {config.data_dirs && (
                      <Descriptions.Item label="Data dirs" span={2}>
                        <code style={{ fontSize: 12 }}>{config.data_dirs}</code>
                      </Descriptions.Item>
                    )}
                  </Descriptions>
                </div>
              )}

              {/* Frontend Settings */}
              {(config.project_type === 'frontend_only' || config.project_type === 'fullstack') && (
                <div className="border-t pt-3" style={{ borderColor: 'var(--seam)' }}>
                  <h4 className="text-xs font-medium text-matrix-500 uppercase tracking-wider mb-3">
                    <DesktopOutlined className="mr-1" /> Frontend
                  </h4>
                  <Descriptions
                    column={2}
                    size="small"
                    labelStyle={{ color: 'var(--ink-faint)', fontSize: 13 }}
                    contentStyle={{ color: 'var(--ink)', fontSize: 13 }}
                  >
                    <Descriptions.Item label="Frontend dir">
                      <code style={{ fontSize: 12 }}>{config.frontend_dir}</code>
                    </Descriptions.Item>
                    <Descriptions.Item label="Build tool">{config.frontend_build_tool}</Descriptions.Item>
                    <Descriptions.Item label="Build command">
                      <code style={{ fontSize: 12 }}>{config.frontend_build_command}</code>
                    </Descriptions.Item>
                    <Descriptions.Item label="Output dir">
                      <code style={{ fontSize: 12 }}>{config.frontend_output_dir}</code>
                    </Descriptions.Item>
                    {config.frontend_env_content && (
                      <Descriptions.Item label="Env file">
                        <code style={{ fontSize: 12 }}>{config.frontend_env_filename}</code>
                      </Descriptions.Item>
                    )}
                  </Descriptions>
                  {config.frontend_env_content && (
                    <div className="mt-3">
                      <span className="text-xs" style={{ color: 'var(--ink-faint)' }}>
                        Frontend build env
                      </span>
                      <pre
                        className="mt-1 p-3 rounded-lg text-xs overflow-auto max-h-40 bg-void-950"
                        style={{ color: 'var(--ink-muted)', border: '1px solid var(--seam)' }}
                      >
                        {config.frontend_env_content}
                      </pre>
                    </div>
                  )}
                </div>
              )}

              {/* Docker Settings */}
              {config.docker_enabled && (
                <div className="border-t pt-3" style={{ borderColor: 'var(--seam)' }}>
                  <h4
                    className="text-xs font-medium uppercase tracking-wider mb-3"
                    style={{ color: 'var(--color-cyber-500)' }}
                  >
                    <DockerOutlined className="mr-1" /> Docker
                  </h4>
                  <Descriptions
                    column={2}
                    size="small"
                    labelStyle={{ color: 'var(--ink-faint)', fontSize: 13 }}
                    contentStyle={{ color: 'var(--ink)', fontSize: 13 }}
                  >
                    {config.docker_image_name && (
                      <Descriptions.Item label="Image name">
                        <code style={{ fontSize: 12 }}>{config.docker_image_name}</code>
                      </Descriptions.Item>
                    )}
                    <Descriptions.Item label="Base image">
                      <code style={{ fontSize: 12 }}>{config.docker_base_image}</code>
                    </Descriptions.Item>
                    <Descriptions.Item label="Exposed port">{config.docker_expose_port}</Descriptions.Item>
                    {config.docker_install_node && (
                      <Descriptions.Item label="Node.js">Installed</Descriptions.Item>
                    )}
                    {config.docker_api_proxy && (
                      <Descriptions.Item label="API proxy" span={2}>
                        <code style={{ fontSize: 12 }}>{config.docker_api_proxy}</code>
                      </Descriptions.Item>
                    )}
                  </Descriptions>
                  {config.docker_env_vars && (
                    <div className="mt-3">
                      <span className="text-xs" style={{ color: 'var(--ink-faint)' }}>
                        Docker runtime env
                      </span>
                      <pre
                        className="mt-1 p-3 rounded-lg text-xs overflow-auto max-h-40 bg-void-950"
                        style={{ color: 'var(--ink-muted)', border: '1px solid var(--seam)' }}
                      >
                        {config.docker_env_vars}
                      </pre>
                    </div>
                  )}
                  {config.docker_custom_commands && (
                    <div className="mt-3">
                      <span className="text-xs" style={{ color: 'var(--ink-faint)' }}>
                        Custom commands
                      </span>
                      <pre
                        className="mt-1 p-3 rounded-lg text-xs overflow-auto max-h-40 bg-void-950"
                        style={{ color: 'var(--ink-muted)', border: '1px solid var(--seam)' }}
                      >
                        {config.docker_custom_commands}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm" style={{ color: 'var(--ink-faint)' }}>
              No configuration data available for this build.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
