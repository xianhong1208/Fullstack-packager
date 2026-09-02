import { useQuery } from '@tanstack/react-query'
import { useRef, useMemo } from 'react'
import { Spin, Button } from 'antd'
import { ReloadOutlined, ExclamationCircleOutlined } from '@ant-design/icons'
import api from '../api/client'
import { getErrorDetail } from '../utils/errors'

// ── htop-style color scheme ──
const CORE_COLORS = [
  '#06b6d4', '#8b5cf6', '#f59e0b', '#10b981',
  '#ef4444', '#3b82f6', '#ec4899', '#14b8a6',
  '#f97316', '#6366f1', '#84cc16', '#a855f7',
  '#e11d48', '#0ea5e9', '#22c55e', '#d946ef',
]

const MAX_HISTORY = 60

interface SystemStats {
  cpu: {
    percent: number
    percent_per_core: number[]
    count_physical: number
    count_logical: number
    frequency_current: number | null
    frequency_max: number | null
  }
  memory: {
    total: number
    available: number
    used: number
    percent: number
  }
  swap: {
    total: number
    used: number
    percent: number
  }
  disk: {
    total: number
    used: number
    free: number
    percent: number
  }
  network: {
    bytes_sent: number
    bytes_recv: number
  }
  gpus: Array<{
    id: number
    name: string
    load: number
    memory_used: number
    memory_total: number
    memory_percent: number
    temperature: number
  }>
}

const formatBytes = (bytes: number): string => {
  if (bytes === 0) return '0B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(1)}${sizes[i]}`
}

const barColor = (pct: number): string => {
  if (pct >= 90) return '#ef4444'
  if (pct >= 70) return '#f59e0b'
  return '#10b981'
}

// Non-color-only status indicator: color is paired with a text label + glyph
// so the load level is legible without relying on color perception.
const loadStatus = (pct: number): { label: string; color: string; high: boolean; glyph: string } => {
  if (pct >= 90) return { label: '過高', color: '#ef4444', high: true, glyph: '▲' }
  if (pct >= 70) return { label: '偏高', color: '#f59e0b', high: true, glyph: '△' }
  return { label: '正常', color: '#10b981', high: false, glyph: '●' }
}

// Generate htop-style block characters for a bar
const htopBar = (pct: number, width: number, color: string): { filled: number; chars: string; color: string } => {
  const filled = Math.round((pct / 100) * width)
  const chars = '|'.repeat(filled) + ' '.repeat(Math.max(0, width - filled))
  return { filled, chars, color }
}

