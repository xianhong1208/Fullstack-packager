import { useMemo } from 'react'
import { Tooltip } from 'antd'
import { WarningOutlined } from '@ant-design/icons'
import type { BuildStats } from '../api/types'

/** Health thresholds for the success-rate readout. Kept explicit rather than
 *  a gradient so the same number always reads the same way to everyone. */
const RATE_OK = 90
const RATE_WARN = 70

function rateColor(rate: number | null): string {
  if (rate === null) return '#94a3b8'
  if (rate >= RATE_OK) return '#56d6a1'
  if (rate >= RATE_WARN) return '#f0bd5e'
  return '#f27d7d'
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

function Stat({
  label,
  value,
  hint,
  color = '#e5e7eb',
}: {
  label: string
  value: string
  hint?: string
  color?: string
}) {
  const body = (
    <div className="glass-card p-4" style={{ minWidth: 0 }}>
      <div className="text-xs text-gray-500 uppercase tracking-wider truncate">{label}</div>
      <div className="text-2xl font-semibold mt-1" style={{ color, fontFamily: 'var(--font-display)' }}>
        {value}
      </div>
      {hint && <div className="text-xs text-gray-500 mt-1 truncate">{hint}</div>}
    </div>
  )
  return hint ? <Tooltip title={hint}>{body}</Tooltip> : body
}

/** Daily success rate as an inline SVG sparkline.
 *
 *  Hand-drawn rather than pulling in a charting library: the bundle already
 *  trips Vite's 500 kB warning, and this needs one polyline. Days with no
 *  builds are skipped instead of plotted as 0% — a quiet day is not a bad day,
 *  and drawing it as one would make the trend line lie.
 */
function Sparkline({ points }: { points: { day: string; rate: number }[] }) {
  const W = 100
  const H = 28
  const path = useMemo(() => {
    if (points.length < 2) return null
    const step = W / (points.length - 1)
    return points.map((p, i) => `${i * step},${H - (p.rate / 100) * H}`).join(' ')
  }, [points])

  if (!path) return <div className="text-xs text-gray-600">資料點不足</div>

  const last = points[points.length - 1]
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: 40 }}>
      <line x1="0" y1={H - (RATE_OK / 100) * H} x2={W} y2={H - (RATE_OK / 100) * H}
            stroke="#22c55e" strokeWidth="0.3" strokeDasharray="2 2" opacity="0.4" />
      <polyline points={path} fill="none" stroke={rateColor(last.rate)} strokeWidth="1.2"
                vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  )
}

/** Colour for a single build outcome. Shared by the run strip and the
 *  "latest result" dot so the same outcome never reads two different ways. */
const STATUS_COLOR: Record<string, string> = {
  completed: '#56d6a1',
  failed: '#f27d7d',
  cancelled: '#94a3b8',
  running: '#6ba6f7',
  pending: '#64748b',
}
const STATUS_LABEL: Record<string, string> = {
  completed: '成功',
  failed: '失敗',
  cancelled: '已取消',
  running: '進行中',
  pending: '排隊中',
}

function relativeTime(iso: string | null): string {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return '剛剛'
  if (m < 60) return `${m} 分鐘前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小時前`
  return `${Math.floor(h / 24)} 天前`
}

/** Last N outcomes as a strip, newest on the left.
 *
 *  A success percentage cannot distinguish "broken until yesterday, fixed now"
 *  from "fails every other run" — both can read 50%. The strip shows which. */
function RunStrip({ recent }: { recent: string[] }) {
  return (
    <div className="flex gap-1 items-center">
      {recent.map((s, i) => (
        <Tooltip key={i} title={`${i === 0 ? '最近一次' : `${i} 次前`}：${STATUS_LABEL[s] ?? s}`}>
          <span
            style={{
              width: 8,
              height: 14,
              borderRadius: 2,
              background: STATUS_COLOR[s] ?? '#475569',
              opacity: i === 0 ? 1 : 0.75,
              display: 'inline-block',
            }}
          />
        </Tooltip>
      ))}
    </div>
  )
}

