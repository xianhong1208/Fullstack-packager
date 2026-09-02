import { useState, useEffect } from 'react'
import { message, Modal, Table, Tag, Tabs, Empty, Popconfirm } from 'antd'
import {
  LockOutlined,
  QuestionCircleOutlined,
  HistoryOutlined,
  DesktopOutlined,
  LoadingOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  MobileOutlined,
  TabletOutlined,
  DeleteOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons'
import { authApi } from '../api/authApi'
import type { LoginHistoryItem, Session } from '../api/types'
import { useAuth } from '../contexts/AuthContext'

export default function SecuritySettingsPage() {
  const { user, logoutAllDevices } = useAuth()

  // Change Password State
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [isChangingPassword, setIsChangingPassword] = useState(false)

  // Security Question State
  const [securityQuestion, setSecurityQuestion] = useState('')
  const [securityAnswer, setSecurityAnswer] = useState('')
  const [isSettingQuestion, setIsSettingQuestion] = useState(false)

  // Login History State
  const [loginHistory, setLoginHistory] = useState<LoginHistoryItem[]>([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyPage, setHistoryPage] = useState(1)
  const [isLoadingHistory, setIsLoadingHistory] = useState(false)

  // Active Sessions State
  const [sessions, setSessions] = useState<Session[]>([])
  const [isLoadingSessions, setIsLoadingSessions] = useState(false)

  useEffect(() => {
    loadLoginHistory()
    loadSessions()
  }, [])

  const loadLoginHistory = async (page = 1) => {
    setIsLoadingHistory(true)
    try {
      const limit = 10
      const offset = (page - 1) * limit
      const data = await authApi.getLoginHistory(limit, offset)
      setLoginHistory(data.items)
      setHistoryTotal(data.total)
      setHistoryPage(page)
    } catch (err) {
      message.error('載入登入紀錄失敗')
    } finally {
      setIsLoadingHistory(false)
    }
  }

  const loadSessions = async () => {
    setIsLoadingSessions(true)
    try {
      const data = await authApi.getActiveSessions()
      setSessions(data.sessions)
    } catch (err) {
      message.error('載入使用中的工作階段失敗')
    } finally {
      setIsLoadingSessions(false)
    }
  }

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault()

    if (newPassword.length < 6) {
      message.error('新密碼長度至少需要 6 個字元')
      return
    }

    if (newPassword !== confirmPassword) {
      message.error('兩次輸入的密碼不一致')
      return
    }

    setIsChangingPassword(true)
    try {
      await authApi.changePassword(currentPassword, newPassword)
      message.success('密碼已成功變更')
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '變更密碼失敗')
    } finally {
      setIsChangingPassword(false)
    }
  }

  const handleSetSecurityQuestion = async (e: React.FormEvent) => {
    e.preventDefault()

    if (securityQuestion.length < 10) {
      message.error('安全問題長度至少需要 10 個字元')
      return
    }

    if (!securityAnswer.trim()) {
      message.error('請提供答案')
      return
    }

    setIsSettingQuestion(true)
    try {
      await authApi.setSecurityQuestion({
        question: securityQuestion,
        answer: securityAnswer,
      })
      message.success('安全問題已成功設定')
      setSecurityQuestion('')
      setSecurityAnswer('')
    } catch (err) {
      message.error(err instanceof Error ? err.message : '設定安全問題失敗')
    } finally {
      setIsSettingQuestion(false)
    }
  }

  const handleRevokeSession = async (sessionId: number) => {
    try {
      await authApi.revokeSession(sessionId)
      message.success('已撤銷工作階段')
      loadSessions()
    } catch (err) {
      message.error('撤銷工作階段失敗')
    }
  }

  const handleLogoutAllDevices = () => {
    Modal.confirm({
      title: '從所有裝置登出？',
      icon: <ExclamationCircleOutlined />,
      content: '這將撤銷所有使用中的工作階段（包含目前這個），您需要重新登入。',
      okText: '全部登出',
      okType: 'danger',
      onOk: async () => {
        await logoutAllDevices()
        window.location.href = '/login'
      },
    })
  }

  const getDeviceIcon = (deviceType: string | null | undefined) => {
    switch (deviceType) {
      case 'mobile':
        return <MobileOutlined />
      case 'tablet':
        return <TabletOutlined />
      default:
        return <DesktopOutlined />
    }
  }

  const historyColumns = [
    {
      title: '時間',
      dataIndex: 'login_time',
      key: 'login_time',
      render: (time: string) => new Date(time).toLocaleString(),
    },
    {
      title: '狀態',
      dataIndex: 'success',
      key: 'success',
      render: (success: boolean) =>
        success ? (
          <Tag color="green" icon={<CheckCircleOutlined />}>
            成功
          </Tag>
        ) : (
          <Tag color="red" icon={<CloseCircleOutlined />}>
            失敗
          </Tag>
        ),
    },
    {
      title: '裝置',
      key: 'device',
      render: (_: unknown, record: LoginHistoryItem) => (
        <span className="flex items-center gap-2">
          {getDeviceIcon(record.device_type)}
          <span>{record.browser || '未知'}</span>
          {record.os && <span className="text-gray-500">({record.os})</span>}
        </span>
      ),
    },
    {
      title: 'IP 位址',
      dataIndex: 'ip_address',
      key: 'ip_address',
      render: (ip: string | null) => ip || '-',
    },
  ]

  const tabItems = [
    {
      key: 'password',
      label: (
        <span className="flex items-center gap-2">
          <LockOutlined />
          變更密碼
        </span>
      ),
      children: (
        <form onSubmit={handleChangePassword} className="max-w-md space-y-4">
          <div>
            <label htmlFor="security-current-password" className="block text-sm text-gray-400 mb-2">目前密碼</label>
            <input
              id="security-current-password"
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className="input-field"
              placeholder="請輸入目前密碼"
              autoComplete="current-password"
            />
          </div>
          <div>
            <label htmlFor="security-new-password" className="block text-sm text-gray-400 mb-2">新密碼</label>
            <input
              id="security-new-password"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="input-field"
              placeholder="請輸入新密碼（至少 6 個字元）"
              autoComplete="new-password"
            />
          </div>
          <div>
            <label htmlFor="security-confirm-password" className="block text-sm text-gray-400 mb-2">確認新密碼</label>
            <input
              id="security-confirm-password"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="input-field"
              placeholder="請再次輸入新密碼"
              autoComplete="new-password"
            />
          </div>
          <button
            type="submit"
            disabled={isChangingPassword}
            className="btn-cyber flex items-center gap-2"
          >
            {isChangingPassword && <LoadingOutlined className="animate-spin" />}
            變更密碼
          </button>
        </form>
      ),
    },
    {
      key: 'security_question',
      label: (
        <span className="flex items-center gap-2">
          <QuestionCircleOutlined />
          安全問題
        </span>
      ),
      children: (
        <div className="max-w-md space-y-4">
          {user?.has_security_question && (
            <div className="p-3 rounded-lg bg-matrix-500/10 border border-matrix-500/30 text-matrix-400 text-sm mb-4">
              <CheckCircleOutlined className="mr-2" />
              您已設定安全問題，可於下方進行更新。
            </div>
          )}
          <form onSubmit={handleSetSecurityQuestion} className="space-y-4">
            <div>
              <label htmlFor="security-question" className="block text-sm text-gray-400 mb-2">安全問題</label>
              <input
                id="security-question"
                type="text"
                value={securityQuestion}
                onChange={(e) => setSecurityQuestion(e.target.value)}
                className="input-field"
                placeholder="例如：您第一隻寵物的名字是什麼？"
              />
              <p className="text-xs text-gray-500 mt-1">
                此問題將用於在您重設密碼時驗證身分。
              </p>
            </div>
            <div>
              <label htmlFor="security-answer" className="block text-sm text-gray-400 mb-2">答案</label>
              <input
                id="security-answer"
                type="text"
                value={securityAnswer}
                onChange={(e) => setSecurityAnswer(e.target.value)}
                className="input-field"
                placeholder="請輸入您的答案"
              />
              <p className="text-xs text-gray-500 mt-1">
                答案不區分大小寫，並會自動去除前後空白。
              </p>
            </div>
            <button
              type="submit"
              disabled={isSettingQuestion}
              className="btn-cyber flex items-center gap-2"
            >
              {isSettingQuestion && <LoadingOutlined className="animate-spin" />}
              {user?.has_security_question ? '更新安全問題' : '設定安全問題'}
            </button>
          </form>
        </div>
      ),
    },
    {
      key: 'login_history',
      label: (
        <span className="flex items-center gap-2">
          <HistoryOutlined />
          登入紀錄
        </span>
      ),
      children: (
        <div>
          <Table
            dataSource={loginHistory}
            columns={historyColumns}
            rowKey="id"
            loading={isLoadingHistory}
            pagination={{
              current: historyPage,
              total: historyTotal,
              pageSize: 10,
              onChange: (page) => loadLoginHistory(page),
              showSizeChanger: false,
            }}
            locale={{
              emptyText: <Empty description="沒有登入紀錄" />,
            }}
          />
        </div>
      ),
    },
    {
      key: 'sessions',
      label: (
        <span className="flex items-center gap-2">
          <DesktopOutlined />
          使用中的工作階段
        </span>
      ),
      children: (
        <div className="space-y-4">
          <div className="flex justify-between items-center">
            <p className="text-gray-400 text-sm">
              管理您在所有裝置上使用中的工作階段。
            </p>
            <button
              onClick={handleLogoutAllDevices}
              className="text-alert-400 hover:text-alert-400 text-sm"
            >
              從所有裝置登出
            </button>
          </div>

          {isLoadingSessions ? (
            <div className="flex justify-center py-8">
              <LoadingOutlined className="text-2xl text-cyber-400" />
            </div>
          ) : sessions.length === 0 ? (
            <Empty description="沒有使用中的工作階段" />
          ) : (
            <div className="space-y-3">
              {sessions.map((session) => (
                <div
                  key={session.id}
                  className={`p-4 rounded-lg border ${
                    session.is_current
                      ? 'bg-cyber-500/10 border-cyber-500/30'
                      : 'bg-gray-800/50 border-gray-700'
                  }`}
                >
                  <div className="flex justify-between items-start">
                    <div className="flex items-start gap-3">
                      <div className="text-gray-400 mt-1">
                        <DesktopOutlined className="text-lg" />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-white">
                            {session.device_info || '未知裝置'}
                          </span>
                          {session.is_current && (
                            <Tag color="cyan" className="text-xs">
                              目前
                            </Tag>
                          )}
                          {session.is_remember_me && (
                            <Tag color="blue" className="text-xs">
                              記住我
                            </Tag>
                          )}
                        </div>
                        <p className="text-gray-500 text-sm mt-1">
                          IP：{session.ip_address || '未知'}
                        </p>
                        <p className="text-gray-500 text-sm">
                          建立時間：{new Date(session.created_at).toLocaleString()}
                        </p>
                        <p className="text-gray-500 text-sm">
                          到期時間：{new Date(session.expires_at).toLocaleString()}
                        </p>
                      </div>
                    </div>
                    {!session.is_current && (
                      <Popconfirm
                        title="撤銷此工作階段？"
                        description="該裝置將會被登出。"
                        onConfirm={() => handleRevokeSession(session.id)}
                        okText="撤銷"
                        cancelText="取消"
                      >
                        <button aria-label="撤銷此工作階段" className="text-gray-400 hover:text-alert-400 transition-colors">
                          <DeleteOutlined />
                        </button>
                      </Popconfirm>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-white" style={{ fontFamily: 'var(--font-display)' }}>
          安全設定
        </h1>
        <p className="text-gray-400 mt-1">管理您的帳號安全與使用中的工作階段</p>
      </div>

      <div className="glass-card p-6">
        <Tabs items={tabItems} />
      </div>
    </div>
  )
}
