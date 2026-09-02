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
      message.error('載入使用者失敗')
    } finally {
      setIsLoading(false)
    }
  }

  const handleStatusChange = async (userId: number, isActive: boolean) => {
    try {
      await userApi.updateUserStatus(userId, isActive)
      message.success(`使用者已${isActive ? '啟用' : '停用'}`)
      loadUsers()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '更新狀態失敗')
    }
  }

  const handleRoleChange = async (userId: number, roleId: number) => {
    try {
      await userApi.updateUserRole(userId, roleId)
      message.success('角色已更新')
      loadUsers()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '更新角色失敗')
    }
  }

  const handleResetPassword = (userId: number, username: string) => {
    let newPassword = ''

    Modal.confirm({
      title: `重設 ${username} 的密碼？`,
      content: (
        <div className="mt-4">
          <p className="text-gray-400 mb-2">請輸入新密碼：</p>
          <Input.Password
            placeholder="新密碼（至少 6 個字元）"
            onChange={(e) => {
              newPassword = e.target.value
            }}
          />
        </div>
      ),
      okText: '重設密碼',
      onOk: async () => {
        if (newPassword.length < 6) {
          message.error('密碼長度至少需要 6 個字元')
          throw new Error('Validation error')
        }
        await userApi.resetUserPassword(userId, newPassword)
        message.success('密碼已成功重設')
      },
    })
  }

  const columns: ColumnsType<UserListItem> = [
    {
      title: '使用者',
      key: 'user',
      render: (_, record) => (
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-gray-700 flex items-center justify-center">
            <UserOutlined className="text-gray-400" />
          </div>
          <div>
            <Link
              to={`/admin/users/${record.id}`}
              className="text-white hover:text-cyber-400 transition-colors"
            >
              {record.username}
            </Link>
            {record.email && <p className="text-gray-500 text-xs">{record.email}</p>}
          </div>
        </div>
      ),
    },
    {
      title: '角色',
      key: 'role',
      render: (_, record) => {
        if (!hasPermission('user:manage')) {
          return record.role ? (
            <Tag color={record.role.name === 'admin' ? 'gold' : 'blue'}>
              {record.role.name === 'admin' && <CrownOutlined className="mr-1" />}
              {record.role.display_name}
            </Tag>
          ) : (
            <Tag>無角色</Tag>
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
      title: '狀態',
      key: 'status',
      render: (_, record) => (
        <Tag
          color={record.is_active ? 'green' : 'red'}
          icon={record.is_active ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
        >
          {record.is_active ? '啟用中' : '已停用'}
        </Tag>
      ),
    },
    {
      title: '最後登入',
      dataIndex: 'last_login',
      key: 'last_login',
      render: (time: string | null) =>
        time ? new Date(time).toLocaleString() : <span className="text-gray-500">從未</span>,
    },
    {
      title: '建立時間',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (time: string) => new Date(time).toLocaleDateString(),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, record) => (
        <div className="flex items-center gap-2">
          {hasPermission('user:manage') && (
            <button
              onClick={() => handleStatusChange(record.id, !record.is_active)}
              className={`text-sm ${
                record.is_active
                  ? 'text-alert-400 hover:text-alert-400'
                  : 'text-matrix-400 hover:text-matrix-400'
              }`}
            >
              {record.is_active ? '停用' : '啟用'}
            </button>
          )}
          {hasPermission('user:reset_password') && (
            <button
              onClick={() => handleResetPassword(record.id, record.username)}
              className="text-sm text-cyber-400 hover:text-cyber-300"
            >
              重設密碼
            </button>
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-white" style={{ fontFamily: 'var(--font-display)' }}>
          使用者管理
        </h1>
        <p className="text-gray-400 mt-1">管理使用者、角色與權限</p>
      </div>

      {/* Filters */}
      <div className="glass-card p-4 mb-6">
        <div className="flex flex-wrap gap-4">
          <Input
            placeholder="搜尋使用者..."
            prefix={<SearchOutlined className="text-gray-500" />}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPage(1)
            }}
            style={{ width: 250 }}
            allowClear
          />

          <Select
            placeholder="依狀態篩選"
            value={filterStatus}
            onChange={(value) => {
              setFilterStatus(value)
              setPage(1)
            }}
            style={{ width: 150 }}
            allowClear
          >
            <Select.Option value={true}>啟用中</Select.Option>
            <Select.Option value={false}>已停用</Select.Option>
          </Select>

          <Select
            placeholder="依角色篩選"
            value={filterRole}
            onChange={(value) => {
              setFilterRole(value)
              setPage(1)
            }}
            style={{ width: 150 }}
            allowClear
          >
            {roles.map((role) => (
              <Select.Option key={role.id} value={role.id}>
                {role.display_name}
              </Select.Option>
            ))}
          </Select>

          <button
            onClick={loadUsers}
            className="flex items-center gap-2 px-3 py-1 text-gray-400 hover:text-cyber-400 transition-colors"
          >
            <ReloadOutlined />
            重新整理
          </button>
        </div>
      </div>

      {/* Users Table */}
      <div className="glass-card p-4">
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
            showTotal: (total) => `共 ${total} 位使用者`,
          }}
          locale={{
            emptyText: <Empty description="找不到使用者" />,
          }}
        />
      </div>
    </div>
  )
}
