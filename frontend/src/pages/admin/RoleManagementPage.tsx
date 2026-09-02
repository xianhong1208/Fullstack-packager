import { useState, useEffect } from 'react'
import { Table, Tag, Button, Modal, Form, Input, Select, Checkbox, message, Empty, Tooltip } from 'antd'
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  LoadingOutlined,
  ReloadOutlined,
  LockOutlined,
  SafetyOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { userApi } from '../../api/userApi'
import type { Role, RoleCreate, RoleUpdate, PermissionCategory } from '../../api/types'
import { useAuth } from '../../contexts/AuthContext'

export default function RoleManagementPage() {
  const { hasPermission } = useAuth()
  const canManage = hasPermission('role:manage')

  const [roles, setRoles] = useState<Role[]>([])
  const [permissions, setPermissions] = useState<PermissionCategory[]>([])
  const [isLoading, setIsLoading] = useState(false)

  // Modal state
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [editingRole, setEditingRole] = useState<Role | null>(null)
  const [form] = Form.useForm()

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setIsLoading(true)
    try {
      const [rolesData, permsData] = await Promise.all([
        userApi.getRoles(),
        userApi.getPermissions(),
      ])
      setRoles(rolesData)
      setPermissions(permsData)
    } catch {
      message.error('Failed to load data')
    } finally {
      setIsLoading(false)
    }
  }

  const handleCreate = () => {
    setEditingRole(null)
    form.resetFields()
    setIsModalOpen(true)
  }

  const handleEdit = (role: Role) => {
    setEditingRole(role)
    form.setFieldsValue({
      name: role.name,
      display_name: role.display_name,
      description: role.description,
      parent_role_id: role.parent_role_id || undefined,
      permission_codes: role.permissions.map(p => p.code),
    })
    setIsModalOpen(true)
  }

  const handleDelete = (role: Role) => {
    Modal.confirm({
      title: `Delete role "${role.display_name}"?`,
      content: 'This action cannot be undone. Users with this role will need to be reassigned.',
      okText: 'Delete',
      okButtonProps: { danger: true },
      className: 'dark-modal',
      onOk: async () => {
        try {
          await userApi.deleteRole(role.id)
          message.success('Role deleted')
          loadData()
        } catch (err) {
          message.error(err instanceof Error ? err.message : 'Failed to delete role')
        }
      },
    })
  }

  const handleSubmit = async (values: {
    name: string
    display_name: string
    description?: string
    parent_role_id?: number
    permission_codes: string[]
  }) => {
    try {
      if (editingRole) {
        const updateData: RoleUpdate = {
          display_name: values.display_name,
          description: values.description,
          parent_role_id: values.parent_role_id || null,
          permission_codes: values.permission_codes,
        }
        await userApi.updateRole(editingRole.id, updateData)
        message.success('Role updated')
      } else {
        const createData: RoleCreate = {
          name: values.name,
          display_name: values.display_name,
          description: values.description,
          parent_role_id: values.parent_role_id,
          permission_codes: values.permission_codes || [],
        }
        await userApi.createRole(createData)
        message.success('Role created')
      }
      setIsModalOpen(false)
      loadData()
    } catch (err) {
      message.error(err instanceof Error ? err.message : 'Operation failed')
    }
  }

  const columns: ColumnsType<Role> = [
    {
      title: 'Role',
      key: 'role',
      render: (_, record) => (
        <div className="flex items-center gap-3">
          <div className={`w-8 h-8 rounded-full flex items-center justify-center ${
            record.is_system ? 'bg-signal-500/20' : 'bg-cyber-500/20'
          }`}>
            {record.is_system ? (
              <LockOutlined className="text-signal-400" />
            ) : (
              <TeamOutlined className="text-cyber-400" />
            )}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span style={{ color: 'var(--ink)', fontWeight: 500 }}>{record.display_name}</span>
              {record.is_system && (
                <Tooltip title="System role — cannot be deleted">
                  <Tag color="gold" className="text-xs">System</Tag>
                </Tooltip>
              )}
              {!record.is_active && (
                <Tag color="red" className="text-xs">Disabled</Tag>
              )}
            </div>
            <span className="text-xs" style={{ color: 'var(--ink-faint)', fontFamily: 'var(--font-mono)' }}>
              {record.name}
            </span>
          </div>
        </div>
      ),
    },
    {
      title: 'Permissions',
      key: 'permissions',
      render: (_, record) => {
        const permCodes = record.permissions.map(p => p.code)
        if (permCodes.includes('*:*')) {
          return <Tag color="gold" icon={<SafetyOutlined />}>Full access</Tag>
        }
        return (
          <div className="flex flex-wrap gap-1">
            {record.permissions.slice(0, 3).map(p => (
              <Tag key={p.code} className="text-xs">{p.code}</Tag>
            ))}
            {record.permissions.length > 3 && (
              <Tooltip title={record.permissions.slice(3).map(p => p.code).join(', ')}>
                <Tag className="text-xs">+{record.permissions.length - 3} more</Tag>
              </Tooltip>
            )}
          </div>
        )
      },
    },
    {
      title: 'Parent role',
      key: 'parent',
      render: (_, record) => {
        if (!record.parent_role_id) return <span style={{ color: 'var(--ink-faint)' }}>-</span>
        const parent = roles.find(r => r.id === record.parent_role_id)
        return parent ? (
          <Tag color="blue">{parent.display_name}</Tag>
        ) : (
          <span style={{ color: 'var(--ink-faint)' }}>-</span>
        )
      },
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
        <div className="flex items-center gap-2">
          {canManage && (
            <>
              <Button
                type="text"
                aria-label="Edit role"
                icon={<EditOutlined />}
                onClick={() => handleEdit(record)}
                className="text-cyber-400 hover:text-cyber-300"
              />
              {!record.is_system && (
                <Button
                  type="text"
                  aria-label="Delete role"
                  icon={<DeleteOutlined />}
                  onClick={() => handleDelete(record)}
                  className="text-alert-400 hover:text-alert-400"
                />
              )}
            </>
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
            Role management
          </h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--ink-muted)' }}>
            Manage roles and their permissions
          </p>
        </div>
        <div className="flex gap-2">
          <Button icon={<ReloadOutlined />} onClick={loadData}>
            Refresh
          </Button>
          {canManage && (
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={handleCreate}
            >
              Add role
            </Button>
          )}
        </div>
      </div>

      {/* Roles table */}
      <Table
        dataSource={roles}
        columns={columns}
        rowKey="id"
        loading={{
          spinning: isLoading,
          indicator: <LoadingOutlined className="text-cyber-400" />,
        }}
        pagination={false}
        locale={{
          emptyText: <Empty description="No roles found" />,
        }}
      />

      {/* Create/Edit Modal */}
      <Modal
        title={editingRole ? `Edit role: ${editingRole.display_name}` : 'Create role'}
        open={isModalOpen}
        onCancel={() => setIsModalOpen(false)}
        footer={null}
        width={700}
        className="dark-modal"
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          className="mt-4"
        >
          <div className="grid grid-cols-2 gap-4">
            <Form.Item
              name="name"
              label="Role name (ID)"
              rules={[
                { required: true, message: 'Enter a role name' },
                { pattern: /^[a-z][a-z0-9_]*$/, message: 'Lowercase letters, numbers, and underscores only' },
              ]}
            >
              <Input
                placeholder="e.g. moderator"
                disabled={!!editingRole}
              />
            </Form.Item>

            <Form.Item
              name="display_name"
              label="Display name"
              rules={[{ required: true, message: 'Enter a display name' }]}
            >
              <Input placeholder="e.g. Moderator" />
            </Form.Item>
          </div>

          <Form.Item
            name="description"
            label="Description"
          >
            <Input.TextArea
              placeholder="Role description..."
              rows={2}
            />
          </Form.Item>

          <Form.Item
            name="parent_role_id"
            label="Parent role (inherits its permissions)"
          >
            <Select
              placeholder="Select a parent role (optional)"
              allowClear
              options={roles
                .filter(r => r.id !== editingRole?.id)
                .map(r => ({ value: r.id, label: r.display_name }))}
            />
          </Form.Item>

          <Form.Item
            name="permission_codes"
            label="Permissions"
          >
            <div className="max-h-64 overflow-y-auto rounded-lg p-4" style={{ border: '1px solid var(--color-void-700)' }}>
              {permissions.map(category => (
                <div key={category.category} className="mb-4">
                  <h4 className="text-sm font-medium mb-2 capitalize" style={{ color: 'var(--ink-muted)' }}>
                    {category.category}
                  </h4>
                  <Form.Item name="permission_codes" noStyle>
                    <Checkbox.Group className="flex flex-wrap gap-2">
                      {category.permissions.map(perm => (
                        <Checkbox key={perm.code} value={perm.code}>
                          <Tooltip title={perm.description}>
                            <span style={{ color: 'var(--ink-muted)' }}>{perm.name}</span>
                          </Tooltip>
                        </Checkbox>
                      ))}
                    </Checkbox.Group>
                  </Form.Item>
                </div>
              ))}
            </div>
          </Form.Item>

          <div className="flex justify-end gap-2 mt-6">
            <Button onClick={() => setIsModalOpen(false)}>
              Cancel
            </Button>
            <Button type="primary" htmlType="submit">
              {editingRole ? 'Update' : 'Create'}
            </Button>
          </div>
        </Form>
      </Modal>
    </div>
  )
}
