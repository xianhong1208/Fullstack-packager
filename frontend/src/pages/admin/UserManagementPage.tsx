import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Table, Tag, Input, Select, message, Empty, Modal } from 'antd'
import {
  UserOutlined,
  SearchOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  ReloadOutlined,
  CrownOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { userApi } from '../../api/userApi'
import type { UserListItem, Role } from '../../api/types'
import { useAuth } from '../../contexts/AuthContext'

export default function UserManagementPage() {
  const { hasPermission } = useAuth()

  const [users, setUsers] = useState<UserListItem[]>([])
  const [roles, setRoles] = useState<Role[]>([])
  const [total, setTotal] = useState(0)
  const [isLoading, setIsLoading] = useState(false)

  // Filters
  const [search, setSearch] = useState('')
  const [filterStatus, setFilterStatus] = useState<boolean | undefined>(undefined)
  const [filterRole, setFilterRole] = useState<number | undefined>(undefined)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(10)

  useEffect(() => {
    loadRoles()
  }, [])

  useEffect(() => {
    loadUsers()
  }, [search, filterStatus, filterRole, page])

  const loadRoles = async () => {
    try {
      const data = await userApi.getRoles()
      setRoles(data)
    } catch {
      // Ignore - roles are optional for display
    }
  }

  const loadUsers = async () => {
    setIsLoading(true)
    try {
      const [usersData, countData] = await Promise.all([
        userApi.getUsers({
          skip: (page - 1) * pageSize,
          limit: pageSize,
          search: search || undefined,
          is_active: filterStatus,
          role_id: filterRole,
        }),
        userApi.getUsersCount({
          search: search || undefined,
          is_active: filterStatus,
          role_id: filterRole,
        }),
      ])
      setUsers(usersData)
      setTotal(countData.count)
    } catch (err) {
      message.error('Failed to load users')
    } finally {
      setIsLoading(false)
    }
  }

  const handleStatusChange = async (userId: number, isActive: boolean) => {
    try {
      await userApi.updateUserStatus(userId, isActive)
      message.success(`User ${isActive ? 'activated' : 'deactivated'}`)
      loadUsers()
    } catch (err) {
      message.error(err instanceof Error ? err.message : 'Failed to update status')
    }
  }

  const handleRoleChange = async (userId: number, roleId: number) => {
    try {
      await userApi.updateUserRole(userId, roleId)
      message.success('Role updated')
      loadUsers()
    } catch (err) {
      message.error(err instanceof Error ? err.message : 'Failed to update role')
    }
  }

  const handleResetPassword = (userId: number, username: string) => {
    let newPassword = ''

    Modal.confirm({
      title: `Reset password for ${username}?`,
      content: (
        <div className="mt-4">
          <p className="mb-2" style={{ color: 'var(--ink-muted)' }}>Enter a new password:</p>
          <Input.Password
            placeholder="New password (at least 6 characters)"
            onChange={(e) => {
              newPassword = e.target.value
            }}
          />
        </div>
      ),
      okText: 'Reset password',
      onOk: async () => {
        if (newPassword.length < 6) {
          message.error('Password must be at least 6 characters')
          throw new Error('Validation error')
        }
        await userApi.resetUserPassword(userId, newPassword)
        message.success('Password reset successfully')
      },
    })
  }

  const columns: ColumnsType<UserListItem> = [
    {
      title: 'User',
      key: 'user',
      render: (_, record) => (
        <div className="flex items-center gap-3">
          <div
            className="w-8 h-8 rounded-full flex items-center justify-center"
            style={{ background: 'var(--color-void-700)' }}
          >
            <UserOutlined style={{ color: 'var(--ink-muted)' }} />
          </div>
          <div>
            <Link
              to={`/admin/users/${record.id}`}
              style={{ color: 'var(--ink)', fontWeight: 600 }}
              className="hover:!text-cyber-300 transition-colors"
            >
              {record.username}
            </Link>
            {record.email && (
              <p className="text-xs" style={{ color: 'var(--ink-faint)' }}>{record.email}</p>
            )}
          </div>
        </div>
      ),
    },
    {
      title: 'Role',
      key: 'role',
      render: (_, record) => {
        if (!hasPermission('user:manage')) {
          return record.role ? (
            <Tag color={record.role.name === 'admin' ? 'gold' : 'blue'}>
              {record.role.name === 'admin' && <CrownOutlined className="mr-1" />}
              {record.role.display_name}
            </Tag>
          ) : (
            <Tag>No role</Tag>
          )
        }

        return (
          <Select
            value={record.role?.id}
            onChange={(value) => handleRoleChange(record.id, value)}
            style={{ width: 120 }}
            size="small"
          >
            {roles.map((role) => (
              <Select.Option key={role.id} value={role.id}>
                {role.display_name}
              </Select.Option>
            ))}
          </Select>
        )
      },
    },
    {
      title: 'Status',
      key: 'status',
      render: (_, record) => (
        <Tag
          color={record.is_active ? 'green' : 'red'}
          icon={record.is_active ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
        >
          {record.is_active ? 'Active' : 'Disabled'}
        </Tag>
      ),
    },
    {
      title: 'Last login',
      dataIndex: 'last_login',
      key: 'last_login',
      render: (time: string | null) =>
        time ? (
          new Date(time).toLocaleString()
        ) : (
          <span style={{ color: 'var(--ink-faint)' }}>Never</span>
        ),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (time: string) => new Date(time).toLocaleDateString(),
    },
    {
      title: 'Actions',
      key: 'actions',
      render: (_, record) => (
        <div className="flex items-center gap-3">
          {hasPermission('user:manage') && (
            <button
              onClick={() => handleStatusChange(record.id, !record.is_active)}
              className={`text-sm ${
                record.is_active
                  ? 'text-alert-400 hover:text-alert-400'
                  : 'text-matrix-400 hover:text-matrix-400'
              }`}
            >
              {record.is_active ? 'Deactivate' : 'Activate'}
            </button>
          )}
          {hasPermission('user:reset_password') && (
            <button
              onClick={() => handleResetPassword(record.id, record.username)}
              className="text-sm text-cyber-400 hover:text-cyber-300"
            >
              Reset password
            </button>
          )}
        </div>
      ),
    },
  ]

  return (
    <div>
      {/* Page header */}
      <div
        className="flex flex-wrap items-start justify-between gap-4 pb-4 mb-6"
        style={{ borderBottom: '1px solid var(--seam)' }}
      >
        <div>
          <h1 className="text-2xl font-semibold" style={{ fontFamily: 'var(--font-display)', color: 'var(--ink)' }}>
            User management
          </h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--ink-muted)' }}>
            Manage users, roles, and permissions
          </p>
        </div>
        <button onClick={loadUsers} className="btn-ghost flex items-center gap-2">
          <ReloadOutlined />
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="glass-card p-4 mb-6">
        <div className="flex flex-wrap gap-4">
          <Input
            placeholder="Search users..."
            prefix={<SearchOutlined style={{ color: 'var(--ink-faint)' }} />}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPage(1)
            }}
            style={{ width: 250 }}
            allowClear
          />

          <Select
            placeholder="Filter by status"
            value={filterStatus}
            onChange={(value) => {
              setFilterStatus(value)
              setPage(1)
            }}
            style={{ width: 160 }}
            allowClear
          >
            <Select.Option value={true}>Active</Select.Option>
            <Select.Option value={false}>Disabled</Select.Option>
          </Select>

          <Select
            placeholder="Filter by role"
            value={filterRole}
            onChange={(value) => {
              setFilterRole(value)
              setPage(1)
            }}
            style={{ width: 160 }}
            allowClear
          >
            {roles.map((role) => (
              <Select.Option key={role.id} value={role.id}>
                {role.display_name}
              </Select.Option>
            ))}
          </Select>
        </div>
      </div>

      {/* Users table */}
      <Table
        dataSource={users}
        columns={columns}
        rowKey="id"
        loading={{
          spinning: isLoading,
          indicator: <LoadingOutlined className="text-cyber-400" />,
        }}
        pagination={{
          current: page,
          total: total,
          pageSize: pageSize,
          onChange: setPage,
          showSizeChanger: false,
          showTotal: (total) => `${total} users total`,
        }}
        locale={{
          emptyText: <Empty description="No users found" />,
        }}
      />
    </div>
  )
}
