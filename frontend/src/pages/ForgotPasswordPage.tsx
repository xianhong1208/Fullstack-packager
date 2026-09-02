import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { BuildOutlined, UserOutlined, QuestionCircleOutlined, LockOutlined, LoadingOutlined, ArrowLeftOutlined, CheckCircleOutlined } from '@ant-design/icons'
import { authApi } from '../api/authApi'

type Step = 'username' | 'security_question' | 'reset_password' | 'success'

export default function ForgotPasswordPage() {
  const navigate = useNavigate()

  const [step, setStep] = useState<Step>('username')
  const [username, setUsername] = useState('')
  const [securityQuestion, setSecurityQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [resetToken, setResetToken] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleUsernameSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (!username.trim()) {
      setError('請輸入您的使用者名稱')
      return
    }

    setIsSubmitting(true)
    try {
      const response = await authApi.forgotPassword(username)

      if (response.has_security_question && response.security_question) {
        setSecurityQuestion(response.security_question)
        setStep('security_question')
      } else {
        setError('此帳號尚未設定安全問題，請聯絡管理員。')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '檢查帳號失敗')
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleAnswerSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (!answer.trim()) {
      setError('請輸入您的答案')
      return
    }

    setIsSubmitting(true)
    try {
      const response = await authApi.verifySecurityAnswer(username, answer)
      setResetToken(response.reset_token)
      setStep('reset_password')
    } catch (err) {
      setError(err instanceof Error ? err.message : '答案錯誤')
    } finally {
      setIsSubmitting(false)
    }
  }

  const handlePasswordReset = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (newPassword.length < 6) {
      setError('密碼長度至少需要 6 個字元')
      return
    }

    if (newPassword !== confirmPassword) {
      setError('兩次輸入的密碼不一致')
      return
    }

    setIsSubmitting(true)
    try {
      await authApi.resetPassword(resetToken, newPassword)
      setStep('success')
    } catch (err) {
      setError(err instanceof Error ? err.message : '重設密碼失敗')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="glass-card w-full max-w-md p-8">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-cyan-500/20 to-cyan-600/20 border border-cyan-500/30 mb-4">
            <BuildOutlined className="text-3xl text-cyan-400" />
          </div>
          <h1 className="text-2xl font-semibold text-white" style={{ fontFamily: 'var(--font-display)' }}>
            {step === 'success' ? '密碼已重設' : '忘記密碼'}
          </h1>
          <p className="text-gray-400 mt-2 text-sm">
            {step === 'username' && '輸入您的使用者名稱以復原帳號'}
            {step === 'security_question' && '回答您的安全問題'}
            {step === 'reset_password' && '建立新密碼'}
            {step === 'success' && '您的密碼已成功重設'}
          </p>
        </div>

        {/* Step 1: Username */}
        {step === 'username' && (
          <form onSubmit={handleUsernameSubmit} className="space-y-5">
            <div>
              <label htmlFor="forgot-username" className="block text-sm text-gray-400 mb-2">使用者名稱</label>
              <div className="relative">
                <UserOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 z-10" />
                <input
                  id="forgot-username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="input-field"
                  style={{ paddingLeft: 40 }}
                  placeholder="請輸入您的使用者名稱"
                  autoComplete="username"
                />
              </div>
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={isSubmitting}
              className="btn-cyber w-full flex items-center justify-center gap-2"
            >
              {isSubmitting ? <LoadingOutlined className="animate-spin" /> : null}
              繼續
            </button>
          </form>
        )}

        {/* Step 2: Security Question */}
        {step === 'security_question' && (
          <form onSubmit={handleAnswerSubmit} className="space-y-5">
            <div className="p-4 rounded-lg bg-gray-800/50 border border-gray-700">
              <div className="flex items-start gap-3">
                <QuestionCircleOutlined className="text-cyan-400 text-lg mt-0.5" />
                <p className="text-gray-300">{securityQuestion}</p>
              </div>
            </div>

            <div>
              <label htmlFor="forgot-answer" className="block text-sm text-gray-400 mb-2">您的答案</label>
              <input
                id="forgot-answer"
                type="text"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                className="input-field"
                placeholder="請輸入您的答案"
                autoComplete="off"
              />
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={isSubmitting}
              className="btn-cyber w-full flex items-center justify-center gap-2"
            >
              {isSubmitting ? <LoadingOutlined className="animate-spin" /> : null}
              驗證答案
            </button>
          </form>
        )}

        {/* Step 3: Reset Password */}
        {step === 'reset_password' && (
          <form onSubmit={handlePasswordReset} className="space-y-5">
            <div>
              <label htmlFor="forgot-new-password" className="block text-sm text-gray-400 mb-2">新密碼</label>
              <div className="relative">
                <LockOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 z-10" />
                <input
                  id="forgot-new-password"
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="input-field"
                  style={{ paddingLeft: 40 }}
                  placeholder="請輸入新密碼"
                  autoComplete="new-password"
                />
              </div>
            </div>

            <div>
              <label htmlFor="forgot-confirm-password" className="block text-sm text-gray-400 mb-2">確認密碼</label>
              <div className="relative">
                <LockOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 z-10" />
                <input
                  id="forgot-confirm-password"
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="input-field"
                  style={{ paddingLeft: 40 }}
                  placeholder="請再次輸入新密碼"
                  autoComplete="new-password"
                />
              </div>
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={isSubmitting}
              className="btn-cyber w-full flex items-center justify-center gap-2"
            >
              {isSubmitting ? <LoadingOutlined className="animate-spin" /> : null}
              重設密碼
            </button>
          </form>
        )}

        {/* Step 4: Success */}
        {step === 'success' && (
          <div className="text-center space-y-6">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-green-500/20 border border-green-500/30">
              <CheckCircleOutlined className="text-3xl text-green-400" />
            </div>
            <p className="text-gray-300">
              您的密碼已成功重設，現在可以使用新密碼登入。
            </p>
            <button
              onClick={() => navigate('/login')}
              className="btn-cyber w-full"
            >
              前往登入
            </button>
          </div>
        )}

        {/* Back to login */}
        {step !== 'success' && (
          <div className="mt-6 text-center">
            <Link
              to="/login"
              className="inline-flex items-center gap-2 text-sm text-gray-400 hover:text-cyan-400 transition-colors"
            >
              <ArrowLeftOutlined />
              返回登入
            </Link>
          </div>
        )}
      </div>
    </div>
  )
}
