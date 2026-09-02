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
      message.error('載入資料失敗')
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
      title: `刪除角色「${role.display_name}」？`,
      content: '此操作無法復原。擁有此角色的使用者將需要重新指派角色。',
      okText: '刪除',
      okButtonProps: { danger: true },
      className: 'dark-modal',
      onOk: async () => {
        try {
          await userApi.deleteRole(role.id)
          message.success('角色已刪除')
          loadData()
        } catch (err) {
          message.error(err instanceof Error ? err.message : '刪除角色失敗')
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
        message.success('角色已更新')
      } else {
        const createData: RoleCreate = {
          name: values.name,
          display_name: values.display_name,
          description: values.description,
          parent_role_id: values.parent_role_id,
          permission_codes: values.permission_codes || [],
        }
        await userApi.createRole(createData)
        message.success('角色已建立')
      }
      setIsModalOpen(false)
      loadData()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '操作失敗')
    }
  }

  const columns: ColumnsType<Role> = [
    {
      title: '角色',
      key: 'role',
      render: (_, record) => (
        <div className="flex items-center gap-3">
          <div className={`w-8 h-8 rounded-full flex items-center justify-center ${
            record.is_system ? 'bg-amber-500/20' : 'bg-cyber-500/20'
          }`}>
            {record.is_system ? (
              <LockOutlined className="text-amber-400" />
            ) : (
              <TeamOutlined className="text-cyber-400" />
            )}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-white font-medium">{record.display_name}</span>
              {record.is_system && (
                <Tooltip title="系統角色，無法刪除">
                  <Tag color="gold" className="text-xs">系統</Tag>
                </Tooltip>
              )}
              {!record.is_active && (
                <Tag color="red" className="text-xs">已停用</Tag>
              )}
            </div>
            <span className="text-gray-500 text-xs">{record.name}</span>
          </div>
        </div>
      ),
    },
    {
      title: '權限',
      key: 'permissions',
      render: (_, record) => {
        const permCodes = record.permissions.map(p => p.code)
        if (permCodes.includes('*:*')) {
          return <Tag color="gold" icon={<SafetyOutlined />}>完整存取權</Tag>
        }
        return (
          <div className="flex flex-wrap gap-1">
            {record.permissions.slice(0, 3).map(p => (
              <Tag key={p.code} className="text-xs">{p.code}</Tag>
            ))}
            {record.permissions.length > 3 && (
              <Tooltip title={record.permissions.slice(3).map(p => p.code).join(', ')}>
                <Tag className="text-xs">還有 {record.permissions.length - 3} 項</Tag>
              </Tooltip>
            )}
          </div>
        )
      },
    },
    {
      title: '上層角色',
      key: 'parent',
      render: (_, record) => {
        if (!record.parent_role_id) return <span className="text-gray-500">-</span>
        const parent = roles.find(r => r.id === record.parent_role_id)
        return parent ? (
          <Tag color="blue">{parent.display_name}</Tag>
        ) : (
          <span className="text-gray-500">-</span>
        )
      },
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
          {canManage && (
            <>
              <Button
                type="text"
                aria-label="編輯角色"
                icon={<EditOutlined />}
                onClick={() => handleEdit(record)}
                className="text-cyber-400 hover:text-cyber-300"
              />
              {!record.is_system && (
                <Button
                  type="text"
                  aria-label="刪除角色"
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
    <div className="p-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-white" style={{ fontFamily: 'var(--font-display)' }}>
            角色管理
          </h1>
          <p className="text-gray-400 mt-1">管理角色及其權限</p>
        </div>
        <div className="flex gap-2">
          <Button
            icon={<ReloadOutlined />}
            onClick={loadData}
            className="text-gray-400"
          >
            重新整理
          </Button>
          {canManage && (
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={handleCreate}
            >
              建立角色
            </Button>
          )}
        </div>
      </div>

      {/* Roles Table */}
      <div className="glass-card p-4">
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
            emptyText: <Empty description="找不到角色" />,
          }}
        />
      </div>

      {/* Create/Edit Modal */}
      <Modal
        title={editingRole ? `編輯角色：${editingRole.display_name}` : '建立新角色'}
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
              label="角色名稱（ID）"
              rules={[
                { required: true, message: '請輸入角色名稱' },
                { pattern: /^[a-z][a-z0-9_]*$/, message: '僅限小寫字母、數字與底線' },
              ]}
            >
              <Input
                placeholder="例如：moderator"
                disabled={!!editingRole}
              />
            </Form.Item>

            <Form.Item
              name="display_name"
              label="顯示名稱"
              rules={[{ required: true, message: '請輸入顯示名稱' }]}
            >
              <Input placeholder="例如：版主" />
            </Form.Item>
          </div>

          <Form.Item
            name="description"
            label="描述"
          >
            <Input.TextArea
              placeholder="角色描述..."
              rows={2}
            />
          </Form.Item>

          <Form.Item
            name="parent_role_id"
            label="上層角色（繼承其權限）"
          >
            <Select
              placeholder="選擇上層角色（選填）"
              allowClear
              options={roles
                .filter(r => r.id !== editingRole?.id)
                .map(r => ({ value: r.id, label: r.display_name }))}
            />
          </Form.Item>

          <Form.Item
            name="permission_codes"
            label="權限"
          >
            <div className="max-h-64 overflow-y-auto border border-gray-700 rounded-lg p-4">
              {permissions.map(category => (
                <div key={category.category} className="mb-4">
                  <h4 className="text-sm font-medium text-gray-300 mb-2 capitalize">
                    {category.category}
                  </h4>
                  <Form.Item name="permission_codes" noStyle>
                    <Checkbox.Group className="flex flex-wrap gap-2">
                      {category.permissions.map(perm => (
                        <Checkbox key={perm.code} value={perm.code}>
                          <Tooltip title={perm.description}>
                            <span className="text-gray-400">{perm.name}</span>
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
              取消
            </Button>
            <Button type="primary" htmlType="submit">
              {editingRole ? '更新' : '建立'}
            </Button>
          </div>
        </Form>
      </Modal>
    </div>
  )
}
