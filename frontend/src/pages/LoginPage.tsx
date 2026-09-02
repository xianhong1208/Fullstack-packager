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
          ? '帳號已建立，請等待管理員審核後再使用 MCP Center 登入。'
          : `MCP Center 登入失敗:${ssoError}`,
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
      setLocalError('請填寫所有欄位')
      return
    }

    if (isRegisterMode && password !== confirmPassword) {
      setLocalError('兩次輸入的密碼不一致')
      return
    }

    if (password.length < 4) {
      setLocalError('密碼長度至少需要 4 個字元')
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
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-md bg-gradient-to-br from-cyber-500/20 to-cyber-600/20 border border-cyber-500/30 mb-4">
            <BuildOutlined className="text-3xl text-cyber-400" />
          </div>
          <h1 className="text-2xl font-semibold text-white" style={{ fontFamily: 'var(--font-display)' }}>
            Build Center
          </h1>
          <p className="text-gray-400 mt-2 text-sm">
            {isFirstUser
              ? '建立您的管理員帳號以開始使用'
              : isRegisterMode
                ? '建立新帳號'
                : '登入您的帳號'
            }
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Username */}
          <div>
            <label htmlFor="login-username" className="block text-sm text-gray-400 mb-2" style={{ fontFamily: 'var(--font-display)' }}>
              使用者名稱
            </label>
            <div className="relative">
              <UserOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 z-10" />
              <input
                id="login-username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="input-field"
                style={{ paddingLeft: 40 }}
                placeholder="請輸入使用者名稱"
                autoComplete="username"
              />
            </div>
          </div>

          {/* Password */}
          <div>
            <label htmlFor="login-password" className="block text-sm text-gray-400 mb-2" style={{ fontFamily: 'var(--font-display)' }}>
              密碼
            </label>
            <div className="relative">
              <LockOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 z-10" />
              <input
                id="login-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input-field"
                style={{ paddingLeft: 40 }}
                placeholder="請輸入密碼"
                autoComplete={isRegisterMode ? 'new-password' : 'current-password'}
              />
            </div>
          </div>

          {/* Confirm Password (Register mode only) */}
          {isRegisterMode && (
            <div>
              <label htmlFor="login-confirm-password" className="block text-sm text-gray-400 mb-2" style={{ fontFamily: 'var(--font-display)' }}>
                確認密碼
              </label>
              <div className="relative">
                <LockOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 z-10" />
                <input
                  id="login-confirm-password"
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="input-field"
                  style={{ paddingLeft: 40 }}
                  placeholder="請再次輸入密碼"
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
                  className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-cyber-500 focus:ring-cyber-500 focus:ring-offset-0"
                />
                <span className="text-sm text-gray-400">記住我</span>
              </label>
              <Link
                to="/forgot-password"
                className="text-sm text-cyber-400 hover:text-cyber-300 transition-colors"
              >
                忘記密碼？
              </Link>
            </div>
          )}

          {/* Pending approval message */}
          {pendingApproval && (
            <div className="p-3 rounded-lg bg-matrix-500/10 border border-matrix-500/30 text-matrix-400 text-sm flex items-start gap-2">
              <CheckCircleOutlined className="mt-0.5 flex-shrink-0" />
              <span>帳號建立成功，請等待管理員審核後即可登入。</span>
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
            className="btn-cyber w-full flex items-center justify-center gap-2"
          >
            {isSubmitting ? (
              <>
                <LoadingOutlined className="animate-spin" />
                {isRegisterMode ? '正在建立帳號...' : '正在登入...'}
              </>
            ) : (
              isRegisterMode ? '建立帳號' : '登入'
            )}
          </button>
        </form>

        {/* MCP Center single sign-on (only when enabled on the server) */}
        {ssoEnabled && !isRegisterMode && (
          <div className="mt-4">
            <div className="flex items-center gap-3 my-4">
              <div className="flex-1 h-px bg-gray-700/60" />
              <span className="text-xs text-gray-500">或</span>
              <div className="flex-1 h-px bg-gray-700/60" />
            </div>
            <a
              href="/auth/oauth/mcp/login"
              className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg border border-gray-600 text-gray-200 hover:border-cyber-500 hover:text-cyber-300 transition-colors"
            >
              <SafetyCertificateOutlined />
              使用 MCP Center 登入
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
              className="text-sm text-gray-400 hover:text-cyber-400 transition-colors"
            >
              {isRegisterMode
                ? '已經有帳號了？登入'
                : '還沒有帳號？註冊'
              }
            </button>
          </div>
        )}

        {/* Footer */}
        <div className="mt-8 pt-6 border-t border-gray-700/50 text-center">
          <p className="text-xs text-gray-500">
            Build Center
          </p>
        </div>
      </div>
    </div>
  )
}
