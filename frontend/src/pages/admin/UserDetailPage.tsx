import { useState, useEffect } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { Descriptions, Tag, Table, message, Modal, Select, Tabs, Empty, Spin } from 'antd'
import {
  UserOutlined,
  ArrowLeftOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CrownOutlined,
  LoadingOutlined,
  HistoryOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { userApi } from '../../api/userApi'
import type { User, Role, LoginHistoryItem } from '../../api/types'
import { useAuth } from '../../contexts/AuthContext'

export default function UserDetailPage() {
  const { userId } = useParams<{ userId: string }>()
  const navigate = useNavigate()
  const { hasPermission, user: currentUser } = useAuth()

  const [user, setUser] = useState<User | null>(null)
  const [roles, setRoles] = useState<Role[]>([])
  const [loginHistory, setLoginHistory] = useState<LoginHistoryItem[]>([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyPage, setHistoryPage] = useState(1)

  const [isLoading, setIsLoading] = useState(true)
  const [isLoadingHistory, setIsLoadingHistory] = useState(false)

  useEffect(() => {
    if (userId) {
      loadUser()
      loadRoles()
      loadLoginHistory()
    }
  }, [userId])

  const loadUser = async () => {
    setIsLoading(true)
    try {
      const data = await userApi.getUser(Number(userId))
      setUser(data)
    } catch (err) {
      message.error('載入使用者失敗')
      navigate('/admin/users')
    } finally {
      setIsLoading(false)
    }
  }

  const loadRoles = async () => {
    try {
      const data = await userApi.getRoles()
      setRoles(data)
    } catch {
      // Ignore
    }
  }

  const loadLoginHistory = async (page = 1) => {
    setIsLoadingHistory(true)
    try {
      const limit = 10
      const offset = (page - 1) * limit
      const data = await userApi.getUserLoginHistory(Number(userId), limit, offset)
      setLoginHistory(data.items)
      setHistoryTotal(data.total)
      setHistoryPage(page)
    } catch {
      // Ignore
    } finally {
      setIsLoadingHistory(false)
    }
  }

  const handleStatusChange = async (isActive: boolean) => {
    try {
      await userApi.updateUserStatus(Number(userId), isActive)
      message.success(`使用者已${isActive ? '啟用' : '停用'}`)
      loadUser()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '更新狀態失敗')
    }
  }

  const handleRoleChange = async (roleId: number) => {
    try {
      await userApi.updateUserRole(Number(userId), roleId)
      message.success('角色已更新')
      loadUser()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '更新角色失敗')
    }
  }

  const handleResetPassword = () => {
    let newPassword = ''

    Modal.confirm({
      title: `重設 ${user?.username} 的密碼？`,
      content: (
        <div className="mt-4">
          <p className="text-gray-400 mb-2">請輸入新密碼：</p>
          <input
            type="password"
            placeholder="新密碼（至少 6 個字元）"
            onChange={(e) => {
              newPassword = e.target.value
            }}
            className="input-field"
          />
        </div>
      ),
      okText: '重設密碼',
      onOk: async () => {
        if (newPassword.length < 6) {
          message.error('密碼長度至少需要 6 個字元')
          throw new Error('Validation error')
        }
        await userApi.resetUserPassword(Number(userId), newPassword)
        message.success('密碼已成功重設')
      },
    })
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
        <span>
          {record.browser || '未知'}
          {record.os && <span className="text-gray-500"> ({record.os})</span>}
        </span>
      ),
    },
    {
      title: 'IP 位址',
      dataIndex: 'ip_address',
      key: 'ip_address',
      render: (ip: string | null) => ip || '-',
    },
    {
      title: '失敗原因',
      dataIndex: 'failure_reason',
      key: 'failure_reason',
      render: (reason: string | null) =>
        reason ? <span className="text-red-400">{reason}</span> : '-',
    },
  ]

  if (isLoading) {
    return (
      <div className="flex justify-center items-center h-64">
        <Spin indicator={<LoadingOutlined className="text-cyan-400 text-3xl" />} />
      </div>
    )
  }

  if (!user) {
    return (
      <div className="p-6">
        <Empty description="找不到使用者" />
      </div>
    )
  }

  const isSelf = currentUser?.id === user.id

  const tabItems = [
    {
      key: 'details',
      label: (
        <span className="flex items-center gap-2">
          <UserOutlined />
          詳細資料
        </span>
      ),
      children: (
        <Descriptions
          column={1}
          labelStyle={{ color: '#9ca3af', width: 150 }}
          contentStyle={{ color: '#fff' }}
        >
          <Descriptions.Item label="使用者名稱">{user.username}</Descriptions.Item>
          <Descriptions.Item label="電子郵件">{user.email || '-'}</Descriptions.Item>
          <Descriptions.Item label="角色">
            {hasPermission('user:manage') && !isSelf ? (
              <Select
                value={user.role?.id}
                onChange={handleRoleChange}
                style={{ width: 150 }}
              >
                {roles.map((role) => (
                  <Select.Option key={role.id} value={role.id}>
                    {role.display_name}
                  </Select.Option>
                ))}
              </Select>
            ) : user.role ? (
              <Tag color={user.role.name === 'admin' ? 'gold' : 'blue'}>
                {user.role.name === 'admin' && <CrownOutlined className="mr-1" />}
                {user.role.display_name}
              </Tag>
            ) : (
              <Tag>無角色</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="狀態">
            <Tag
              color={user.is_active ? 'green' : 'red'}
              icon={user.is_active ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
            >
              {user.is_active ? '啟用中' : '已停用'}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="安全問題">
            {user.has_security_question ? (
              <Tag color="green" icon={<CheckCircleOutlined />}>
                已設定
              </Tag>
            ) : (
              <Tag color="orange">未設定</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="建立時間">
            {user.created_at ? new Date(user.created_at).toLocaleString() : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="最後登入">
            {user.last_login ? new Date(user.last_login).toLocaleString() : '從未'}
          </Descriptions.Item>
        </Descriptions>
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
      ),
    },
    {
      key: 'permissions',
      label: (
        <span className="flex items-center gap-2">
          <SafetyCertificateOutlined />
          權限
        </span>
      ),
      children: (
        <div>
          {user.role ? (
            <div>
              <p className="text-gray-400 mb-4">
                權限繼承自使用者的角色：{' '}
                <Tag color={user.role.name === 'admin' ? 'gold' : 'blue'}>
                  {user.role.display_name}
                </Tag>
              </p>
              <div className="flex flex-wrap gap-2">
                {roles
                  .find((r) => r.id === user.role?.id)
                  ?.permissions.map((p) => (
                    <Tag key={p.id} color="cyan">
                      {p.code}
                    </Tag>
                  ))}
              </div>
            </div>
          ) : (
            <Empty description="未指派角色，此使用者沒有任何權限" />
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="p-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <Link
          to="/admin/users"
          className="inline-flex items-center gap-2 text-gray-400 hover:text-cyan-400 transition-colors mb-4"
        >
          <ArrowLeftOutlined />
          返回使用者列表
        </Link>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-16 h-16 rounded-full bg-gray-700 flex items-center justify-center">
              <UserOutlined className="text-2xl text-gray-400" />
            </div>
            <div>
              <h1 className="text-2xl font-semibold text-white" style={{ fontFamily: 'var(--font-display)' }}>
                {user.username}
              </h1>
              <p className="text-gray-400">{user.email || '無電子郵件'}</p>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3">
            {hasPermission('user:manage') && !isSelf && (
              <button
                onClick={() => handleStatusChange(!user.is_active)}
                className={`px-4 py-2 rounded-lg border transition-colors ${
                  user.is_active
                    ? 'border-red-500/30 text-red-400 hover:bg-red-500/10'
                    : 'border-green-500/30 text-green-400 hover:bg-green-500/10'
                }`}
              >
                {user.is_active ? '停用' : '啟用'}
              </button>
            )}
            {hasPermission('user:reset_password') && (
              <button onClick={handleResetPassword} className="btn-cyber">
                重設密碼
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="glass-card p-6">
        <Tabs items={tabItems} />
      </div>
    </div>
  )
}
