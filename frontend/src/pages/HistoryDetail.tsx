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
  backend_only: { label: '後端', color: 'blue', icon: <CloudServerOutlined /> },
  frontend_only: { label: '前端', color: 'green', icon: <DesktopOutlined /> },
  fullstack: { label: '全端', color: 'purple', icon: <AppstoreOutlined /> },
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

  // Server-configured workspace root (GIT_WORKSPACE_DIR) — the static
  // string is only the pre-fetch fallback
  const [workspaceDir, setWorkspaceDir] = useState<string>('/media/disk1/Build_workspace')
  useEffect(() => {
    taskApi.getSystemInfo().then((info) => {
      if (info.git_workspace_dir) setWorkspaceDir(info.git_workspace_dir)
    }).catch((e) => console.warn('系統資訊載入失敗，改用預設值', e))
  }, [])

  const handleDeleteWorkspace = () => {
    if (!record) return
    Modal.confirm({
      title: '確定要刪除此工作區嗎？',
      icon: <ExclamationCircleOutlined style={{ color: '#f87171' }} />,
      content: (
        <div>
          <p>這會刪除 <code>{workspaceDir}/{record.task_id}</code> 底下的所有內容，包括 clone 下來的原始碼與 <code>.venv</code>。</p>
          {record.status === 'completed' && !record.config?.docker_enabled && (
            <p className="text-orange-400">
              ⚠️ 這是非 Docker 的完成任務，刪除後將<b>無法再下載</b> Output 檔案。
            </p>
          )}
          <p className="text-gray-400 text-sm mt-2">
            任務的歷史紀錄不會被刪除，仍可在 History 裡看到。
          </p>
        </div>
      ),
      okText: '確定刪除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        setDeletingWorkspace(true)
        try {
          await taskApi.deleteTaskWorkspace(record.task_id)
          message.success('工作區已刪除')
          queryClient.invalidateQueries({ queryKey: ['history'] })
        } catch (err: unknown) {
          const e = err as { response?: { data?: { detail?: string } } }
          message.error(e.response?.data?.detail || '刪除工作區失敗')
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
        <h2 className="text-xl text-gray-400 mb-4">找不到此紀錄</h2>
        <button onClick={() => navigate('/history')} className="btn-cyber">
          返回歷史紀錄
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
    message.success('已複製')
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center gap-4 mb-6">
        <button
          onClick={() => navigate('/history')}
          aria-label="返回歷史紀錄"
          className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-white/5 transition-colors"
        >
          <ArrowLeftOutlined className="text-lg" />
        </button>
        <div className="flex-1">
          <h1 className="text-xl font-semibold text-white" style={{ fontFamily: 'var(--font-display)' }}>
            {record.project_name}
          </h1>
          <p className="text-sm text-gray-400">
            任務 ID: {record.task_id.slice(0, 8)}...
            <Tooltip title="複製完整 ID">
              <CopyOutlined
                className="ml-2 cursor-pointer hover:text-white"
                onClick={() => copyToClipboard(record.task_id)}
              />
            </Tooltip>
          </p>
        </div>
      </div>

      {/* Failure diagnosis — diagnosis is persisted in result so it shows
          even for old records whose logs were already evicted. */}
      {record.status === 'failed' && (
        <div
          className="glass-card p-5 mb-6"
          style={{ borderLeft: '3px solid #f87171', background: 'rgba(239,68,68,0.06)' }}
        >
          <div className="flex items-center gap-2 mb-3">
            <CloseCircleOutlined style={{ color: '#f87171' }} />
            <h3 className="text-base font-medium text-red-300">打包失敗</h3>
          </div>
          {record.result?.diagnosis && record.result.diagnosis.length > 0 ? (
            <div className="space-y-3 mb-3">
              {record.result.diagnosis.map((d, i) => (
                <div key={i} className="rounded-lg p-3" style={{ background: 'rgba(0,0,0,0.25)' }}>
                  <div className="text-sm text-red-200 font-medium mb-1">⚠ {d.problem}</div>
                  <div className="text-sm text-cyan-200">建議：{d.suggestion}</div>
                  {d.evidence && (
                    <code className="block text-xs text-gray-500 mt-1.5 font-mono break-all">
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
            <p className="text-xs text-gray-500">此任務未留下自動診斷，請對照下方日誌。</p>
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
                <div className="text-xs text-gray-500 mb-1.5 uppercase tracking-wider">關鍵錯誤行</div>
                <div className="rounded-lg p-3 font-mono text-xs space-y-1" style={{ background: 'rgba(0,0,0,0.35)' }}>
                  {errLines.map((l, i) => (
                    <div key={i} className="text-red-300 break-all">{l}</div>
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
            <h3 className="text-base font-medium text-white">打包成果</h3>
            <div className="flex items-center gap-3">
              {typeof record.result.duration_seconds === 'number' && (
                <span className="text-xs text-gray-400">
                  耗時 {record.result.duration_seconds >= 60
                    ? `${Math.floor(record.result.duration_seconds / 60)}m ${Math.round(record.result.duration_seconds % 60)}s`
                    : `${record.result.duration_seconds}s`}
                </span>
              )}
              {record.result.verify && (
                <span
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
                  style={{ backgroundColor: VERIFY_META[record.result.verify.status].bg, color: VERIFY_META[record.result.verify.status].color }}
                >
                  {VERIFY_META[record.result.verify.status].icon}
                  {VERIFY_META[record.result.verify.status].label}
                </span>
              )}
            </div>
          </div>

          {record.result.verify?.detail && (
            <div
              className="text-sm mb-4 px-3 py-2 rounded-lg"
              style={{ background: VERIFY_META[record.result.verify.status].bg, color: VERIFY_META[record.result.verify.status].color, lineHeight: 1.6 }}
            >
              {record.result.verify.detail}
            </div>
          )}

          {record.result.artifact && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
              <div className="rounded-lg p-3" style={{ background: 'rgba(0,0,0,0.25)' }}>
                <div className="text-xs text-gray-500 mb-1"><FileZipOutlined className="mr-1" />產物大小</div>
                <div className="text-lg font-semibold text-cyan-300">{record.result.artifact.size_human}</div>
              </div>
              <div className="rounded-lg p-3" style={{ background: 'rgba(0,0,0,0.25)' }}>
                <div className="text-xs text-gray-500 mb-1">檔案數</div>
                <div className="text-lg font-semibold text-gray-200">{record.result.artifact.file_count}</div>
              </div>
              {record.result.artifact.sha256 && (
                <div className="rounded-lg p-3 col-span-2" style={{ background: 'rgba(0,0,0,0.25)' }}>
                  <div className="text-xs text-gray-500 mb-1">SHA-256(主執行檔)</div>
                  <Tooltip title="點擊複製完整雜湊值">
                    <code
                      className="text-xs text-gray-300 font-mono break-all cursor-pointer hover:text-cyan-300"
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
              <div className="text-xs text-gray-500 mb-2 uppercase tracking-wider">打包前預檢</div>
              <div className="space-y-1.5">
                {record.result.preflight.map((c) => (
                  <div key={c.label} className="flex items-start gap-2 text-sm">
                    <span style={{ color: c.passed ? '#4ade80' : c.critical ? '#f87171' : '#fbbf24', marginTop: 2 }}>
                      {c.passed ? <CheckCircleOutlined /> : c.critical ? <CloseCircleOutlined /> : <WarningOutlined />}
                    </span>
                    <span className="text-gray-300 w-28 flex-shrink-0">{c.label}</span>
                    <span className="text-gray-500 text-xs flex-1" style={{ lineHeight: 1.6 }}>{c.detail}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {record.result.report_file && (
            <div className="text-xs text-gray-500 mt-4">
              已產生編譯報告 <code className="text-gray-400">{record.result.report_file}</code>(已包含在下載檔內)
            </div>
          )}
        </div>
      )}

      {/* Full build log (when still available in memory) */}
      {logs.length > 0 && (
        <div className="mb-6">
          <LogViewer logs={logs} title="建置日誌" height={360} />
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Panel - Overview */}
        <div className="lg:col-span-1 space-y-4">
          {/* Status Card */}
          <div className="glass-card p-5">
            <div
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full mb-4"
              style={{ backgroundColor: status.bgColor, color: status.color }}
            >
              {status.icon}
              <span className="text-sm font-medium uppercase">{record.status}</span>
            </div>

            <div className="space-y-3 text-sm">
              <div className="flex justify-between">
                <span className="text-gray-500">使用者</span>
                <span className="text-gray-300">{record.user_name}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">開始時間</span>
                <span className="text-gray-300 text-xs">{new Date(record.start_time).toLocaleString()}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">結束時間</span>
                <span className="text-gray-300 text-xs">
                  {record.end_time ? new Date(record.end_time).toLocaleString() : '-'}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">耗時</span>
                <span className="text-cyan-400 font-mono text-xs">
                  {formatDuration(record.start_time, record.end_time)}
                </span>
              </div>
              {record.output_dir && (
                <div className="flex justify-between">
                  <span className="text-gray-500">輸出路徑</span>
                  <Tooltip title={record.output_dir}>
                    <span
                      className="text-gray-300 font-mono text-xs truncate max-w-[160px] cursor-pointer"
                      onClick={() => copyToClipboard(record.output_dir)}
                    >
                      {record.output_dir}
                    </span>
                  </Tooltip>
                </div>
              )}
            </div>

            {/* Action Buttons */}
            <div className="mt-6 space-y-2">
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
                  className="w-full"
                  style={{
                    background: 'rgba(6, 182, 212, 0.1)',
                    borderColor: 'rgba(6, 182, 212, 0.3)',
                    color: '#22d3ee',
                  }}
                >
                  重新打包
                </Button>
              )}
              {record.status === 'completed' && (
                <Button
                  icon={<DownloadOutlined />}
                  loading={downloading}
                  onClick={async () => {
                    setDownloading(true)
                    try {
                      triggerUrlDownload(await taskApi.getOutputDownloadUrl(record.task_id))
                      message.success('已開始下載輸出')
                    } catch (err: unknown) {
                      message.error(getErrorDetail(err, '下載輸出失敗'))
                    } finally {
                      setDownloading(false)
                    }
                  }}
                  className="w-full"
                  style={{
                    background: 'rgba(34, 197, 94, 0.1)',
                    borderColor: 'rgba(34, 197, 94, 0.3)',
                    color: '#4ade80',
                  }}
                >
                  下載輸出
                </Button>
              )}
              {config?.source_type === 'git'
                && record.status !== 'running'
                && record.status !== 'pending' && (
                <Button
                  icon={<DeleteOutlined />}
                  loading={deletingWorkspace}
                  onClick={handleDeleteWorkspace}
                  className="w-full"
                  style={{
                    background: 'rgba(239, 68, 68, 0.1)',
                    borderColor: 'rgba(239, 68, 68, 0.3)',
                    color: '#f87171',
                  }}
                >
                  刪除工作區
                </Button>
              )}
            </div>
          </div>
        </div>

        {/* Right Panel - Build Configuration Details */}
        <div className="lg:col-span-2 space-y-4">
          {/* Project Type & Docker */}
          <div className="glass-card p-5">
            <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wider mb-4">
              建置設定
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
                    <Tag color="default" style={{ fontSize: 14, padding: '2px 10px' }}>單檔執行檔</Tag>
                  )}
                </div>

                {/* Common Settings */}
                <Descriptions
                  column={2}
                  size="small"
                  labelStyle={{ color: '#6b7280', fontSize: 13 }}
                  contentStyle={{ color: '#d1d5db', fontSize: 13 }}
                >
                  {config.source_type === 'git' ? (
                    <>
                      <Descriptions.Item label="來源" span={2}>
                        <span style={{ color: '#67e8f9' }}>Git URL</span>
                      </Descriptions.Item>
                      <Descriptions.Item label="Git 網址" span={2}>
                        <code style={{ fontSize: 12, color: '#93c5fd' }}>{config.git_url}</code>
                      </Descriptions.Item>
                      <Descriptions.Item
                        label={config.git_ref_type === 'tag' ? '標籤' : '分支'}
                        span={2}
                      >
                        <code style={{ fontSize: 12 }}>{config.git_ref}</code>
                      </Descriptions.Item>
                      <Descriptions.Item label="工作區" span={2}>
                        <code style={{ fontSize: 11, color: '#6b7280' }}>
                          {workspaceDir}/{record.task_id}
                        </code>
                      </Descriptions.Item>
                    </>
                  ) : (
                    <Descriptions.Item label="專案路徑" span={2}>
                      <code style={{ fontSize: 12, color: '#93c5fd' }}>{config.project_path}</code>
                    </Descriptions.Item>
                  )}
                  <Descriptions.Item label="輸出目錄">
                    <code style={{ fontSize: 12 }}>{config.output_dir}</code>
                  </Descriptions.Item>
                </Descriptions>

                {/* Backend Settings */}
                {(config.project_type === 'backend_only' || config.project_type === 'fullstack') && (
                  <>
                    <div className="border-t border-white/5 pt-3">
                      <h4 className="text-xs font-medium text-cyan-500 uppercase tracking-wider mb-3">
                        <CloudServerOutlined className="mr-1" /> Backend
                      </h4>
                      <Descriptions
                        column={2}
                        size="small"
                        labelStyle={{ color: '#6b7280', fontSize: 13 }}
                        contentStyle={{ color: '#d1d5db', fontSize: 13 }}
                      >
                        <Descriptions.Item label="Python 版本">v{config.python_version}</Descriptions.Item>
                        <Descriptions.Item label="進入點">
                          <code style={{ fontSize: 12 }}>{config.entry_point}</code>
                        </Descriptions.Item>
                        {config.output_name && (
                          <Descriptions.Item label="二進位執行檔">
                            <code style={{ fontSize: 12 }}>{config.output_name}</code>
                          </Descriptions.Item>
                        )}
                        <Descriptions.Item label="CPU 並行數">
                          {config.nuitka_jobs > 0 ? `${config.nuitka_jobs} 核` : '自動'}
                        </Descriptions.Item>
                        {config.include_packages && (
                          <Descriptions.Item label="強制納入套件" span={2}>
                            <code style={{ fontSize: 12 }}>{config.include_packages}</code>
                          </Descriptions.Item>
                        )}
                        {config.extra_dirs && (
                          <Descriptions.Item label="打包的 source code" span={2}>
                            <code style={{ fontSize: 12 }}>{config.extra_dirs}</code>
                          </Descriptions.Item>
                        )}
                        {config.data_dirs && (
                          <Descriptions.Item label="資料目錄" span={2}>
                            <code style={{ fontSize: 12 }}>{config.data_dirs}</code>
                          </Descriptions.Item>
                        )}
                      </Descriptions>
                    </div>
                  </>
                )}

                {/* Frontend Settings */}
                {(config.project_type === 'frontend_only' || config.project_type === 'fullstack') && (
                  <div className="border-t border-white/5 pt-3">
                    <h4 className="text-xs font-medium text-green-500 uppercase tracking-wider mb-3">
                      <DesktopOutlined className="mr-1" /> Frontend
                    </h4>
                    <Descriptions
                      column={2}
                      size="small"
                      labelStyle={{ color: '#6b7280', fontSize: 13 }}
                      contentStyle={{ color: '#d1d5db', fontSize: 13 }}
                    >
                      <Descriptions.Item label="前端目錄">
                        <code style={{ fontSize: 12 }}>{config.frontend_dir}</code>
                      </Descriptions.Item>
                      <Descriptions.Item label="建置工具">{config.frontend_build_tool}</Descriptions.Item>
                      <Descriptions.Item label="建置指令">
                        <code style={{ fontSize: 12 }}>{config.frontend_build_command}</code>
                      </Descriptions.Item>
                      <Descriptions.Item label="輸出目錄">
                        <code style={{ fontSize: 12 }}>{config.frontend_output_dir}</code>
                      </Descriptions.Item>
                      {config.frontend_env_content && (
                        <Descriptions.Item label="Env 檔">
                          <code style={{ fontSize: 12 }}>{config.frontend_env_filename}</code>
                        </Descriptions.Item>
                      )}
                    </Descriptions>
                    {config.frontend_env_content && (
                      <div className="mt-3">
                        <span className="text-xs text-gray-500">Frontend Build Env:</span>
                        <pre
                          className="mt-1 p-3 rounded-lg text-xs text-gray-300 overflow-auto max-h-40"
                          style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.05)' }}
                        >
                          {config.frontend_env_content}
                        </pre>
                      </div>
                    )}
                  </div>
                )}

                {/* Docker Settings */}
                {config.docker_enabled && (
                  <div className="border-t border-white/5 pt-3">
                    <h4 className="text-xs font-medium uppercase tracking-wider mb-3" style={{ color: '#2496ED' }}>
                      <DockerOutlined className="mr-1" /> Docker
                    </h4>
                    <Descriptions
                      column={2}
                      size="small"
                      labelStyle={{ color: '#6b7280', fontSize: 13 }}
                      contentStyle={{ color: '#d1d5db', fontSize: 13 }}
                    >
                      {config.docker_image_name && (
                        <Descriptions.Item label="Image 名稱">
                          <code style={{ fontSize: 12 }}>{config.docker_image_name}</code>
                        </Descriptions.Item>
                      )}
                      <Descriptions.Item label="基底 Image">
                        <code style={{ fontSize: 12 }}>{config.docker_base_image}</code>
                      </Descriptions.Item>
                      <Descriptions.Item label="對外連接埠">{config.docker_expose_port}</Descriptions.Item>
                      {config.docker_install_node && (
                        <Descriptions.Item label="Node.js">已安裝</Descriptions.Item>
                      )}
                      {config.docker_api_proxy && (
                        <Descriptions.Item label="API Proxy" span={2}>
                          <code style={{ fontSize: 12 }}>{config.docker_api_proxy}</code>
                        </Descriptions.Item>
                      )}
                    </Descriptions>
                    {config.docker_env_vars && (
                      <div className="mt-3">
                        <span className="text-xs text-gray-500">Docker ENV (Runtime):</span>
                        <pre
                          className="mt-1 p-3 rounded-lg text-xs text-gray-300 overflow-auto max-h-40"
                          style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.05)' }}
                        >
                          {config.docker_env_vars}
                        </pre>
                      </div>
                    )}
                    {config.docker_custom_commands && (
                      <div className="mt-3">
                        <span className="text-xs text-gray-500">Custom Commands:</span>
                        <pre
                          className="mt-1 p-3 rounded-lg text-xs text-gray-300 overflow-auto max-h-40"
                          style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.05)' }}
                        >
                          {config.docker_custom_commands}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-gray-500 text-sm">No configuration data available for this build.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