// Mini sparkline from history array
function Sparkline({ data, color, height = 32, label }: { data: number[]; color: string; height?: number; label: string }) {
  if (data.length < 2) return null
  const max = 100
  const w = 200
  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w
    const y = height - (v / max) * height
    return `${x},${y}`
  })
  const areaPoints = `0,${height} ${points.join(' ')} ${w},${height}`
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${w} ${height}`}
      style={{ width: '100%', height }}
      preserveAspectRatio="none"
    >
      <title>{label}</title>
      <polygon points={areaPoints} fill={color} opacity={0.15} />
      <polyline points={points.join(' ')} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  )
}

export default function MonitoringPage() {
  const cpuHistoryRef = useRef<number[][]>([]) // per-core history
  const overallHistoryRef = useRef<number[]>([])
  const prevNetRef = useRef<{ sent: number; recv: number } | null>(null)
  const netRateRef = useRef<{ sent: number; recv: number }>({ sent: 0, recv: 0 })

  const { data: stats, isLoading, isError, error, refetch } = useQuery<SystemStats>({
    queryKey: ['systemStats'],
    queryFn: async () => {
      const response = await api.get('/monitoring/stats')
      return response.data
    },
    refetchInterval: 2000,
  })

  // Build history
  const { cpuHistory, overallHistory } = useMemo(() => {
    if (!stats) return { cpuHistory: [] as number[][], overallHistory: [] as number[] }

    // Overall
    overallHistoryRef.current.push(stats.cpu.percent)
    if (overallHistoryRef.current.length > MAX_HISTORY) overallHistoryRef.current.shift()

    // Per-core
    while (cpuHistoryRef.current.length < stats.cpu.percent_per_core.length) {
      cpuHistoryRef.current.push([])
    }
    stats.cpu.percent_per_core.forEach((p, i) => {
      cpuHistoryRef.current[i].push(p)
      if (cpuHistoryRef.current[i].length > MAX_HISTORY) cpuHistoryRef.current[i].shift()
    })

    // Network rate
    if (prevNetRef.current) {
      netRateRef.current = {
        sent: Math.max(0, (stats.network.bytes_sent - prevNetRef.current.sent) / 2),
        recv: Math.max(0, (stats.network.bytes_recv - prevNetRef.current.recv) / 2),
      }
    }
    prevNetRef.current = { sent: stats.network.bytes_sent, recv: stats.network.bytes_recv }

    return {
      cpuHistory: cpuHistoryRef.current.map(h => [...h]),
      overallHistory: [...overallHistoryRef.current],
    }
  }, [stats])

  // Error state: without this the page would spin forever if /monitoring/stats fails.
  if (isError && !stats) {
    return (
      <div
        className="flex flex-col items-center justify-center h-64 gap-3"
        style={{
          color: '#c8c8c8',
          background: '#0a0e14',
          borderRadius: 8,
          border: '1px solid #1a1f2e',
          padding: '24px 16px',
          textAlign: 'center',
        }}
      >
        <ExclamationCircleOutlined style={{ fontSize: 40, color: '#ef4444' }} aria-hidden="true" />
        <div style={{ color: '#ef4444', fontWeight: 'bold' }}>無法載入系統監控資料</div>
        <div style={{ color: '#888', maxWidth: 420 }}>{getErrorDetail(error)}</div>
        <Button
          icon={<ReloadOutlined />}
          aria-label="重新載入系統監控資料"
          onClick={() => refetch()}
        >
          重試
        </Button>
      </div>
    )
  }

  if (isLoading || !stats) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spin size="large" tip="載入系統監控資料中…" />
      </div>
    )
  }

  const BAR_WIDTH = 30
  const coreCount = stats.cpu.percent_per_core.length
  // Split cores into two columns
  const half = Math.ceil(coreCount / 2)
  const leftCores = stats.cpu.percent_per_core.slice(0, half)
  const rightCores = stats.cpu.percent_per_core.slice(half)

  const uptimeLabel = (() => {
    const totalSent = stats.network.bytes_sent
    const totalRecv = stats.network.bytes_recv
    return `累計傳送 ${formatBytes(totalSent)}｜接收 ${formatBytes(totalRecv)}`
  })()

  return (
    <div
      style={{
        fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace",
        fontSize: 13,
        lineHeight: 1.5,
        color: '#c8c8c8',
        background: '#0a0e14',
        borderRadius: 8,
        border: '1px solid #1a1f2e',
        padding: '12px 16px',
        minHeight: '80vh',
      }}
    >
      {/* ── Header ── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, borderBottom: '1px solid #1a2030', paddingBottom: 6 }}>
        <span style={{ color: '#06b6d4', fontWeight: 'bold' }}>系統監控</span>
        <span style={{ color: '#555' }}>更新頻率：2 秒 | {new Date().toLocaleTimeString()}</span>
      </div>

      {/* ── CPU Meters (htop-style, 2 columns) ── */}
      <div style={{ display: 'flex', gap: 24, marginBottom: 12 }}>
        {/* Left column */}
        <div style={{ flex: 1 }}>
          {leftCores.map((pct, i) => {
            const bar = htopBar(pct, BAR_WIDTH, CORE_COLORS[i % CORE_COLORS.length])
            const st = loadStatus(pct)
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 4, height: 18 }}>
                <span style={{ color: '#555', width: 28, textAlign: 'right' }}>{i}</span>
                <span style={{ color: '#333' }}>[</span>
                <span style={{ color: bar.color, letterSpacing: -1 }}>
                  {bar.chars.split('').map((c, ci) => (
                    <span key={ci} style={{ color: ci < bar.filled ? bar.color : '#1a1f2e' }}>{c === ' ' ? ' ' : '|'}</span>
                  ))}
                </span>
                <span style={{ color: '#333' }}>]</span>
                <span style={{ color: barColor(pct), width: 42, textAlign: 'right' }}>
                  {pct.toFixed(1)}%
                </span>
                <span style={{ color: st.color, width: 44, fontSize: 11 }} title={`核心 ${i} 負載${st.label}`}>
                  {st.glyph}{st.high ? st.label : ''}
                </span>
              </div>
            )
          })}
        </div>
        {/* Right column */}
        <div style={{ flex: 1 }}>
          {rightCores.map((pct, origI) => {
            const i = half + origI
            const bar = htopBar(pct, BAR_WIDTH, CORE_COLORS[i % CORE_COLORS.length])
            const st = loadStatus(pct)
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 4, height: 18 }}>
                <span style={{ color: '#555', width: 28, textAlign: 'right' }}>{i}</span>
                <span style={{ color: '#333' }}>[</span>
                <span style={{ color: bar.color, letterSpacing: -1 }}>
                  {bar.chars.split('').map((c, ci) => (
                    <span key={ci} style={{ color: ci < bar.filled ? bar.color : '#1a1f2e' }}>{c === ' ' ? ' ' : '|'}</span>
                  ))}
                </span>
                <span style={{ color: '#333' }}>]</span>
                <span style={{ color: barColor(pct), width: 42, textAlign: 'right' }}>
                  {pct.toFixed(1)}%
                </span>
                <span style={{ color: st.color, width: 44, fontSize: 11 }} title={`核心 ${i} 負載${st.label}`}>
                  {st.glyph}{st.high ? st.label : ''}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      {/* ── Memory / Swap / Disk Meters ── */}
      <div style={{ marginBottom: 12 }}>
        {/* Memory */}
        {(() => {
          const pct = stats.memory.percent
          const bar = htopBar(pct, BAR_WIDTH * 2 + 10, barColor(pct))
          const st = loadStatus(pct)
          return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 4, height: 20 }}>
              <span style={{ color: '#10b981', width: 48 }}>記憶體</span>
              <span style={{ color: '#333' }}>[</span>
              <span style={{ letterSpacing: -1 }}>
                {bar.chars.split('').map((c, ci) => (
                  <span key={ci} style={{ color: ci < bar.filled ? bar.color : '#1a1f2e' }}>{c === ' ' ? ' ' : '|'}</span>
                ))}
              </span>
              <span style={{ color: '#333' }}>]</span>
              <span style={{ color: barColor(pct) }}>
                {formatBytes(stats.memory.used)}/{formatBytes(stats.memory.total)}
              </span>
              <span style={{ color: '#555' }}>({pct.toFixed(1)}%)</span>
              <span style={{ color: st.color }} title={`記憶體負載${st.label}`}>{st.glyph} {st.label}</span>
            </div>
          )
        })()}

        {/* Swap */}
        {stats.swap.total > 0 && (() => {
          const pct = stats.swap.percent
          const bar = htopBar(pct, BAR_WIDTH * 2 + 10, barColor(pct))
          const st = loadStatus(pct)
          return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 4, height: 20 }}>
              <span style={{ color: '#f59e0b', width: 48 }}>置換</span>
              <span style={{ color: '#333' }}>[</span>
              <span style={{ letterSpacing: -1 }}>
                {bar.chars.split('').map((c, ci) => (
                  <span key={ci} style={{ color: ci < bar.filled ? bar.color : '#1a1f2e' }}>{c === ' ' ? ' ' : '|'}</span>
                ))}
              </span>
              <span style={{ color: '#333' }}>]</span>
              <span style={{ color: barColor(pct) }}>
                {formatBytes(stats.swap.used)}/{formatBytes(stats.swap.total)}
              </span>
              <span style={{ color: '#555' }}>({pct.toFixed(1)}%)</span>
              <span style={{ color: st.color }} title={`置換空間負載${st.label}`}>{st.glyph} {st.label}</span>
            </div>
          )
        })()}

        {/* Disk */}
        {(() => {
          const pct = stats.disk.percent
          const bar = htopBar(pct, BAR_WIDTH * 2 + 10, barColor(pct))
          const st = loadStatus(pct)
          return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 4, height: 20 }}>
              <span style={{ color: '#8b5cf6', width: 48 }}>磁碟</span>
              <span style={{ color: '#333' }}>[</span>
              <span style={{ letterSpacing: -1 }}>
                {bar.chars.split('').map((c, ci) => (
                  <span key={ci} style={{ color: ci < bar.filled ? bar.color : '#1a1f2e' }}>{c === ' ' ? ' ' : '|'}</span>
                ))}
              </span>
              <span style={{ color: '#333' }}>]</span>
              <span style={{ color: barColor(pct) }}>
                {formatBytes(stats.disk.used)}/{formatBytes(stats.disk.total)}
              </span>
              <span style={{ color: '#555' }}>({pct.toFixed(1)}%)</span>
              <span style={{ color: st.color }} title={`磁碟負載${st.label}`}>{st.glyph} {st.label}</span>
            </div>
          )
        })()}
      </div>

      {/* ── GPU Section ── */}
      {stats.gpus.length > 0 && (
        <div style={{ marginBottom: 12, borderTop: '1px solid #1a2030', paddingTop: 8 }}>
          {stats.gpus.map((gpu) => {
            const loadBar = htopBar(gpu.load, BAR_WIDTH + 5, barColor(gpu.load))
            const memBar = htopBar(gpu.memory_percent, BAR_WIDTH + 5, barColor(gpu.memory_percent))
            const loadSt = loadStatus(gpu.load)
            const memSt = loadStatus(gpu.memory_percent)
            const tempColor = gpu.temperature > 80 ? '#ef4444' : gpu.temperature > 60 ? '#f59e0b' : '#10b981'
            const tempStatus = gpu.temperature > 80 ? { glyph: '▲', label: '過熱' } : gpu.temperature > 60 ? { glyph: '△', label: '偏高' } : { glyph: '●', label: '正常' }
            return (
              <div key={gpu.id}>
                <div style={{ color: '#06b6d4', marginBottom: 2 }}>GPU{gpu.id}：{gpu.name}</div>
                <div style={{ display: 'flex', gap: 24 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, height: 20 }}>
                    <span style={{ color: '#555', width: 40 }}>負載</span>
                    <span style={{ color: '#333' }}>[</span>
                    <span style={{ letterSpacing: -1 }}>
                      {loadBar.chars.split('').map((c, ci) => (
                        <span key={ci} style={{ color: ci < loadBar.filled ? loadBar.color : '#1a1f2e' }}>{c === ' ' ? ' ' : '|'}</span>
                      ))}
                    </span>
                    <span style={{ color: '#333' }}>]</span>
                    <span style={{ color: barColor(gpu.load) }}>{gpu.load.toFixed(1)}%</span>
                    <span style={{ color: loadSt.color }} title={`GPU 負載${loadSt.label}`}>{loadSt.glyph} {loadSt.label}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, height: 20 }}>
                    <span style={{ color: '#555', width: 40 }}>VRAM</span>
                    <span style={{ color: '#333' }}>[</span>
                    <span style={{ letterSpacing: -1 }}>
                      {memBar.chars.split('').map((c, ci) => (
                        <span key={ci} style={{ color: ci < memBar.filled ? memBar.color : '#1a1f2e' }}>{c === ' ' ? ' ' : '|'}</span>
                      ))}
                    </span>
                    <span style={{ color: '#333' }}>]</span>
                    <span style={{ color: barColor(gpu.memory_percent) }}>
                      {formatBytes(gpu.memory_used * 1024 * 1024)}/{formatBytes(gpu.memory_total * 1024 * 1024)}
                    </span>
                    <span style={{ color: memSt.color }} title={`顯示記憶體負載${memSt.label}`}>{memSt.glyph} {memSt.label}</span>
                  </div>
                  <span style={{ color: tempColor }} title={`GPU 溫度${tempStatus.label}`}>{tempStatus.glyph} {gpu.temperature}°C（{tempStatus.label}）</span>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* ── Sparkline Charts ── */}
      <div style={{ borderTop: '1px solid #1a2030', paddingTop: 8, marginBottom: 12 }}>
        <div style={{ color: '#555', marginBottom: 4 }}>
          CPU 整體使用率（{stats.cpu.percent.toFixed(1)}%）— 實體核心 {stats.cpu.count_physical} / 邏輯核心 {stats.cpu.count_logical}
          {stats.cpu.frequency_current ? `　@ ${stats.cpu.frequency_current}MHz` : ''}
        </div>
        <div style={{ background: '#0d1117', borderRadius: 4, padding: '4px 8px', marginBottom: 8 }}>
          <Sparkline
            data={overallHistory}
            color="#06b6d4"
            height={40}
            label={`CPU 整體使用率趨勢圖，目前 ${stats.cpu.percent.toFixed(1)}%`}
          />
        </div>

        {/* Per-core mini sparklines in grid */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 6 }}>
          {cpuHistory.map((hist, i) => {
            const st = loadStatus(stats.cpu.percent_per_core[i])
            return (
              <div key={i} style={{ background: '#0d1117', borderRadius: 4, padding: '2px 6px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                  <span style={{ color: CORE_COLORS[i % CORE_COLORS.length] }}>核心 {i}</span>
                  <span style={{ color: barColor(stats.cpu.percent_per_core[i]) }} title={`核心 ${i} 負載${st.label}`}>
                    {st.glyph} {stats.cpu.percent_per_core[i].toFixed(1)}%
                  </span>
                </div>
                <Sparkline
                  data={hist}
                  color={CORE_COLORS[i % CORE_COLORS.length]}
                  height={24}
                  label={`核心 ${i} 使用率趨勢圖，目前 ${stats.cpu.percent_per_core[i].toFixed(1)}%`}
                />
              </div>
            )
          })}
        </div>
      </div>

      {/* ── Footer: Network ── */}
      <div style={{ borderTop: '1px solid #1a2030', paddingTop: 6, display: 'flex', justifyContent: 'space-between', color: '#555' }}>
        <span>
          網路：<span style={{ color: '#10b981' }}>▲ 上傳 {formatBytes(netRateRef.current.sent)}/s</span>
          {' '}
          <span style={{ color: '#06b6d4' }}>▼ 下載 {formatBytes(netRateRef.current.recv)}/s</span>
        </span>
        <span>{uptimeLabel}</span>
      </div>
    </div>
  )
}
