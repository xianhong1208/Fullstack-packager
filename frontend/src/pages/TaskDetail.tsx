import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Progress, Button, Spin, message, Steps, Tooltip } from 'antd'
import {
  ArrowLeftOutlined,
  StopOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  RedoOutlined,
  DownloadOutlined,
  WarningOutlined,
  FileZipOutlined,
  FieldTimeOutlined,
} from '@ant-design/icons'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import LogViewer from '../components/LogViewer'
import { useWebSocket } from '../hooks/useWebSocket'
import { taskApi } from '../api/client'
import type { TaskResponse } from '../api/types'
import { triggerUrlDownload } from '../utils/download'
import { STATUS_META, VERIFY_META, isTerminal } from '../utils/statusMeta'
import { getErrorDetail } from '../utils/errors'
import { extractErrorLines } from '../utils/diagnose'

// ── Build-stage stepper (set by nuitka_worker: preflight→compile→bundle→verify→done) ──
const STAGE_ORDER = ['queued', 'preflight', 'compile', 'bundle', 'verify', 'done'] as const
const STAGE_ITEMS = [
  { title: '排隊', description: '等待建置名額' },
  { title: '預檢', description: '檢查環境' },
  { title: '編譯', description: 'Nuitka 編譯' },
  { title: '打包', description: '複製依賴 / 資料' },
  { title: '驗證', description: '啟動測試' },
]
// Plain-language names for the stage a failure happened in. Mirrors
// STAGE_ITEMS above, but keyed so a value coming back from the server can be
// rendered without matching it against an index.
const STAGE_LABELS: Record<string, string> = {
  queued: '排隊等待',
  preflight: '預檢（檢查路徑、Python 版本與依賴環境）',
  compile: '編譯（Nuitka 執行中）',
  bundle: '打包（複製依賴與資料目錄）',
  verify: '驗證（試跑產出的執行檔）',
  done: '收尾',
}

const stageIndex = (stage: string) => {
  const i = STAGE_ORDER.indexOf(stage as (typeof STAGE_ORDER)[number])
  return i < 0 ? 0 : i
}

