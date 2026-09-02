import { useEffect, useRef, useState, useCallback } from 'react'

const IDLE_TIMEOUT_MS = 30 * 60 * 1000       // 30 minutes
const WARNING_BEFORE_MS = 5 * 60 * 1000       // Show warning 5 minutes before logout
const ACTIVITY_THROTTLE_MS = 30 * 1000        // Throttle activity detection to every 30 seconds

const ACTIVITY_EVENTS: (keyof DocumentEventMap)[] = [
  'mousemove',
  'mousedown',
  'keydown',
  'scroll',
  'touchstart',
]

interface UseIdleTimeoutOptions {
  onLogout: () => void
  enabled?: boolean
}

interface UseIdleTimeoutReturn {
  showWarning: boolean
  remainingSeconds: number
  resetTimer: () => void
}

export function useIdleTimeout({ onLogout, enabled = true }: UseIdleTimeoutOptions): UseIdleTimeoutReturn {
  const [showWarning, setShowWarning] = useState(false)
  const [remainingSeconds, setRemainingSeconds] = useState(0)

  // Hold the callback in a ref so it never enters a dependency array.
  //
  // Callers pass an inline arrow (App.tsx does), so `onLogout` is a new
  // reference on every render. It fed startTimers -> resetTimer -> the main
  // effect, whose cleanup cleared the timers and immediately restarted them.
  // AppLayout re-renders constantly — it subscribes to the task store, which
  // updates on every line of build log — so the 30-minute timer was reset
  // several times a second and the auto-logout could never fire. Silent
  // failure: no error, nothing in the console, and only visible to someone who
  // deliberately sat idle for half an hour.
  const onLogoutRef = useRef(onLogout)
  useEffect(() => {
    onLogoutRef.current = onLogout
  }, [onLogout])

  const lastActivityRef = useRef(Date.now())
  const logoutTimerRef = useRef<number | null>(null)
  const warningTimerRef = useRef<number | null>(null)
  const countdownRef = useRef<number | null>(null)
  const throttleRef = useRef(false)

  const clearAllTimers = useCallback(() => {
    if (logoutTimerRef.current) {
      clearTimeout(logoutTimerRef.current)
      logoutTimerRef.current = null
    }
    if (warningTimerRef.current) {
      clearTimeout(warningTimerRef.current)
      warningTimerRef.current = null
    }
    if (countdownRef.current) {
      clearInterval(countdownRef.current)
      countdownRef.current = null
    }
  }, [])

  const startTimers = useCallback(() => {
    clearAllTimers()

    // Warning timer: fires at (IDLE_TIMEOUT - WARNING_BEFORE) ms
    warningTimerRef.current = window.setTimeout(() => {
      setShowWarning(true)
      setRemainingSeconds(Math.floor(WARNING_BEFORE_MS / 1000))

      // Start countdown
      countdownRef.current = window.setInterval(() => {
        setRemainingSeconds((prev) => {
          if (prev <= 1) {
            if (countdownRef.current) clearInterval(countdownRef.current)
            return 0
          }
          return prev - 1
        })
      }, 1000)
    }, IDLE_TIMEOUT_MS - WARNING_BEFORE_MS)

    // Logout timer: fires at IDLE_TIMEOUT ms
    logoutTimerRef.current = window.setTimeout(() => {
      clearAllTimers()
      setShowWarning(false)
      onLogoutRef.current()
    }, IDLE_TIMEOUT_MS)
  }, [clearAllTimers])

  const resetTimer = useCallback(() => {
    lastActivityRef.current = Date.now()
    setShowWarning(false)
    setRemainingSeconds(0)
    startTimers()
  }, [startTimers])

  // Handle user activity
  useEffect(() => {
    if (!enabled) {
      clearAllTimers()
      return
    }

    const handleActivity = () => {
      // Throttle to avoid excessive timer resets
      if (throttleRef.current) return
      throttleRef.current = true
      setTimeout(() => { throttleRef.current = false }, ACTIVITY_THROTTLE_MS)

      resetTimer()
    }

    // Start initial timers
    startTimers()

    // Register activity listeners
    ACTIVITY_EVENTS.forEach((event) => {
      document.addEventListener(event, handleActivity, { passive: true })
    })

    return () => {
      clearAllTimers()
      ACTIVITY_EVENTS.forEach((event) => {
        document.removeEventListener(event, handleActivity)
      })
    }
    // Only `enabled` — the three callbacks are now stable, and listing
    // them again would reintroduce the restart loop the moment one of
    // them gains an unstable dependency.
  }, [enabled])

  return { showWarning, remainingSeconds, resetTimer }
}
