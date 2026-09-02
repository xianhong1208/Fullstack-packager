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
      message.error('Failed to load user')
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
      message.success(`User ${isActive ? 'activated' : 'deactivated'}`)
      loadUser()
    } catch (err) {
      message.error(err instanceof Error ? err.message : 'Failed to update status')
    }
  }

  const handleRoleChange = async (roleId: number) => {
    try {
      await userApi.updateUserRole(Number(userId), roleId)
      message.success('Role updated')
      loadUser()
    } catch (err) {
      message.error(err instanceof Error ? err.message : 'Failed to update role')
    }
  }

  const handleResetPassword = () => {
    let newPassword = ''

    Modal.confirm({
      title: `Reset password for ${user?.username}?`,
      content: (
        <div className="mt-4">
          <p className="mb-2" style={{ color: 'var(--ink-muted)' }}>Enter a new password:</p>
          <input
            type="password"
            placeholder="New password (at least 6 characters)"
            onChange={(e) => {
              newPassword = e.target.value
            }}
            className="input-field"
          />
        </div>
      ),
      okText: 'Reset password',
      onOk: async () => {
        if (newPassword.length < 6) {
          message.error('Password must be at least 6 characters')
          throw new Error('Validation error')
        }
        await userApi.resetUserPassword(Number(userId), newPassword)
        message.success('Password reset successfully')
      },
    })
  }

  const historyColumns = [
    {
      title: 'Time',
      dataIndex: 'login_time',
      key: 'login_time',
      render: (time: string) => new Date(time).toLocaleString(),
    },
    {
      title: 'Status',
      dataIndex: 'success',
      key: 'success',
      render: (success: boolean) =>
        success ? (
          <Tag color="green" icon={<CheckCircleOutlined />}>
            Success
          </Tag>
        ) : (
          <Tag color="red" icon={<CloseCircleOutlined />}>
            Failed
          </Tag>
        ),
    },
    {
      title: 'Device',
      key: 'device',
      render: (_: unknown, record: LoginHistoryItem) => (
        <span>
          {record.browser || 'Unknown'}
          {record.os && <span style={{ color: 'var(--ink-faint)' }}> ({record.os})</span>}
        </span>
      ),
    },
    {
      title: 'IP address',
      dataIndex: 'ip_address',
      key: 'ip_address',
      render: (ip: string | null) => ip || '-',
    },
    {
      title: 'Failure reason',
      dataIndex: 'failure_reason',
      key: 'failure_reason',
      render: (reason: string | null) =>
        reason ? <span className="text-alert-400">{reason}</span> : '-',
    },
  ]

  if (isLoading) {
    return (
      <div className="flex justify-center items-center h-64">
        <Spin indicator={<LoadingOutlined className="text-cyber-400 text-3xl" />} />
      </div>
    )
  }

  if (!user) {
    return (
      <div className="p-6">
        <Empty description="User not found" />
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
          Details
        </span>
      ),
      children: (
        <Descriptions
          column={1}
          labelStyle={{ color: 'var(--ink-faint)', width: 150 }}
          contentStyle={{ color: 'var(--ink)' }}
        >
          <Descriptions.Item label="Username">{user.username}</Descriptions.Item>
          <Descriptions.Item label="Email">{user.email || '-'}</Descriptions.Item>
          <Descriptions.Item label="Role">
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
              <Tag>No role</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="Status">
            <Tag
              color={user.is_active ? 'green' : 'red'}
              icon={user.is_active ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
            >
              {user.is_active ? 'Active' : 'Disabled'}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Security question">
            {user.has_security_question ? (
              <Tag color="green" icon={<CheckCircleOutlined />}>
                Set
              </Tag>
            ) : (
              <Tag color="orange">Not set</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="Created">
            {user.created_at ? new Date(user.created_at).toLocaleString() : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="Last login">
            {user.last_login ? new Date(user.last_login).toLocaleString() : 'Never'}
          </Descriptions.Item>
        </Descriptions>
      ),
    },
    {
      key: 'login_history',
      label: (
        <span className="flex items-center gap-2">
          <HistoryOutlined />
          Login history
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
            emptyText: <Empty description="No login history" />,
          }}
        />
      ),
    },
    {
      key: 'permissions',
      label: (
        <span className="flex items-center gap-2">
          <SafetyCertificateOutlined />
          Permissions
        </span>
      ),
      children: (
        <div>
          {user.role ? (
            <div>
              <p className="mb-4" style={{ color: 'var(--ink-muted)' }}>
                Permissions inherited from this user's role:{' '}
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
            <Empty description="No role assigned — this user has no permissions" />
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="pb-4 mb-6" style={{ borderBottom: '1px solid var(--seam)' }}>
        <Link
          to="/admin/users"
          className="inline-flex items-center gap-2 hover:!text-cyber-300 transition-colors mb-4"
          style={{ color: 'var(--ink-muted)' }}
        >
          <ArrowLeftOutlined />
          Back to users
        </Link>

        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div
              className="w-16 h-16 rounded-full flex items-center justify-center"
              style={{ background: 'var(--color-void-700)' }}
            >
              <UserOutlined className="text-2xl" style={{ color: 'var(--ink-muted)' }} />
            </div>
            <div>
              <h1 className="text-2xl font-semibold" style={{ fontFamily: 'var(--font-display)', color: 'var(--ink)' }}>
                {user.username}
              </h1>
              <p style={{ color: 'var(--ink-muted)' }}>{user.email || 'No email'}</p>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3">
            {hasPermission('user:manage') && !isSelf && (
              <button
                onClick={() => handleStatusChange(!user.is_active)}
                className={user.is_active ? 'btn-danger' : 'btn-ghost'}
              >
                {user.is_active ? 'Deactivate' : 'Activate'}
              </button>
            )}
            {hasPermission('user:reset_password') && (
              <button onClick={handleResetPassword} className="btn-cyber">
                Reset password
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