export default function BuildStatsPanel({ stats }: { stats: BuildStats }) {
  const { summary, environment, daily, projects, top_failures } = stats

  const sparkPoints = useMemo(
    () =>
      daily
        .filter((d) => d.success_rate !== null)
        .map((d) => ({ day: d.day, rate: d.success_rate as number })),
    [daily],
  )

  return (
    <div className="mb-6">
      {/* The environment banner is first and loud on purpose. A compile target
          whose venv stopped resolving produces no error anywhere — builds just
          silently fall back to the service's own Python and hand users a
          binary that breaks on their machine. */}
      {environment.degraded.length > 0 && (
        <div
          className="glass-card p-4 mb-4"
          style={{ borderLeft: '3px solid #f27d7d', background: 'rgba(239,68,68,0.06)' }}
        >
          <div className="flex items-center gap-2 mb-1">
            <WarningOutlined style={{ color: '#f27d7d' }} />
            <span className="text-sm font-medium text-alert-400">
              編譯環境降級：Python {environment.degraded.join('、')} 無法使用
            </span>
          </div>
          <div className="text-xs text-gray-400">
            這些版本的 venv 直譯器連結已失效，選用時會**靜默退回**服務自己的 Python{' '}
            {environment.service_python}，產出將在執行期出現 ModuleNotFoundError。
            請在伺服器上執行 <code className="text-cyber-300">bash scripts/setup_nuitka_venvs.sh</code> 重建。
          </div>
          {environment.targets
            .filter((t) => !t.usable && t.target)
            .map((t) => (
              <div key={t.venv} className="text-xs text-gray-600 mt-1 font-mono break-all">
                {t.venv} → {t.target}
              </div>
            ))}
        </div>
      )}

      <div className="grid gap-4 mb-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
        <Stat
          label={`成功率 / ${stats.window_days} 天`}
          value={summary.success_rate === null ? '—' : `${summary.success_rate}%`}
          color={rateColor(summary.success_rate)}
          hint={`${summary.completed} 成功 / ${summary.failed} 失敗 / ${summary.cancelled} 取消`}
        />
        <Stat label="建置次數" value={String(summary.total)} />
        <Stat label="平均耗時" value={formatDuration(summary.avg_duration_seconds)} hint="僅計成功的建置" />
        <Stat
          label="診斷覆蓋率"
          value={stats.diagnosis_coverage === null ? '—' : `${stats.diagnosis_coverage}%`}
          color={rateColor(stats.diagnosis_coverage)}
          hint="失敗中有自動診斷的比例。偏低代表使用者只能盲目重試"
        />
      </div>

      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
        <div className="glass-card p-4">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">每日成功率</div>
          <Sparkline points={sparkPoints} />
          <div className="flex justify-between text-xs text-gray-600 mt-1">
            <span>{sparkPoints[0]?.day ?? ''}</span>
            <span>{sparkPoints[sparkPoints.length - 1]?.day ?? ''}</span>
          </div>
        </div>

        <div className="glass-card p-4">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">失敗原因</div>
          {top_failures.length === 0 ? (
            <div className="text-xs text-gray-600">沒有帶診斷的失敗紀錄</div>
          ) : (
            <div className="space-y-1.5">
              {top_failures.slice(0, 5).map((f) => (
                <div key={f.problem} className="flex gap-2 text-sm">
                  <span className="font-mono text-xs text-gray-500 shrink-0 mt-0.5">{f.count}×</span>
                  <span className="text-gray-300 leading-snug">{f.problem}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Per-project health. Sorted by most recent activity rather than by
          failure count: the question when opening the dashboard is usually
          "how did my last build go", and that answer must be at the top. */}
      {projects.length > 0 && (
        <div className="glass-card p-4 mt-4">
          <div className="flex items-baseline justify-between mb-3">
            <div className="text-xs text-gray-500 uppercase tracking-wider">
              專案狀態（近 {stats.window_days} 天）
            </div>
            <div className="text-xs text-gray-600">最近 12 次結果，新 → 舊</div>
          </div>

          <div style={{ maxHeight: 320, overflowY: 'auto' }}>
            {projects.map((p) => (
              <div
                key={p.project_name}
                className="flex items-center gap-3 py-2"
                style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}
              >
                <Tooltip title={`最近一次：${STATUS_LABEL[p.last_status] ?? p.last_status}`}>
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background: STATUS_COLOR[p.last_status] ?? '#475569',
                      flexShrink: 0,
                    }}
                  />
                </Tooltip>

                <div className="flex-1 min-w-0">
                  <div className="text-sm text-gray-200 truncate" title={p.project_name}>
                    {p.project_name}
                  </div>
                  <div className="text-xs text-gray-600">{relativeTime(p.last_run)}</div>
                </div>

                <RunStrip recent={p.recent} />

                <div className="text-right shrink-0" style={{ width: 84 }}>
                  <div className="text-sm font-mono" style={{ color: rateColor(p.success_rate) }}>
                    {p.success_rate === null ? '—' : `${p.success_rate}%`}
                  </div>
                  <div className="text-xs text-gray-600 font-mono">
                    {p.completed}/{p.total}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
