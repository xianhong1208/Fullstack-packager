import { useState, useEffect } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { BuildOutlined, UserOutlined, LockOutlined, LoadingOutlined, CheckCircleOutlined } from '@ant-design/icons'
import { useAuth, checkFirstUser } from '../contexts/AuthContext'

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

  useEffect(() => {
    if (isAuthenticated) {
      navigate('/')
    }
  }, [isAuthenticated, navigate])

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
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-cyan-500/20 to-cyan-600/20 border border-cyan-500/30 mb-4">
            <BuildOutlined className="text-3xl text-cyan-400" />
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
                  className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-cyan-500 focus:ring-cyan-500 focus:ring-offset-0"
                />
                <span className="text-sm text-gray-400">記住我</span>
              </label>
              <Link
                to="/forgot-password"
                className="text-sm text-cyan-400 hover:text-cyan-300 transition-colors"
              >
                忘記密碼？
              </Link>
            </div>
          )}

          {/* Pending approval message */}
          {pendingApproval && (
            <div className="p-3 rounded-lg bg-green-500/10 border border-green-500/30 text-green-400 text-sm flex items-start gap-2">
              <CheckCircleOutlined className="mt-0.5 flex-shrink-0" />
              <span>帳號建立成功，請等待管理員審核後即可登入。</span>
            </div>
          )}

          {/* Error message */}
          {displayError && (
            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
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
              className="text-sm text-gray-400 hover:text-cyan-400 transition-colors"
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
