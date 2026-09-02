import { useQuery } from '@tanstack/react-query'
import { useRef, useMemo } from 'react'
import { Spin, Button } from 'antd'
import { ReloadOutlined, ExclamationCircleOutlined } from '@ant-design/icons'
import api from '../api/client'
import { getErrorDetail } from '../utils/errors'

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
  if (pct >= 90) return '#ec5f5f'  // coral
  if (pct >= 70) return '#e6a53a'  // amber
  return '#33c489'                 // mint
}

// A clean industry-style meter: label + rounded track + value.
function Meter({ label, pct, value, color }: { label: string; pct: number; value?: string; color?: string }) {
  const c = color ?? barColor(pct)
  return (
    <div className="flex items-center gap-3">
      <span className="truncate" style={{ width: 52, fontSize: 11, color: 'var(--ink-muted)', fontFamily: 'var(--font-mono)' }}>{label}</span>
      <div style={{ flex: 1, height: 6, borderRadius: 999, background: 'var(--color-void-700)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${Math.min(100, pct)}%`, background: c, borderRadius: 999, transition: 'width 0.3s ease' }} />
      </div>
      <span className="tabular-nums" style={{ minWidth: value ? 138 : 40, textAlign: 'right', fontSize: 11, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>
        {value ?? `${pct.toFixed(0)}%`}
      </span>
    </div>
  )
}

// A KPI tile: big number + thin bar (+ optional sparkline).
function StatTile({ label, pct, sub, spark, showBar = true }: { label: string; pct: number; sub: string; spark?: number[]; showBar?: boolean }) {
  return (
    <div className="stat-card">
      <div className="uppercase tracking-wider" style={{ fontSize: 11, color: 'var(--ink-faint)', fontFamily: 'var(--font-display)' }}>{label}</div>
      {showBar && (
        <div className="mt-1" style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 30, color: 'var(--ink)', lineHeight: 1 }}>
          {pct.toFixed(0)}<span style={{ fontSize: 16, color: 'var(--ink-faint)' }}>%</span>
        </div>
      )}
      {showBar && (
        <div className="mt-3" style={{ height: 6, borderRadius: 999, background: 'var(--color-void-700)', overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${Math.min(100, pct)}%`, background: barColor(pct), borderRadius: 999, transition: 'width 0.3s ease' }} />
        </div>
      )}
      {spark && <div className="mt-2"><Sparkline data={spark} color="#4c8df0" height={30} label={`${label} trend`} /></div>}
      <div className="mt-2 truncate" style={{ fontSize: 11, color: 'var(--ink-muted)', fontFamily: 'var(--font-mono)' }}>{sub}</div>
    </div>
  )
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
  const { overallHistory } = useMemo(() => {
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
        <div style={{ color: '#ef4444', fontWeight: 'bold' }}>Could not load system monitoring data</div>
        <div style={{ color: '#888', maxWidth: 420 }}>{getErrorDetail(error)}</div>
        <Button
          icon={<ReloadOutlined />}
          aria-label="Reload system monitoring data"
          onClick={() => refetch()}
        >
          Retry
        </Button>
      </div>
    )
  }

  if (isLoading || !stats) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spin size="large" tip="Loading system monitoring data…" />
      </div>
    )
  }

  const uptimeLabel = (() => {
    const totalSent = stats.network.bytes_sent
    const totalRecv = stats.network.bytes_recv
    return `Sent ${formatBytes(totalSent)} · Received ${formatBytes(totalRecv)}`
  })()

  return (
    <div>
      {/* Page header */}
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, color: 'var(--ink)' }}>System monitoring</h1>
        <p style={{ margin: '4px 0 0', color: 'var(--ink-muted)' }}>
          Live CPU, memory, disk, GPU and network, refreshed every 2 seconds.
        </p>
      </div>

      {/* KPI tiles */}
      <div className="grid gap-4 mb-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))' }}>
        <StatTile label="CPU" pct={stats.cpu.percent} spark={overallHistory}
          sub={`${stats.cpu.count_physical} cores · ${stats.cpu.count_logical} threads${stats.cpu.frequency_current ? ` · ${(stats.cpu.frequency_current / 1000).toFixed(1)} GHz` : ''}`} />
        <StatTile label="Memory" pct={stats.memory.percent}
          sub={`${formatBytes(stats.memory.used)} / ${formatBytes(stats.memory.total)}`} />
        <StatTile label="Disk" pct={stats.disk.percent}
          sub={`${formatBytes(stats.disk.used)} / ${formatBytes(stats.disk.total)}`} />
        {stats.gpus.length > 0 ? (
          <StatTile label="GPU" pct={stats.gpus[0].load} sub={stats.gpus[0].name} />
        ) : (
          <StatTile label="Network" pct={0} showBar={false}
            sub={`↑ ${formatBytes(netRateRef.current.sent)}/s   ↓ ${formatBytes(netRateRef.current.recv)}/s`} />
        )}
      </div>

      {/* CPU cores + Memory/Disk */}
      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))' }}>
        <section className="glass-card" style={{ padding: '16px 18px' }}>
          <div className="mon-title">CPU cores</div>
          <div className="mb-3">
            <Sparkline data={overallHistory} color="#4c8df0" height={44}
              label={`Overall CPU usage, currently ${stats.cpu.percent.toFixed(1)}%`} />
          </div>
          <div className="grid gap-x-6 gap-y-2" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
            {stats.cpu.percent_per_core.map((pct, i) => (
              <Meter key={i} label={`Core ${i}`} pct={pct} />
            ))}
          </div>
        </section>

        <section className="glass-card" style={{ padding: '16px 18px' }}>
          <div className="mon-title">Memory & storage</div>
          <div className="flex flex-col gap-3">
            <Meter label="RAM" pct={stats.memory.percent}
              value={`${formatBytes(stats.memory.used)} / ${formatBytes(stats.memory.total)}`} />
            {stats.swap.total > 0 && (
              <Meter label="Swap" pct={stats.swap.percent}
                value={`${formatBytes(stats.swap.used)} / ${formatBytes(stats.swap.total)}`} />
            )}
            <Meter label="Disk" pct={stats.disk.percent}
              value={`${formatBytes(stats.disk.used)} / ${formatBytes(stats.disk.total)}`} />
          </div>
        </section>
      </div>

      {/* GPU */}
      {stats.gpus.length > 0 && (
        <section className="glass-card mt-4" style={{ padding: '16px 18px' }}>
          <div className="mon-title">GPU</div>
          <div className="flex flex-col gap-4">
            {stats.gpus.map((gpu) => {
              const tempPct = gpu.temperature > 80 ? 95 : gpu.temperature > 60 ? 75 : 40
              return (
                <div key={gpu.id}>
                  <div className="flex items-center justify-between mb-2">
                    <span style={{ color: 'var(--ink)', fontWeight: 600, fontFamily: 'var(--font-display)' }}>
                      GPU {gpu.id} · {gpu.name}
                    </span>
                    <span className="tabular-nums" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: barColor(tempPct) }}>
                      {gpu.temperature}°C
                    </span>
                  </div>
                  <div className="flex flex-col gap-2">
                    <Meter label="Load" pct={gpu.load} />
                    <Meter label="VRAM" pct={gpu.memory_percent}
                      value={`${formatBytes(gpu.memory_used * 1024 * 1024)} / ${formatBytes(gpu.memory_total * 1024 * 1024)}`} />
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {/* Network */}
      <div className="glass-card mt-4 flex items-center justify-between flex-wrap gap-2"
        style={{ padding: '12px 18px', fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--ink-muted)' }}>
        <span>
          Network{' '}
          <span style={{ color: '#33c489' }}>↑ {formatBytes(netRateRef.current.sent)}/s</span>{'  '}
          <span style={{ color: '#4c8df0' }}>↓ {formatBytes(netRateRef.current.recv)}/s</span>
        </span>
        <span>{uptimeLabel}</span>
      </div>
    </div>
  )
}
