import type {
  ForgotPasswordResponse,
  LoginHistoryList,
  SecurityQuestionSet,
  SessionList,
} from './types'

const API_BASE = '/auth'

// Get access token from storage
const getAccessToken = () =>
  sessionStorage.getItem('access_token') || localStorage.getItem('access_token')

// Helper for authenticated requests
async function authFetch<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getAccessToken()
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }))
    throw new Error(error.detail || 'Request failed')
  }

  return response.json()
}

export const authApi = {
  // ===== Security Question =====

  setSecurityQuestion: async (data: SecurityQuestionSet): Promise<{ message: string }> => {
    return authFetch('/security-question', {
      method: 'PUT',
      body: JSON.stringify(data),
    })
  },

  // ===== Forgot Password Flow =====

  forgotPassword: async (username: string): Promise<ForgotPasswordResponse> => {
    return authFetch('/forgot-password', {
      method: 'POST',
      body: JSON.stringify({ username }),
    })
  },

  verifySecurityAnswer: async (
    username: string,
    answer: string
  ): Promise<{ reset_token: string; message: string }> => {
    return authFetch('/verify-security-answer', {
      method: 'POST',
      body: JSON.stringify({ username, answer }),
    })
  },

  resetPassword: async (token: string, newPassword: string): Promise<{ message: string }> => {
    return authFetch('/reset-password', {
      method: 'POST',
      body: JSON.stringify({ token, new_password: newPassword }),
    })
  },

  // ===== Password Change =====

  changePassword: async (
    currentPassword: string,
    newPassword: string
  ): Promise<{ message: string }> => {
    return authFetch('/change-password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    })
  },

  // ===== Login History =====

  getLoginHistory: async (limit = 50, offset = 0): Promise<LoginHistoryList> => {
    return authFetch(`/login-history?limit=${limit}&offset=${offset}`)
  },

  // ===== Active Sessions =====

  getActiveSessions: async (): Promise<SessionList> => {
    return authFetch('/active-sessions')
  },

  revokeSession: async (sessionId: number): Promise<{ message: string }> => {
    return authFetch(`/sessions/${sessionId}`, {
      method: 'DELETE',
    })
  },
}

export default authApi
