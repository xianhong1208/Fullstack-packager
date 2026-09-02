import { describe, it, expect } from 'vitest'
import { AxiosError } from 'axios'
import { getErrorDetail } from './errors'

/**
 * Every error toast in the app goes through here, so what it drops is what the
 * user never learns. The backend puts the actionable text in `detail` — the
 * failure mode this guards against is falling back to a generic message while
 * a specific one was sitting in the response.
 */
function axiosErrorWith(data: unknown, message = 'Request failed'): AxiosError {
  const err = new AxiosError(message)
  // Minimal shape: getErrorDetail only reads response.data.detail.
  err.response = { data, status: 400, statusText: '', headers: {}, config: {} as never }
  return err
}

describe('getErrorDetail', () => {
  it('prefers the backend detail over the generic fallback', () => {
    const err = axiosErrorWith({ detail: 'The download link expired. Go back to Build Center and click download again.' })
    expect(getErrorDetail(err, 'Download failed')).toContain('expired')
  })

  it('unwraps pydantic validation errors', () => {
    // FastAPI returns detail as a list for request-validation failures; showing
    // "[object Object]" there would hide which field was wrong.
    const err = axiosErrorWith({ detail: [{ msg: 'git_url is required', loc: ['body', 'git_url'] }] })
    expect(getErrorDetail(err)).toBe('git_url is required')
  })

  it('falls back to the axios message when detail is absent', () => {
    const err = axiosErrorWith({}, 'Network Error')
    expect(getErrorDetail(err)).toBe('Network Error')
  })

  it('ignores a blank detail rather than showing an empty toast', () => {
    const err = axiosErrorWith({ detail: '   ' }, 'Request failed')
    expect(getErrorDetail(err)).toBe('Request failed')
  })

  it('handles a plain Error', () => {
    expect(getErrorDetail(new Error('boom'))).toBe('boom')
  })

  it('uses the caller fallback for values that are not errors at all', () => {
    // Rejected promises can carry anything; a toast still has to say something.
    expect(getErrorDetail(undefined, 'Could not download the output')).toBe('Could not download the output')
    expect(getErrorDetail({ weird: true }, 'Could not download the output')).toBe('Could not download the output')
    expect(getErrorDetail('a string', 'Could not download the output')).toBe('Could not download the output')
  })

  it('has a default fallback so a call site cannot produce an empty message', () => {
    expect(getErrorDetail(null)).toBeTruthy()
  })
})
