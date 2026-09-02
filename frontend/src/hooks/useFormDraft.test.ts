import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useFormDraft, __testing } from './useFormDraft'

const { DRAFT_STORAGE_KEY, SAVE_DEBOUNCE_MS } = __testing

/** Minimal antd FormInstance stand-in — the hook only uses two methods. */
function fakeForm(values: Record<string, unknown> = {}) {
  const state = { ...values }
  return {
    getFieldsValue: () => ({ ...state }),
    setFieldsValue: (v: Record<string, unknown>) => Object.assign(state, v),
    __state: state,
  } as never as import('antd').FormInstance & { __state: Record<string, unknown> }
}

function storedDraft(): Record<string, unknown> | null {
  const raw = localStorage.getItem(DRAFT_STORAGE_KEY)
  return raw ? JSON.parse(raw) : null
}

describe('useFormDraft', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('saves field values after the debounce window', () => {
    const form = fakeForm({ project_name: 'token-server', python_version: 'auto' })
    const { result } = renderHook(() => useFormDraft(form, false))

    act(() => result.current.scheduleSave())
    expect(storedDraft()).toBeNull() // nothing written yet — still debouncing

    act(() => vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))
    expect(storedDraft()).toEqual({ project_name: 'token-server', python_version: 'auto' })
  })

  it('NEVER persists the env content', () => {
    // The reason this hook exists as a tested unit. That field is a paste of
    // the project's real .env — JWT keys, DB passwords, API keys — and
    // localStorage is readable by any script on the origin and outlives logout.
    const form = fakeForm({
      project_name: 'x',
      frontend_env_content: 'JWT_SECRET_KEY=super-secret\nDB_PASSWORD=hunter2',
    })
    const { result } = renderHook(() => useFormDraft(form, false))

    act(() => result.current.scheduleSave())
    act(() => vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))

    const draft = storedDraft()!
    expect(draft).not.toHaveProperty('frontend_env_content')
    expect(JSON.stringify(draft)).not.toContain('super-secret')
    expect(JSON.stringify(draft)).not.toContain('hunter2')
    expect(draft.project_name).toBe('x') // other fields still saved
  })

  it('collapses rapid keystrokes into one write', () => {
    const form = fakeForm({ project_name: 'abc' })
    const { result } = renderHook(() => useFormDraft(form, false))

    act(() => {
      result.current.scheduleSave()
      vi.advanceTimersByTime(100)
      result.current.scheduleSave()
      vi.advanceTimersByTime(100)
      result.current.scheduleSave()
    })
    act(() => vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))

    expect(storedDraft()).toEqual({ project_name: 'abc' })
  })

  it('restores a saved draft on mount', () => {
    localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ project_name: 'restored' }))
    const form = fakeForm()

    const { result } = renderHook(() => useFormDraft(form, false))

    expect(form.__state.project_name).toBe('restored')
    expect(result.current.savedAt).not.toBeNull()
  })

  it('does nothing at all in rebuild mode', () => {
    // A rebuild form is seeded from the original task. Restoring a stale draft
    // over it would rebuild something other than what was asked for, and
    // saving would overwrite the user's real draft with rebuild values.
    localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ project_name: 'stale-draft' }))
    const form = fakeForm({ project_name: 'from-original-task' })

    const { result } = renderHook(() => useFormDraft(form, true))
    act(() => result.current.scheduleSave())
    act(() => vi.advanceTimersByTime(SAVE_DEBOUNCE_MS))

    expect(form.__state.project_name).toBe('from-original-task')
    expect(storedDraft()).toEqual({ project_name: 'stale-draft' }) // untouched
  })

  it('survives a malformed draft instead of blocking the form', () => {
    localStorage.setItem(DRAFT_STORAGE_KEY, '{not json')
    const form = fakeForm({ project_name: 'kept' })

    expect(() => renderHook(() => useFormDraft(form, false))).not.toThrow()
    expect(form.__state.project_name).toBe('kept')
  })

  it('clear() removes the draft and notifies the caller', () => {
    localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ a: 1 }))
    const onCleared = vi.fn()
    const { result } = renderHook(() => useFormDraft(fakeForm(), false, onCleared))

    act(() => result.current.clear())

    expect(storedDraft()).toBeNull()
    expect(result.current.savedAt).toBeNull()
    expect(onCleared).toHaveBeenCalledOnce()
  })

  it('does not write after unmount', () => {
    // The debounce timer outlives the component otherwise, firing against a
    // form that no longer exists.
    const form = fakeForm({ project_name: 'x' })
    const { result, unmount } = renderHook(() => useFormDraft(form, false))

    act(() => result.current.scheduleSave())
    unmount()
    act(() => vi.advanceTimersByTime(SAVE_DEBOUNCE_MS * 2))

    expect(storedDraft()).toBeNull()
  })
})