export default function TaskDetail() {
  const { taskId } = useParams<{ taskId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [wsConnected, setWsConnected] = useState(false)

  // WebSocket carries live increments; HTTP provides the initial snapshot
  // plus a reconciling poll. While WS is connected we poll slowly (5s) just
  // to correct any drift; if WS drops we fall back to 1s. Either way polling
  // stops once the task reaches a terminal state.
  const { data: task, isLoading } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => taskApi.get(taskId!),
    enabled: !!taskId,
    refetchInterval: (query) =>
      isTerminal(query.state.data?.status) ? false : wsConnected ? 5000 : 1000,
  })

  // Apply live updates straight into the query cache so the UI reacts
  // instantly without re-fetching the whole task every tick.
  const { isConnected, stoppedReason: wsStoppedReason } = useWebSocket({
    taskId,
    onMessage: (msg) => {
      queryClient.setQueryData<typeof task>(['task', taskId], (old) => {
        if (!old) return old
        switch (msg.type) {
          case 'log':
            return { ...old, logs: [...(old.logs ?? []), String(msg.data)] }
          case 'status':
            return { ...old, status: msg.data as TaskResponse['status'] }
          case 'progress':
            return { ...old, progress: Number(msg.data) }
          case 'status_msg':
            return { ...old, status_msg: String(msg.data) }
          case 'stage':
            return { ...old, stage: String(msg.data) }
          case 'result':
            return { ...old, result: msg.data as TaskResponse['result'] }
          default:
            return old
        }
      })
    },
  })
  useEffect(() => {
    setWsConnected(isConnected)
  }, [isConnected])

  const [downloading, setDownloading] = useState(false)

  // Cancel mutation
  const cancelMutation = useMutation({
    mutationFn: taskApi.cancel,
    onSuccess: () => {
      message.success('已送出取消要求')
      queryClient.invalidateQueries({ queryKey: ['task', taskId] })
    },
    onError: (err) => {
      message.error(getErrorDetail(err, '取消任務失敗'))
    },
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Spin size="large" />
      </div>
    )
  }

  if (!task) {
    return (
      <div className="text-center py-20">
        <h2 className="text-xl text-gray-400 mb-4">找不到此任務</h2>
        <button onClick={() => navigate('/')} className="btn-cyber">
          返回儀表板
        </button>
      </div>
    )
  }

  const config = STATUS_META[task.status]
  const isActive = task.status === 'pending' || task.status === 'running'
  const hasBackend = task.config.project_type === 'backend_only' || task.config.project_type === 'fullstack'
  const result = task.result

  return (
    <div>
      {/* Header */}
      <div className="flex items-center gap-4 mb-6">
        <button
          onClick={() => navigate('/')}
          aria-label="返回儀表板"
          className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-white/5 transition-colors"
        >
          <ArrowLeftOutlined className="text-lg" />
        </button>
        <div>
          <h1 className="text-xl font-semibold text-white" style={{ fontFamily: 'var(--font-display)' }}>
            {task.project_name}
          </h1>
          <p className="text-sm text-gray-400">任務 ID:{task.id.slice(0, 8)}...</p>
        </div>
      </div>

      {/* Build stage stepper — backend / fullstack only */}
      {hasBackend && task.stage && (
        <div className="glass-card p-4 mb-6">
          <Steps
            size="small"
            current={stageIndex(task.stage)}
            status={task.status === 'failed' || task.status === 'cancelled' ? 'error' : undefined}
            items={STAGE_ITEMS}
          />
        </div>
      )}

      {/* Failure diagnosis callout — shown for FAILED tasks even when
          result is null, so a failed build always explains itself. */}
      {task.status === 'failed' && (
        <div
          className="glass-card p-5 mb-6"
          style={{ borderLeft: '3px solid #f87171', background: 'rgba(239,68,68,0.06)' }}
        >
          <div className="flex items-center gap-2 mb-3">
            <CloseCircleOutlined style={{ color: '#f87171' }} />
            <h3 className="text-base font-medium text-alert-400">打包失敗</h3>
          </div>
          {task.status_msg && (
            <p className="text-sm text-gray-300 mb-3">{task.status_msg}</p>
          )}

          {/* Name the stage before the details. Someone who cannot read a
              stack trace can still act on "it failed while installing
              dependencies" — and it tells them which settings to revisit. */}
          {result?.failed_stage && (
            <div className="text-xs text-gray-400 mb-3">
              失敗階段：
              <span className="text-alert-400 ml-1">
                {STAGE_LABELS[result.failed_stage] ?? result.failed_stage}
              </span>
            </div>
          )}

          {result?.diagnosis && result.diagnosis.length > 0 ? (
            <div className="space-y-3 mb-3">
              {result.diagnosis.map((d, i) => (
                <div key={i} className="rounded-lg p-3" style={{ background: 'rgba(0,0,0,0.25)' }}>
                  <div className="text-sm text-alert-400 font-medium mb-1">⚠ {d.problem}</div>
                  <div className="text-sm text-cyber-200">建議：{d.suggestion}</div>
                  {d.evidence && (
                    <code className="block text-xs text-gray-500 mt-1.5 font-mono break-all">
                      {d.evidence}
                    </code>
                  )}
                  {/* The fix, when the platform can apply it — rebuilding with
                      the corrected setting instead of making the user rebuild
                      the whole form from memory. */}
                  {d.action && (
                    <Button
                      size="small"
                      type="primary"
                      className="mt-2"
                      onClick={() =>
                        navigate('/create', {
                          state: {
                            rebuild: true,
                            projectName: task.project_name,
                            config: { ...task.config, ...d.action!.overrides },
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
            <p className="text-xs text-gray-500 mb-3">
              系統無法自動判斷失敗原因，請對照下方關鍵錯誤行與右側完整日誌。
            </p>
          )}

          {(() => {
            // Server-selected lines when available (they persist past log
            // eviction); fall back to scanning the live log, which is what a
            // still-running or just-finished task has.
            const errLines = result?.error_lines?.length
              ? result.error_lines
              : extractErrorLines(task.logs)
            return errLines.length > 0 ? (
              <div>
                <div className="text-xs text-gray-500 mb-1.5 uppercase tracking-wider">關鍵錯誤行</div>
                <div
                  className="rounded-lg p-3 font-mono text-xs space-y-1"
                  style={{ background: 'rgba(0,0,0,0.35)' }}
                >
                  {errLines.map((l, i) => (
                    <div key={i} className="text-alert-400 break-all">{l}</div>
                  ))}
                </div>
              </div>
            ) : null
          })()}
        </div>
      )}

      {/* Build result card — preflight / artifact / verify */}
      {result && (
        <div className="glass-card p-5 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-base font-medium text-white">打包成果</h3>
            <div className="flex items-center gap-3">
              {typeof result.duration_seconds === 'number' && (
                <span className="text-xs text-gray-400">
                  <FieldTimeOutlined className="mr-1" />
                  {result.duration_seconds >= 60
                    ? `${Math.floor(result.duration_seconds / 60)}m ${Math.round(result.duration_seconds % 60)}s`
                    : `${result.duration_seconds}s`}
                </span>
              )}
              {result.verify && (
                <span
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
                  style={{ backgroundColor: VERIFY_META[result.verify.status].bg, color: VERIFY_META[result.verify.status].color }}
                >
                  {VERIFY_META[result.verify.status].icon}
                  {VERIFY_META[result.verify.status].label}
                </span>
              )}
            </div>
          </div>

          {result.verify?.detail && (
            <div
              className="text-sm mb-4 px-3 py-2 rounded-lg"
              style={{ background: VERIFY_META[result.verify.status].bg, color: VERIFY_META[result.verify.status].color, lineHeight: 1.6 }}
            >
              {result.verify.detail}
            </div>
          )}

          {result.artifact && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
              <div className="rounded-lg p-3" style={{ background: 'rgba(0,0,0,0.25)' }}>
                <div className="text-xs text-gray-500 mb-1"><FileZipOutlined className="mr-1" />產物大小</div>
                <div className="text-lg font-semibold text-cyber-300">{result.artifact.size_human}</div>
              </div>
              <div className="rounded-lg p-3" style={{ background: 'rgba(0,0,0,0.25)' }}>
                <div className="text-xs text-gray-500 mb-1">檔案數</div>
                <div className="text-lg font-semibold text-gray-200">{result.artifact.file_count}</div>
              </div>
              {result.artifact.sha256 && (
                <div className="rounded-lg p-3 col-span-2" style={{ background: 'rgba(0,0,0,0.25)' }}>
                  <div className="text-xs text-gray-500 mb-1">SHA-256(主執行檔)</div>
                  <Tooltip title="點擊複製完整雜湊值">
                    <code
                      className="text-xs text-gray-300 font-mono break-all cursor-pointer hover:text-cyber-300"
                      onClick={() => {
                        navigator.clipboard?.writeText(result.artifact?.sha256 || '')
                        message.success('已複製 SHA-256')
                      }}
                    >
                      {result.artifact.sha256.slice(0, 32)}…
                    </code>
                  </Tooltip>
                </div>
              )}
            </div>
          )}

          {result.preflight && result.preflight.length > 0 && (
            <div>
              <div className="text-xs text-gray-500 mb-2 uppercase tracking-wider">打包前預檢</div>
              <div className="space-y-1.5">
                {result.preflight.map((c) => (
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

          {result.report_file && (
            <div className="text-xs text-gray-500 mt-4">
              已產生編譯報告 <code className="text-gray-400">{result.report_file}</code>(已包含在下載檔內)
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Panel - Task Info */}
        <div className="lg:col-span-1">
          <div className="glass-card p-5">
            {/* Status Badge */}
            <div
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full mb-4"
              style={{ backgroundColor: config.bgColor, color: config.color }}
            >
              {config.icon}
              <span className="text-sm font-medium uppercase">{task.status}</span>
            </div>

            {/* Progress - always show */}
            <div className="mb-6">
              <div className="flex justify-between text-sm mb-2">
                <span className="text-gray-400">進度</span>
                <span className="text-cyber-400 font-mono">{task.progress}%</span>
              </div>
              <Progress
                percent={task.progress}
                showInfo={false}
                strokeColor={
                  task.status === 'completed'
                    ? '#4ade80'
                    : task.status === 'failed'
                    ? '#f87171'
                    : {
                        '0%': '#c6692f',
                        '100%': '#f09a5e',
                      }
                }
                trailColor="#322a20"
              />
              {task.status_msg && (
                <p className="text-sm text-gray-400 mt-2">{task.status_msg}</p>
              )}
            </div>

            {/* Details */}
            <div className="space-y-3 text-sm">
              <div className="flex justify-between">
                <span className="text-gray-500">使用者</span>
                <span className="text-gray-300">{task.user_name}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">專案類型</span>
                <span className="text-gray-300">
                  {{ backend_only: '後端', frontend_only: '前端', fullstack: '全端' }[task.config.project_type]}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">打包模式</span>
                <span className="text-gray-300">
                  {{ full: 'Full', external: 'External' }[task.config.pack_mode]}
                </span>
              </div>
              {task.config.docker_enabled && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Docker</span>
                  <span className="text-gray-300">是</span>
                </div>
              )}

              {/* Backend settings */}
              {(task.config.project_type === 'backend_only' || task.config.project_type === 'fullstack') && (
                <>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Python</span>
                    <span className="text-gray-300">{task.python_version}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">進入點</span>
                    <span className="text-gray-300 font-mono text-xs">{task.config.entry_point}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">二進位執行檔</span>
                    <span className="text-gray-300 font-mono text-xs">{task.config.output_name}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">單檔執行檔</span>
                    <span className="text-gray-300">{task.config.onefile ? '是' : '否'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">CPU 並行數</span>
                    <span className="text-gray-300">
                      {task.config.nuitka_jobs > 0 ? `${task.config.nuitka_jobs} 核` : '自動'}
                    </span>
                  </div>
                  {task.config.include_packages && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">強制納入套件</span>
                      <span className="text-gray-300 font-mono text-xs truncate max-w-[160px]">
                        {task.config.include_packages}
                      </span>
                    </div>
                  )}
                </>
              )}

              {/* Frontend settings */}
              {(task.config.project_type === 'frontend_only' || task.config.project_type === 'fullstack') && (
                <>
                  <div className="flex justify-between">
                    <span className="text-gray-500">前端目錄</span>
                    <span className="text-gray-300 font-mono text-xs">{task.config.frontend_dir}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">建置工具</span>
                    <span className="text-gray-300">{task.config.frontend_build_tool}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">建置指令</span>
                    <span className="text-gray-300 font-mono text-xs">{task.config.frontend_build_command}</span>
                  </div>
                  {task.config.frontend_env_content && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">Env 檔</span>
                      <span className="text-gray-300 font-mono text-xs">{task.config.frontend_env_filename}</span>
                    </div>
                  )}
                </>
              )}

              {/* Docker settings */}
              {task.config.docker_enabled && (
                <>
                  {task.config.docker_image_name && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">Docker Image</span>
                      <span className="text-gray-300 font-mono text-xs">{task.config.docker_image_name}</span>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-gray-500">基底 Image</span>
                    <span className="text-gray-300 font-mono text-xs">{task.config.docker_base_image}</span>
                  </div>
                </>
              )}

              <div className="flex justify-between">
                <span className="text-gray-500">建立時間</span>
                <span className="text-gray-300 text-xs">
                  {new Date(task.created_at).toLocaleString()}
                </span>
              </div>
            </div>

            {/* Cancel Button */}
            {isActive && (
              <Button
                danger
                icon={<StopOutlined />}
                onClick={() => cancelMutation.mutate(task.id)}
                loading={cancelMutation.isPending}
                className="w-full mt-6"
                style={{
                  background: 'rgba(239, 68, 68, 0.1)',
                  borderColor: 'rgba(239, 68, 68, 0.3)',
                  color: '#f87171',
                }}
              >
                取消任務
              </Button>
            )}

            {/* Rebuild & Download Buttons */}
            {!isActive && (
              <div className="mt-6 space-y-2">
                <Button
                  icon={<RedoOutlined />}
                  onClick={() =>
                    navigate('/create', {
                      state: {
                        rebuild: true,
                        projectName: task.project_name,
                        config: task.config,
                      },
                    })
                  }
                  className="w-full"
                  style={{
                    background: 'rgba(6, 182, 212, 0.1)',
                    borderColor: 'rgba(6, 182, 212, 0.3)',
                    color: '#f09a5e',
                  }}
                >
                  重新打包
                </Button>
                {task.status === 'completed' && (
                  <Button
                    icon={<DownloadOutlined />}
                    loading={downloading}
                    onClick={async () => {
                      setDownloading(true)
                      try {
                        triggerUrlDownload(await taskApi.getOutputDownloadUrl(task.id))
                        message.success('已開始下載產出')
                      } catch (err: unknown) {
                        message.error(getErrorDetail(err, '下載產出失敗'))
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
              </div>
            )}
          </div>
        </div>

        {/* Right Panel - Logs */}
        <div className="lg:col-span-2">
          <div className="glass-card p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-medium text-gray-400 uppercase tracking-wider">
                建置日誌
              </h3>
              <span className="text-xs text-gray-500 font-mono">
                {task.logs.length} 行
              </span>
            </div>
            {/* The stream can stop for reasons retrying cannot fix (session
                expired, task evicted from memory). Say so — otherwise the log
                panel just silently stops updating. */}
            {wsStoppedReason && (
              <div
                className="mb-3 px-3 py-2 rounded-lg text-xs"
                style={{
                  background: 'rgba(234, 179, 8, 0.08)',
                  border: '1px solid rgba(234, 179, 8, 0.25)',
                  color: '#fde68a',
                }}
              >
                即時日誌已停止：{wsStoppedReason}
              </div>
            )}
            <LogViewer
              logs={task.logs}
              height="calc(100vh - 280px)"
            />
          </div>
        </div>
      </div>
    </div>
  )
}
