import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'

/**
 * Tests for the single-flight token refresh.
 * Source: src/api/client.refreshTokens
 *
 * The backend rotates on every refresh and revokes the token it was handed,
 * so two callers that each read the stored value a moment apart cannot both
 * succeed. There were exactly two — the 401 interceptor and AuthContext's
 * expiry timer — each with its own in-progress flag and no knowledge of the
 * other, so the one that finished second presented a just-revoked token, took
 * a 401, and logged the user out while their new token sat in storage.
 *
 * Nothing about that is visible in a passing build: it needs two refreshes to
 * overlap, which happens when a request 401s near the two-minute mark.
 */

const post = vi.fn()

vi.mock('axios', () => {
  const instance = () => ({ create: () => ({ post, interceptors: intercept() }) })
  const intercept = () => ({
    request: { use: vi.fn() },
    response: { use: vi.fn() },
  })
  return { default: { ...instance(), create: () => ({ post, interceptors: intercept() }) } }
})

let refreshTokens: typeof import('./client')['refreshTokens']
let TOKENS_REFRESHED_EVENT: string

/** One rotation's worth of server response. */
function rotation(n: number, expiresIn = 900) {
  return {
    data: {
      access_token: `access-${n}`,
      refresh_token: `refresh-${n}`,
      expires_in: expiresIn,
    },
  }
}

/** A response that only settles when the returned release() is called. */
function deferred() {
  let release!: (value: unknown) => void
  const promise = new Promise((resolve) => {
    release = resolve
  })
  return { promise, release }
}

describe('refreshTokens', () => {
  beforeEach(async () => {
    vi.resetModules()
    post.mockReset()
    localStorage.clear()
    sessionStorage.clear()
    sessionStorage.setItem('refresh_token', 'refresh-0')
    const mod = await import('./client')
    refreshTokens = mod.refreshTokens
    TOKENS_REFRESHED_EVENT = mod.TOKENS_REFRESHED_EVENT
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('rotates once when two callers overlap', async () => {
    const gate = deferred()
    post.mockReturnValueOnce(gate.promise)

    const first = refreshTokens()
    const second = refreshTokens()
    gate.release(rotation(1))

    expect(await first).toBe('access-1')
    expect(await second).toBe('access-1')
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('hands both callers the same promise, not two equal results', async () => {
    post.mockReturnValueOnce(deferred().promise)
    expect(refreshTokens()).toBe(refreshTokens())
  })

  it('sends the stored refresh token', async () => {
    post.mockResolvedValueOnce(rotation(1))
    await refreshTokens()
    expect(post).toHaveBeenCalledWith('/refresh', { refresh_token: 'refresh-0' })
  })

  it('stores the rotated pair so the next call sends the new one', async () => {
    post.mockResolvedValueOnce(rotation(1)).mockResolvedValueOnce(rotation(2))

    await refreshTokens()
    await refreshTokens()

    expect(post).toHaveBeenNthCalledWith(2, '/refresh', { refresh_token: 'refresh-1' })
    expect(sessionStorage.getItem('access_token')).toBe('access-2')
    expect(sessionStorage.getItem('refresh_token')).toBe('refresh-2')
  })

  it('allows a fresh attempt after one completes', async () => {
    post.mockResolvedValueOnce(rotation(1)).mockResolvedValueOnce(rotation(2))

    expect(await refreshTokens()).toBe('access-1')
    expect(await refreshTokens()).toBe('access-2')
    expect(post).toHaveBeenCalledTimes(2)
  })

  it('releases the flight after a failure so a later attempt can run', async () => {
    // A rejected attempt that stayed latched would leave every subsequent
    // caller awaiting a promise that already failed — one bad response and
    // the session could never recover.
    post.mockRejectedValueOnce(new Error('401')).mockResolvedValueOnce(rotation(2))

    await expect(refreshTokens()).rejects.toThrow('401')
    expect(await refreshTokens()).toBe('access-2')
  })

  it('rejects both overlapping callers when the rotation fails', async () => {
    const gate = deferred()
    post.mockReturnValueOnce(gate.promise.then(() => Promise.reject(new Error('401'))))

    const first = refreshTokens()
    const second = refreshTokens()
    gate.release(null)

    await expect(first).rejects.toThrow('401')
    await expect(second).rejects.toThrow('401')
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('refuses without a stored refresh token instead of calling the endpoint', async () => {
    sessionStorage.clear()
    await expect(refreshTokens()).rejects.toThrow(/refresh token/i)
    expect(post).not.toHaveBeenCalled()
  })

  it('announces the new expiry so the timer can be re-armed', async () => {
    post.mockResolvedValueOnce(rotation(1, 600))
    const seen: number[] = []
    window.addEventListener(TOKENS_REFRESHED_EVENT, (e) => {
      seen.push((e as CustomEvent<{ expiresIn: number }>).detail.expiresIn)
    })

    await refreshTokens()

    expect(seen).toEqual([600])
  })

  it('keeps a remembered session in localStorage', async () => {
    // The storage a session started in is the one it stays in: a refresh token
    // written to localStorage for a "don't remember me" login would outlive
    // the browser session it was supposed to die with.
    sessionStorage.clear()
    localStorage.setItem('refresh_token', 'refresh-0')
    post.mockResolvedValueOnce(rotation(1))

    await refreshTokens()

    expect(localStorage.getItem('refresh_token')).toBe('refresh-1')
    expect(sessionStorage.getItem('refresh_token')).toBeNull()
  })
})
