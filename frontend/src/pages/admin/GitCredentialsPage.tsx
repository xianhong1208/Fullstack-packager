import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  App as AntApp,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { DeleteOutlined, EditOutlined, KeyOutlined, PlusOutlined } from '@ant-design/icons'
import {
  gitCredentialsApi,
  type GitCredential,
  type GitProvider,
} from '../../api/gitCredentialsApi'

const { Title, Paragraph, Text } = Typography

const PROVIDER_META: Record<GitProvider, { label: string; color: string; defaultHost: string }> = {
  github: { label: 'GitHub', color: 'purple', defaultHost: 'github.com' },
  gitlab: { label: 'GitLab', color: 'orange', defaultHost: 'gitlab.com' },
  generic: { label: '其他 (自架)', color: 'default', defaultHost: '' },
}

// Managing Git credentials replaces the old server-side GITLAB_TOKEN env var:
// tokens are entered here, stored encrypted, and picked automatically by the
// clone URL's host when a build runs.
export default function GitCredentialsPage() {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<GitCredential | null>(null)
  const [form] = Form.useForm()

  const { data: credentials = [], isLoading } = useQuery({
    queryKey: ['git-credentials'],
    queryFn: gitCredentialsApi.list,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['git-credentials'] })

  const createMutation = useMutation({
    mutationFn: gitCredentialsApi.create,
    onSuccess: () => {
      message.success('已新增憑證')
      setModalOpen(false)
      invalidate()
    },
    onError: (e: unknown) => message.error(errorText(e, '新增失敗')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: { label?: string; token?: string } }) =>
      gitCredentialsApi.update(id, payload),
    onSuccess: () => {
      message.success('已更新憑證')
      setModalOpen(false)
      invalidate()
    },
    onError: (e: unknown) => message.error(errorText(e, '更新失敗')),
  })

  const deleteMutation = useMutation({
    mutationFn: gitCredentialsApi.remove,
    onSuccess: () => {
      message.success('已刪除憑證')
      invalidate()
    },
    onError: (e: unknown) => message.error(errorText(e, '刪除失敗')),
  })

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ provider: 'github' })
    setModalOpen(true)
  }

  const openEdit = (cred: GitCredential) => {
    setEditing(cred)
    form.resetFields()
    form.setFieldsValue({ provider: cred.provider, host: cred.host, label: cred.label ?? '' })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    const values = await form.validateFields()
    if (editing) {
      // Only send fields that change; a blank token means "keep the current one".
      const payload: { label?: string; token?: string } = { label: values.label || null }
      if (values.token) payload.token = values.token
      updateMutation.mutate({ id: editing.id, payload })
    } else {
      createMutation.mutate({
        provider: values.provider,
        host: values.host?.trim() || null,
        label: values.label?.trim() || null,
        token: values.token,
      })
    }
  }

  const columns = [
    {
      title: '服務',
      dataIndex: 'provider',
      key: 'provider',
      render: (p: GitProvider) => <Tag color={PROVIDER_META[p].color}>{PROVIDER_META[p].label}</Tag>,
    },
    { title: '主機', dataIndex: 'host', key: 'host', render: (h: string) => <Text code>{h}</Text> },
    {
      title: '標籤',
      dataIndex: 'label',
      key: 'label',
      render: (l: string | null) => l || <Text type="secondary">—</Text>,
    },
    {
      title: 'Token',
      dataIndex: 'token_hint',
      key: 'token_hint',
      render: (hint: string | null) => (
        <Text type="secondary">••••{hint ?? ''}</Text>
      ),
    },
    {
      title: '最後使用',
      dataIndex: 'last_used_at',
      key: 'last_used_at',
      render: (v: string | null) =>
        v ? new Date(v).toLocaleString() : <Text type="secondary">從未</Text>,
    },
    {
      title: '',
      key: 'actions',
      width: 120,
      render: (_: unknown, cred: GitCredential) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(cred)} />
          <Popconfirm
            title="刪除這組憑證?"
            description="使用此主機的私有 repo 打包將無法認證。"
            okText="刪除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={() => deleteMutation.mutate(cred.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div className="max-w-4xl mx-auto p-6">
      <div className="flex items-center justify-between mb-2">
        <Title level={3} className="!mb-0">
          <KeyOutlined className="mr-2" />
          Git 憑證
        </Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新增憑證
        </Button>
      </div>
      <Paragraph type="secondary">
        設定用來 clone 私有 repo 的存取權杖(Personal Access Token)。系統依 Git URL 的主機自動選用對應的憑證,
        支援 GitHub、GitLab 及自架實例。權杖以加密方式儲存,不會再顯示明文。
      </Paragraph>

      <Card>
        <Table
          rowKey="id"
          loading={isLoading}
          columns={columns}
          dataSource={credentials}
          pagination={false}
          locale={{ emptyText: <Empty description="尚未設定任何 Git 憑證" /> }}
        />
      </Card>

      <Modal
        title={editing ? '編輯憑證' : '新增 Git 憑證'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSubmit}
        confirmLoading={createMutation.isPending || updateMutation.isPending}
        okText={editing ? '儲存' : '新增'}
        cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical" className="mt-4">
          <Form.Item name="provider" label="服務" rules={[{ required: true }]}>
            <Select
              disabled={!!editing}
              onChange={(p: GitProvider) => {
                if (!editing && !form.getFieldValue('host')) {
                  form.setFieldValue('host', PROVIDER_META[p].defaultHost)
                }
              }}
              options={(Object.keys(PROVIDER_META) as GitProvider[]).map((p) => ({
                value: p,
                label: PROVIDER_META[p].label,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="host"
            label="主機"
            tooltip="Git 伺服器的主機名,例如 github.com 或自架的 gitlab.example.com"
            rules={[
              {
                validator: (_, value) => {
                  const provider = form.getFieldValue('provider')
                  if (provider === 'generic' && !value?.trim()) {
                    return Promise.reject(new Error('自架服務必須填主機名'))
                  }
                  return Promise.resolve()
                },
              },
            ]}
          >
            <Input placeholder="留空則使用預設 (github.com / gitlab.com)" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="label" label="標籤 (選填)">
            <Input placeholder="例如:個人 GitHub" maxLength={128} />
          </Form.Item>
          <Form.Item
            name="token"
            label={editing ? '新的 Token (留空表示不變更)' : 'Access Token'}
            rules={editing ? [] : [{ required: true, message: '請輸入 Token' }]}
          >
            <Input.Password
              placeholder={editing ? '留空表示保留現有 Token' : 'ghp_… 或 glpat-…'}
              autoComplete="off"
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

function errorText(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : fallback
}
