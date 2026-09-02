import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useIdleTimeout } from './useIdleTimeout'

/**
 * Idle auto-logout.
 *
 * The bug this pins: `onLogout` is passed as an inline arrow by the caller, so
 * it changed on every render. It fed startTimers -> resetTimer -> the main
 * effect, whose cleanup cleared the timers and restarted them. AppLayout
 * re-renders constantly (it subscribes to the task store, which updates on
 * every line of build log), so the 30-minute timer was reset several times a
 * second and the logout could never fire.
 *
 * It failed silently — no error, nothing logged — and was only observable by
 * deliberately sitting idle for half an hour, which is why it survived.
 */
const THIRTY_ONE_MINUTES = 31 * 60 * 1000

describe('useIdleTimeout', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('logs out after the idle period', () => {
    const onLogout = vi.fn()
    renderHook(() => useIdleTimeout({ onLogout, enabled: true }))

    act(() => { vi.advanceTimersByTime(THIRTY_ONE_MINUTES) })

    expect(onLogout).toHaveBeenCalled()
  })

  it('still logs out when the parent re-renders constantly with a new callback', () => {
    // The actual production condition. Each render passes a fresh arrow, as
    // App.tsx does; previously this reset the timer every time and the logout
    // never happened.
    let renders = 0
    const logout = vi.fn()
    const { rerender } = renderHook(() =>
      useIdleTimeout({ onLogout: () => { renders++; logout() }, enabled: true }),
    )

    act(() => {
      for (let i = 0; i < 60; i++) {
        vi.advanceTimersByTime(30_000)   // half a minute between renders
        rerender()                        // new inline callback each time
      }
    })

    expect(logout).toHaveBeenCalled()
    expect(renders).toBeGreaterThan(0)
  })

  it('calls the latest callback, not the one captured at mount', () => {
    // Consequence of holding it in a ref: staleness would be the obvious way
    // to get this wrong, logging out through a closure over an old navigate.
    const first = vi.fn()
    const second = vi.fn()
    const { rerender } = renderHook(
      ({ cb }) => useIdleTimeout({ onLogout: cb, enabled: true }),
      { initialProps: { cb: first } },
    )

    rerender({ cb: second })
    act(() => { vi.advanceTimersByTime(THIRTY_ONE_MINUTES) })

    expect(second).toHaveBeenCalled()
    expect(first).not.toHaveBeenCalled()
  })

  it('does not log out while the user is active', () => {
    const onLogout = vi.fn()
    const { result } = renderHook(() => useIdleTimeout({ onLogout, enabled: true }))

    act(() => {
      for (let i = 0; i < 10; i++) {
        vi.advanceTimersByTime(5 * 60 * 1000)  // 5 min
        result.current.resetTimer()
      }
    })

    expect(onLogout).not.toHaveBeenCalled()
  })

  it('shows the warning before logging out', () => {
    const onLogout = vi.fn()
    const { result } = renderHook(() => useIdleTimeout({ onLogout, enabled: true }))

    act(() => { vi.advanceTimersByTime(26 * 60 * 1000) })

    expect(result.current.showWarning).toBe(true)
    expect(onLogout).not.toHaveBeenCalled()
  })

  it('does nothing when disabled', () => {
    const onLogout = vi.fn()
    renderHook(() => useIdleTimeout({ onLogout, enabled: false }))

    act(() => { vi.advanceTimersByTime(THIRTY_ONE_MINUTES) })

    expect(onLogout).not.toHaveBeenCalled()
  })
})
