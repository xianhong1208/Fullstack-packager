import { useEffect, useRef, useState } from 'react'
import type { WebSocketMessage } from '../api/types'

interface UseWebSocketOptions {
  taskId?: string
  onMessage?: (message: WebSocketMessage) => void
  autoConnect?: boolean
}

interface UseWebSocketReturn {
  isConnected: boolean
  /** Set when the server refused the connection for a reason retrying cannot
   *  fix; null while the socket is healthy or merely reconnecting. */
  stoppedReason: string | null
  connect: () => void
  disconnect: () => void
  sendPing: () => void
}

// Application close codes sent by app/api/websocket.py.
//
// 4403/4404 are verdicts about this user and this task — retrying produces the
// identical refusal, so looping only wastes connections. 4404 in particular
// fires routinely: a finished task is evicted from the manager's memory after
// FINISHED_TASK_RETENTION_MINUTES, so a tab left open on an old build used to
// reconnect every 30s indefinitely.
const PERMANENT_CLOSE_CODES = new Set([4403, 4404])
// 4401 is different: the access token may simply have expired, and the axios
// layer refreshes it in the background. Give exactly one retry so the socket
// recovers on its own, then stop rather than loop against a dead session.
const AUTH_CLOSE_CODE = 4401
const AUTH_RETRY_DELAY_MS = 3000
const CLOSE_REASONS: Record<number, string> = {
  [AUTH_CLOSE_CODE]: '登入狀態已失效，請重新整理頁面以繼續接收即時日誌',
  4403: '沒有權限接收此任務的即時日誌',
  4404: '此任務已不在即時佇列中（完成後會從記憶體移除），改以歷史紀錄顯示',
}

export function useWebSocket(options: UseWebSocketOptions = {}): UseWebSocketReturn {
  const { taskId, onMessage, autoConnect = true } = options
  const [isConnected, setIsConnected] = useState(false)
  const [stoppedReason, setStoppedReason] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<number | null>(null)
  const pingIntervalRef = useRef<number | null>(null)
  const onMessageRef = useRef(onMessage)
  // Reconnect backoff state: count failures and don't reconnect after an
  // intentional disconnect (unmount / manual).
  const reconnectAttemptsRef = useRef(0)
  const intentionalCloseRef = useRef(false)
  const authRetriedRef = useRef(false)

  // Keep onMessage ref updated
  useEffect(() => {
    onMessageRef.current = onMessage
  }, [onMessage])

  const getWsUrl = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const path = taskId ? `/ws/tasks/${taskId}` : '/ws/tasks'
    // No token in the URL — it's sent via the Sec-WebSocket-Protocol header
    // instead (see connect), so it never lands in access/proxy logs.
    return `${protocol}//${host}${path}`
  }

  const connect = () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return
    }

    intentionalCloseRef.current = false
    setStoppedReason(null)
    // Authenticate via the Sec-WebSocket-Protocol subprotocol: offer
    // ['bearer', <jwt>]; the server validates it and echoes back 'bearer'.
    const token = sessionStorage.getItem('access_token') || localStorage.getItem('access_token')
    const ws = token
      ? new WebSocket(getWsUrl(), ['bearer', token])
      : new WebSocket(getWsUrl())

    ws.onopen = () => {
      setIsConnected(true)
      reconnectAttemptsRef.current = 0 // reset backoff on a healthy connection
      authRetriedRef.current = false
      // Start ping interval to keep connection alive
      pingIntervalRef.current = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send('ping')
        }
      }, 30000)
    }

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as WebSocketMessage
        onMessageRef.current?.(message)
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e)
      }
    }

    ws.onclose = (event) => {
      setIsConnected(false)
      if (pingIntervalRef.current) {
        clearInterval(pingIntervalRef.current)
        pingIntervalRef.current = null
      }
      // Don't reconnect if this close was intentional (unmount / manual).
      if (intentionalCloseRef.current) return
      // Nor if the server gave a verdict that retrying cannot change. The
      // backend closes with 4401 (bad token), 4403 (not your task) and 4404
      // (task not found) — the last one fires routinely once a finished task
      // is evicted from the manager's memory, so a tab left open on an old
      // build used to reconnect every 30s forever.
      if (PERMANENT_CLOSE_CODES.has(event.code)) {
        setStoppedReason(CLOSE_REASONS[event.code] ?? event.reason ?? '連線已被伺服器關閉')
        return
      }
      if (event.code === AUTH_CLOSE_CODE) {
        if (authRetriedRef.current) {
          setStoppedReason(CLOSE_REASONS[AUTH_CLOSE_CODE])
          return
        }
        // The token may have just expired; axios refreshes it on the next
        // poll, so one delayed retry usually reconnects with a fresh one.
        authRetriedRef.current = true
        reconnectTimeoutRef.current = window.setTimeout(connect, AUTH_RETRY_DELAY_MS)
        return
      }
      // Exponential backoff capped at 30s, so a down server isn't hammered
      // every 3s forever. Resets to fast retries once a connection succeeds.
      const attempt = reconnectAttemptsRef.current++
      const delay = Math.min(30000, 1000 * 2 ** attempt) // 1s,2s,4s,…,30s
      reconnectTimeoutRef.current = window.setTimeout(() => {
        connect()
      }, delay)
    }

    ws.onerror = (error) => {
      console.error('WebSocket error:', error)
    }

    wsRef.current = ws
  }

  const disconnect = () => {
    intentionalCloseRef.current = true // suppress the onclose reconnect
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current)
      pingIntervalRef.current = null
    }
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    setIsConnected(false)
  }

  const sendPing = () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send('ping')
    }
  }

  useEffect(() => {
    if (autoConnect) {
      connect()
    }

    return () => {
      disconnect()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, autoConnect])

  return {
    isConnected,
    stoppedReason,
    connect,
    disconnect,
    sendPing,
  }
}
