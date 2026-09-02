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
    <div className="stat-card" style={{ minWidth: 0 }}>
      <div className="text-xs uppercase tracking-wider truncate" style={{ color: 'var(--ink-faint)', fontFamily: 'var(--font-mono)' }}>{label}</div>
      <div className="text-2xl font-semibold mt-1" style={{ color, fontFamily: 'var(--font-display)' }}>
        {value}
      </div>
      {hint && <div className="text-xs mt-1 truncate" style={{ color: 'var(--ink-faint)' }}>{hint}</div>}
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

  if (!path) return <div className="text-xs" style={{ color: 'var(--ink-faint)' }}>Not enough data</div>

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
  completed: 'Passed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  running: 'Running',
  pending: 'Queued',
}

function relativeTime(iso: string | null): string {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

/** Last N outcomes as a strip, newest on the left.
 *
 *  A success percentage cannot distinguish "broken until yesterday, fixed now"
 *  from "fails every other run" — both can read 50%. The strip shows which. */
function RunStrip({ recent }: { recent: string[] }) {
  return (
    <div className="flex gap-1 items-center">
      {recent.map((s, i) => (
        <Tooltip key={i} title={`${i === 0 ? 'Latest' : `${i} runs ago`}: ${STATUS_LABEL[s] ?? s}`}>
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
              Build environment degraded: Python {environment.degraded.join(', ')} is unavailable
            </span>
          </div>
          <div className="text-xs" style={{ color: 'var(--ink-muted)' }}>
            The venv interpreter links for these versions no longer resolve. Selecting one silently falls back to the
            service&apos;s own Python {environment.service_python}, and the output will fail with ModuleNotFoundError at
            runtime. Run <code className="text-cyber-300">bash scripts/setup_nuitka_venvs.sh</code> on the server to rebuild them.
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
          label={`Success rate / ${stats.window_days}d`}
          value={summary.success_rate === null ? '—' : `${summary.success_rate}%`}
          color={rateColor(summary.success_rate)}
          hint={`${summary.completed} passed / ${summary.failed} failed / ${summary.cancelled} cancelled`}
        />
        <Stat label="Total builds" value={String(summary.total)} />
        <Stat label="Avg duration" value={formatDuration(summary.avg_duration_seconds)} hint="Successful builds only" />
        <Stat
          label="Diagnosis coverage"
          value={stats.diagnosis_coverage === null ? '—' : `${stats.diagnosis_coverage}%`}
          color={rateColor(stats.diagnosis_coverage)}
          hint="Share of failures with an automatic diagnosis. Low means users can only retry blind."
        />
      </div>

      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
        <div className="glass-card p-4">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">Daily success rate</div>
          <Sparkline points={sparkPoints} />
          <div className="flex justify-between text-xs text-gray-600 mt-1">
            <span>{sparkPoints[0]?.day ?? ''}</span>
            <span>{sparkPoints[sparkPoints.length - 1]?.day ?? ''}</span>
          </div>
        </div>

        <div className="glass-card p-4">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">Top failures</div>
          {top_failures.length === 0 ? (
            <div className="text-xs text-gray-600">No diagnosed failures</div>
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
              Projects (last {stats.window_days}d)
            </div>
            <div className="text-xs text-gray-600">Last 12 results, new → old</div>
          </div>

          <div style={{ maxHeight: 320, overflowY: 'auto' }}>
            {projects.map((p) => (
              <div
                key={p.project_name}
                className="flex items-center gap-3 py-2"
                style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}
              >
                <Tooltip title={`Latest: ${STATUS_LABEL[p.last_status] ?? p.last_status}`}>
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
