import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Card, Empty, Input, Tooltip, message } from 'antd'
import {
  CopyOutlined,
  DownloadOutlined,
  VerticalAlignBottomOutlined,
  PauseOutlined,
} from '@ant-design/icons'

interface LogViewerProps {
  logs: string[]
  title?: string
  height?: number | string
  autoScroll?: boolean
}

// Fixed row height (px) used for manual windowing. Keep rows single-line so the
// height is predictable; the scroll container allows horizontal overflow.
const LINE_HEIGHT = 22
// Number of extra rows rendered above/below the viewport to avoid blank flashes
// while scrolling fast.
const OVERSCAN = 12
// Distance (px) from the bottom that still counts as "pinned to bottom".
const BOTTOM_THRESHOLD = 24

export default function LogViewer({
  logs,
  title = 'Logs',
  height = 400,
  autoScroll = true,
}: LogViewerProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const prevDisplayLenRef = useRef(0)

  const [search, setSearch] = useState('')
  const [scrollTop, setScrollTop] = useState(0)
  const [viewportHeight, setViewportHeight] = useState(0)
  // When true, auto-scroll-to-bottom is disabled (user scrolled up or paused).
  const [paused, setPaused] = useState(!autoScroll)

  // Filtered view of the logs. Filtering (not just highlighting) keeps the
  // windowing math simple and stays fast for very large logs.
  const displayLogs = useMemo(() => {
    const term = search.trim().toLowerCase()
    if (!term) return logs
    return logs.filter((line) => line.toLowerCase().includes(term))
  }, [logs, search])

  const totalHeight = displayLogs.length * LINE_HEIGHT

  // Compute the visible window [startIndex, endIndex).
  const startIndex = Math.max(0, Math.floor(scrollTop / LINE_HEIGHT) - OVERSCAN)
  const endIndex = Math.min(
    displayLogs.length,
    Math.ceil((scrollTop + viewportHeight) / LINE_HEIGHT) + OVERSCAN
  )

  const visibleRows = useMemo(() => {
    const rows: { index: number; text: string }[] = []
    for (let i = startIndex; i < endIndex; i++) {
      rows.push({ index: i, text: displayLogs[i] })
    }
    return rows
  }, [displayLogs, startIndex, endIndex])

  const measure = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    setViewportHeight(el.clientHeight)
    setScrollTop(el.scrollTop)
  }, [])

  // Keep the viewport height in sync with layout / resize.
  useEffect(() => {
    measure()
    const el = scrollRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => measure())
    ro.observe(el)
    return () => ro.disconnect()
  }, [measure])

  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    setScrollTop(el.scrollTop)
    const atBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD
    // Auto-pause when the user scrolls up, auto-resume when back at the bottom.
    setPaused(!atBottom)
  }, [])

  // Auto-scroll to the bottom when new lines arrive (and not paused).
  useEffect(() => {
    const el = scrollRef.current
    if (el && !paused && displayLogs.length > prevDisplayLenRef.current) {
      el.scrollTop = el.scrollHeight
    }
    prevDisplayLenRef.current = displayLogs.length
  }, [displayLogs, paused])

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
    setPaused(false)
  }, [])

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(logs.join('\n'))
      message.success('已複製全部日誌')
    } catch {
      message.error('複製失敗')
    }
  }, [logs])

  const handleDownload = useCallback(() => {
    const blob = new Blob([logs.join('\n')], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const safeTitle = title.replace(/[^\w一-龥-]+/g, '_')
    a.download = `${safeTitle || 'logs'}.txt`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }, [logs, title])

  return (
    <Card
      title={title}
      size="small"
      extra={
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Input.Search
            allowClear
            size="small"
            placeholder="搜尋日誌"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: 180 }}
          />
          <Tooltip title={paused ? '恢復自動捲動' : '暫停自動捲動'}>
            <Button
              size="small"
              type={paused ? 'default' : 'primary'}
              aria-label={paused ? '恢復自動捲動' : '暫停自動捲動'}
              icon={paused ? <VerticalAlignBottomOutlined /> : <PauseOutlined />}
              onClick={() => (paused ? scrollToBottom() : setPaused(true))}
            />
          </Tooltip>
          <Tooltip title="複製全部">
            <Button
              size="small"
              aria-label="複製全部日誌"
              icon={<CopyOutlined />}
              onClick={handleCopy}
            />
          </Tooltip>
          <Tooltip title="下載日誌">
            <Button
              size="small"
              aria-label="下載日誌檔案"
              icon={<DownloadOutlined />}
              onClick={handleDownload}
            />
          </Tooltip>
        </div>
      }
      styles={{
        body: {
          padding: 0,
          height: height,
          overflow: 'hidden',
          background: '#1e1e1e',
        },
      }}
    >
      <div
        ref={scrollRef}
        className="log-viewer"
        style={{ height: '100%', overflow: 'auto', position: 'relative' }}
        onScroll={handleScroll}
      >
        {displayLogs.length === 0 ? (
          <Empty
            description={search.trim() ? '沒有符合的日誌' : '尚無日誌'}
            style={{ padding: 40, color: '#666' }}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : (
          // Spacer preserves the full scroll height; rows are absolutely
          // positioned inside it so only the visible slice is mounted.
          <div style={{ height: totalHeight, position: 'relative' }}>
            {visibleRows.map((row) => (
              <LogRow key={row.index} text={row.text} top={row.index * LINE_HEIGHT} />
            ))}
          </div>
        )}
      </div>
    </Card>
  )
}

interface LogRowProps {
  text: string
  top: number
}

// Memoized so unchanged rows don't re-render on every poll / scroll.
const LogRow = memo(function LogRow({ text, top }: LogRowProps) {
  return (
    <div
      className="log-line"
      style={{
        position: 'absolute',
        top,
        left: 0,
        right: 0,
        height: LINE_HEIGHT,
        lineHeight: `${LINE_HEIGHT}px`,
        whiteSpace: 'pre',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        color: getLogColor(text),
      }}
    >
      {text}
    </div>
  )
})

// Word-boundary / known-prefix matching so a normal line that merely contains
// "error" inside another word isn't miscolored.
function getLogColor(log: string): string {
  if (
    /(^|\W)(ERROR|Error:|failed|Failed|FAILED)(\W|$)/.test(log) ||
    log.includes('✗')
  ) {
    return '#f5222d'
  }
  if (/(^|\W)(WARN|WARNING|Warning)(\W|:|$)/.test(log) || log.includes('⚠')) {
    return '#faad14'
  }
  if (
    /(^|\W)(SUCCESS|COMPLETED?|Complete[d]?)(\W|$)/.test(log) ||
    log.includes('✓')
  ) {
    return '#52c41a'
  }
  if (/^\[[^\]]+\]/.test(log)) {
    return '#1890ff'
  }
  return '#d4d4d4'
}
