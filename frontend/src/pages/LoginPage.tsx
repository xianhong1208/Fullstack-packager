import { useState, useEffect } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { BuildOutlined, UserOutlined, LockOutlined, LoadingOutlined, CheckCircleOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { useAuth, checkFirstUser, tokenManager } from '../contexts/AuthContext'

export default function LoginPage() {
  const navigate = useNavigate()
  const { login, register, isAuthenticated, error } = useAuth()

  const [isRegisterMode, setIsRegisterMode] = useState(false)
  const [isFirstUser, setIsFirstUser] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [rememberMe, setRememberMe] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const [pendingApproval, setPendingApproval] = useState(false)
  const [ssoEnabled, setSsoEnabled] = useState(false)

  useEffect(() => {
    if (isAuthenticated) {
      navigate('/')
    }
  }, [isAuthenticated, navigate])

  // MCP Center SSO: consume tokens handed back in the URL fragment, surface errors,
  // and decide whether to show the "Sign in with MCP Center" button.
  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.slice(1))
    const accessToken = hash.get('access_token')
    if (accessToken) {
      tokenManager.setTokens(
        accessToken,
        hash.get('refresh_token'),
        Number(hash.get('expires_in') || 900),
        false,
      )
      window.location.replace('/')
      return
    }
    const params = new URLSearchParams(window.location.search)
    const ssoError = params.get('sso_error')
    if (ssoError) {
      setLocalError(
        ssoError === 'account_pending_approval'
          ? 'Account created — an administrator must approve it before you can sign in with MCP Center.'
          : `MCP Center sign-in failed: ${ssoError}`,
      )
    }
    fetch('/auth/oauth/mcp/status')
      .then((r) => (r.ok ? r.json() : { enabled: false }))
      .then((d) => setSsoEnabled(!!d.enabled))
      .catch(() => setSsoEnabled(false))
  }, [])

  useEffect(() => {
    checkFirstUser().then((isFirst) => {
      setIsFirstUser(isFirst)
      if (isFirst) {
        setIsRegisterMode(true)
      }
    })
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLocalError(null)
    setPendingApproval(false)

    if (!username || !password) {
      setLocalError('Enter both your username and password.')
      return
    }

    if (isRegisterMode && password !== confirmPassword) {
      setLocalError('The passwords do not match. Re-enter them.')
      return
    }

    if (password.length < 4) {
      setLocalError('Your password must be at least 4 characters.')
      return
    }

    setIsSubmitting(true)
    try {
      if (isRegisterMode) {
        const result = await register(username, password)
        if (result.pendingApproval) {
          setPendingApproval(true)
          setIsRegisterMode(false)
          setPassword('')
          setConfirmPassword('')
          return
        }
      } else {
        await login(username, password, rememberMe)
      }
      navigate('/')
    } catch {
      // Error is handled by AuthContext
    } finally {
      setIsSubmitting(false)
    }
  }

  const displayError = localError || error

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="glass-card w-full max-w-md p-8">
        {/* Brand */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-lg bg-cyber-500 mb-4">
            <BuildOutlined className="text-3xl text-void-950" />
          </div>
          <h1 className="text-2xl font-semibold text-[var(--ink)]" style={{ fontFamily: 'var(--font-display)' }}>
            Build Center
          </h1>
          <p className="mt-2 text-sm text-[var(--ink-muted)]">
            {isFirstUser
              ? 'Create your administrator account to get started'
              : isRegisterMode
                ? 'Create a new account'
                : 'Sign in to your account'
            }
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Username */}
          <div>
            <label htmlFor="login-username" className="block text-sm text-[var(--ink-muted)] mb-2" style={{ fontFamily: 'var(--font-display)' }}>
              Username
            </label>
            <div className="relative">
              <UserOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--ink-faint)] z-10" />
              <input
                id="login-username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="input-field"
                style={{ paddingLeft: 40 }}
                placeholder="Enter your username"
                autoComplete="username"
              />
            </div>
          </div>

          {/* Password */}
          <div>
            <label htmlFor="login-password" className="block text-sm text-[var(--ink-muted)] mb-2" style={{ fontFamily: 'var(--font-display)' }}>
              Password
            </label>
            <div className="relative">
              <LockOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--ink-faint)] z-10" />
              <input
                id="login-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input-field"
                style={{ paddingLeft: 40 }}
                placeholder="Enter your password"
                autoComplete={isRegisterMode ? 'new-password' : 'current-password'}
              />
            </div>
          </div>

          {/* Confirm Password (Register mode only) */}
          {isRegisterMode && (
            <div>
              <label htmlFor="login-confirm-password" className="block text-sm text-[var(--ink-muted)] mb-2" style={{ fontFamily: 'var(--font-display)' }}>
                Confirm password
              </label>
              <div className="relative">
                <LockOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--ink-faint)] z-10" />
                <input
                  id="login-confirm-password"
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="input-field"
                  style={{ paddingLeft: 40 }}
                  placeholder="Re-enter your password"
                  autoComplete="new-password"
                />
              </div>
            </div>
          )}

          {/* Remember Me & Forgot Password (Login mode only) */}
          {!isRegisterMode && (
            <div className="flex items-center justify-between">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="w-4 h-4 rounded border-void-600 bg-void-800 text-cyber-500 focus:ring-cyber-500 focus:ring-offset-0"
                />
                <span className="text-sm text-[var(--ink-muted)]">Remember me</span>
              </label>
              <Link
                to="/forgot-password"
                className="text-sm text-cyber-400 hover:text-cyber-300 transition-colors rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyber-500/60"
              >
                Forgot password?
              </Link>
            </div>
          )}

          {/* Pending approval message */}
          {pendingApproval && (
            <div className="p-3 rounded-lg bg-matrix-500/10 border border-matrix-500/30 text-matrix-400 text-sm flex items-start gap-2">
              <CheckCircleOutlined className="mt-0.5 flex-shrink-0" />
              <span>Account created — an administrator must approve it before you can sign in.</span>
            </div>
          )}

          {/* Error message */}
          {displayError && (
            <div className="p-3 rounded-lg bg-alert-500/10 border border-alert-500/30 text-alert-400 text-sm">
              {displayError}
            </div>
          )}

          {/* Submit button */}
          <button
            type="submit"
            disabled={isSubmitting}
            className="btn-cyber w-full flex items-center justify-center gap-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyber-300"
          >
            {isSubmitting ? (
              <>
                <LoadingOutlined className="animate-spin" />
                {isRegisterMode ? 'Creating account…' : 'Signing in…'}
              </>
            ) : (
              isRegisterMode ? 'Create account' : 'Sign in'
            )}
          </button>
        </form>

        {/* MCP Center single sign-on (only when enabled on the server) */}
        {ssoEnabled && !isRegisterMode && (
          <div className="mt-6">
            <div className="flex items-center gap-3 my-4">
              <div className="flex-1 h-px bg-void-700" />
              <span className="text-xs uppercase tracking-wider text-[var(--ink-faint)]">or</span>
              <div className="flex-1 h-px bg-void-700" />
            </div>
            <a
              href="/auth/oauth/mcp/login"
              className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg border border-void-600 text-[var(--ink)] hover:border-cyber-500 hover:text-cyber-300 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyber-500/60"
            >
              <SafetyCertificateOutlined />
              Sign in with MCP Center
            </a>
          </div>
        )}

        {/* Toggle mode */}
        {!isFirstUser && (
          <div className="mt-6 text-center">
            <button
              type="button"
              onClick={() => {
                setIsRegisterMode(!isRegisterMode)
                setLocalError(null)
                setPendingApproval(false)
              }}
              className="text-sm text-[var(--ink-muted)] hover:text-cyber-400 transition-colors rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyber-500/60"
            >
              {isRegisterMode
                ? 'Already have an account? Sign in'
                : "Don't have an account? Register"
              }
            </button>
          </div>
        )}

        {/* Footer */}
        <div className="mt-8 pt-6 border-t border-[var(--seam)] text-center">
          <p className="text-xs text-[var(--ink-faint)]">
            Build Center
          </p>
        </div>
      </div>
    </div>
  )
}
