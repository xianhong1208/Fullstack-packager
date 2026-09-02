import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  App as AntApp,
  Button,
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
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import {
  gitCredentialsApi,
  type GitCredential,
  type GitProvider,
} from '../../api/gitCredentialsApi'

const { Text } = Typography

const PROVIDER_META: Record<GitProvider, { label: string; color: string; defaultHost: string }> = {
  github: { label: 'GitHub', color: 'purple', defaultHost: 'github.com' },
  gitlab: { label: 'GitLab', color: 'orange', defaultHost: 'gitlab.com' },
  generic: { label: 'Other (self-hosted)', color: 'default', defaultHost: '' },
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
      message.success('Credential added')
      setModalOpen(false)
      invalidate()
    },
    onError: (e: unknown) => message.error(errorText(e, 'Failed to add credential')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: { label?: string; token?: string } }) =>
      gitCredentialsApi.update(id, payload),
    onSuccess: () => {
      message.success('Credential updated')
      setModalOpen(false)
      invalidate()
    },
    onError: (e: unknown) => message.error(errorText(e, 'Failed to update credential')),
  })

  const deleteMutation = useMutation({
    mutationFn: gitCredentialsApi.remove,
    onSuccess: () => {
      message.success('Credential deleted')
      invalidate()
    },
    onError: (e: unknown) => message.error(errorText(e, 'Failed to delete credential')),
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
      title: 'Provider',
      dataIndex: 'provider',
      key: 'provider',
      render: (p: GitProvider) => <Tag color={PROVIDER_META[p].color}>{PROVIDER_META[p].label}</Tag>,
    },
    { title: 'Host', dataIndex: 'host', key: 'host', render: (h: string) => <Text code>{h}</Text> },
    {
      title: 'Label',
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
      title: 'Last used',
      dataIndex: 'last_used_at',
      key: 'last_used_at',
      render: (v: string | null) =>
        v ? new Date(v).toLocaleString() : <Text type="secondary">Never</Text>,
    },
    {
      title: '',
      key: 'actions',
      width: 120,
      render: (_: unknown, cred: GitCredential) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(cred)} />
          <Popconfirm
            title="Delete this credential?"
            description="Builds cloning private repos from this host will fail to authenticate."
            okText="Delete"
            okButtonProps={{ danger: true }}
            cancelText="Cancel"
            onConfirm={() => deleteMutation.mutate(cred.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div className="max-w-4xl mx-auto">
      {/* Page header */}
      <div
        className="flex flex-wrap items-start justify-between gap-4 pb-4 mb-6"
        style={{ borderBottom: '1px solid var(--seam)' }}
      >
        <div>
          <h1 className="text-2xl font-semibold" style={{ fontFamily: 'var(--font-display)', color: 'var(--ink)' }}>
            Git credentials
          </h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--ink-muted)' }}>
            Access tokens used to clone private repositories during a build
          </p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          Add credential
        </Button>
      </div>

      <p className="mb-6 text-sm" style={{ color: 'var(--ink-muted)' }}>
        The matching credential is selected automatically by the host in the Git URL, with support
        for GitHub, GitLab, and self-hosted instances. Tokens are stored encrypted and are never
        shown again in plain text.
      </p>

      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={credentials}
        pagination={false}
        locale={{ emptyText: <Empty description="No Git credentials configured yet" /> }}
      />

      <Modal
        title={editing ? 'Edit credential' : 'Add Git credential'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSubmit}
        confirmLoading={createMutation.isPending || updateMutation.isPending}
        okText={editing ? 'Save' : 'Add'}
        cancelText="Cancel"
        destroyOnClose
      >
        <Form form={form} layout="vertical" className="mt-4">
          <Form.Item name="provider" label="Provider" rules={[{ required: true }]}>
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
            label="Host"
            tooltip="The Git server hostname, e.g. github.com or a self-hosted gitlab.example.com"
            rules={[
              {
                validator: (_, value) => {
                  const provider = form.getFieldValue('provider')
                  if (provider === 'generic' && !value?.trim()) {
                    return Promise.reject(new Error('A host is required for self-hosted providers'))
                  }
                  return Promise.resolve()
                },
              },
            ]}
          >
            <Input placeholder="Leave blank to use the default (github.com / gitlab.com)" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="label" label="Label (optional)">
            <Input placeholder="e.g. Personal GitHub" maxLength={128} />
          </Form.Item>
          <Form.Item
            name="token"
            label={editing ? 'New token (leave blank to keep current)' : 'Access token'}
            rules={editing ? [] : [{ required: true, message: 'Enter a token' }]}
          >
            <Input.Password
              placeholder={editing ? 'Leave blank to keep the current token' : 'ghp_… or glpat-…'}
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
