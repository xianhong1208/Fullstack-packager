import { AxiosError } from 'axios'

/**
 * Extract a human-readable message from an API error, preferring the
 * backend's `detail` field (FastAPI HTTPException) over a generic string.
 *
 * Standardizes error surfacing across the app — previously some call sites
 * showed the backend detail (download, git ops) while others showed only a
 * hard-coded "Failed to load X", hiding the real cause.
 */
export function getErrorDetail(err: unknown, fallback = 'Something went wrong. Please try again.'): string {
  if (err instanceof AxiosError) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string' && detail.trim()) return detail
    // Some endpoints return {detail: [{msg: ...}]} (pydantic validation)
    if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg)
    if (err.message) return err.message
  }
  if (err instanceof Error && err.message) return err.message
  return fallback
}
