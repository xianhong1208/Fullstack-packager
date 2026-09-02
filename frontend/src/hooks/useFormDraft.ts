import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormInstance } from 'antd'

const DRAFT_STORAGE_KEY = 'createTaskDraft'
const SAVE_DEBOUNCE_MS = 800

/**
 * Fields that must never reach localStorage.
 *
 * `frontend_env_content` is a paste of the project's real .env — in practice
 * it carries JWT signing keys, database passwords and third-party API keys.
 * localStorage is readable by any script on the origin and survives logout, so
 * a convenience feature would otherwise turn the browser into a durable copy
 * of other systems' credentials.
 */
const NEVER_PERSIST = ['frontend_env_content'] as const

export interface FormDraft {
  /** Timestamp of the last successful save, for a "draft saved" indicator. */
  savedAt: number | null
  /** Debounced save; safe to call on every keystroke. */
  scheduleSave: () => void
  /** Discard the stored draft and cancel any pending save. */
  clear: () => void
}

/**
 * Persist a long form to localStorage so an accidental navigation or an idle
 * logout does not throw the work away.
 *
 * Disabled entirely when `isRebuild` is true: that form is seeded from an
 * existing task, and restoring a stale draft over it would silently rebuild
 * something other than what the user asked to rebuild.
 */
export function useFormDraft(
  form: FormInstance,
  isRebuild: boolean,
  onCleared?: () => void,
): FormDraft {
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const restoredRef = useRef(false)

  const scheduleSave = useCallback(() => {
    if (isRebuild) return
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      try {
        const values = { ...form.getFieldsValue(true) } as Record<string, unknown>
        for (const field of NEVER_PERSIST) delete values[field]
        localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(values))
        setSavedAt(Date.now())
      } catch {
        // Quota exceeded or an unserialisable value — losing a draft is not
        // worth interrupting the user over.
      }
    }, SAVE_DEBOUNCE_MS)
  }, [isRebuild, form])

  const clear = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    try {
      localStorage.removeItem(DRAFT_STORAGE_KEY)
    } catch {
      /* ignore */
    }
    setSavedAt(null)
    onCleared?.()
  }, [onCleared])

  // Restore once, on mount, for new tasks only.
  useEffect(() => {
    if (isRebuild) return
    if (restoredRef.current) return
    restoredRef.current = true
    try {
      const raw = localStorage.getItem(DRAFT_STORAGE_KEY)
      if (!raw) return
      const draft = JSON.parse(raw)
      if (draft && typeof draft === 'object') {
        form.setFieldsValue(draft)
        setSavedAt(Date.now())
      }
    } catch {
      // A malformed draft must not block the form from loading.
    }
  }, [isRebuild, form])

  // Cancel a pending save on unmount so it cannot fire against a dead form.
  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    },
    [],
  )

  return { savedAt, scheduleSave, clear }
}

export const __testing = { DRAFT_STORAGE_KEY, NEVER_PERSIST, SAVE_DEBOUNCE_MS }
