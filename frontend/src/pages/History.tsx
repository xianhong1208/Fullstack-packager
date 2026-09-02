import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Typography, Table, Button, Space, Tooltip, message, Select, Empty } from 'antd'
import {
  DownloadOutlined,
  RedoOutlined,
  CloudServerOutlined,
  DesktopOutlined,
  AppstoreOutlined,
  DockerOutlined,
  RocketOutlined,
} from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import type { ColumnsType } from 'antd/es/table'
import { taskApi } from '../api/client'
import type { HistoryItem, TaskStatus, ProjectType } from '../api/types'
import { triggerDownload, triggerUrlDownload } from '../utils/download'
import { useAuth } from '../contexts/AuthContext'
import { STATUS_META } from '../utils/statusMeta'
import { getErrorDetail } from '../utils/errors'

const { Title } = Typography

const projectTypeConfig: Record<ProjectType, { label: string; color: string; icon: React.ReactNode }> = {
  backend_only: { label: 'Backend', color: 'blue', icon: <CloudServerOutlined /> },
  frontend_only: { label: 'Frontend', color: 'green', icon: <DesktopOutlined /> },
  fullstack: { label: 'Full stack', color: 'purple', icon: <AppstoreOutlined /> },
}

export default function History() {
  const navigate = useNavigate()
  const { hasPermission } = useAuth()
  const [downloadingId, setDownloadingId] = useState<string | null>(null)

  // Only view_all holders can scope the list to another user. For view_own
  // users the backend forces their own rows, so the picker would be pointless.
  const canFilterByUser = hasPermission('history:view_all')
  const [userFilter, setUserFilter] = useState<string | undefined>(undefined)

  const { data: history, isLoading } = useQuery({
    queryKey: ['history', userFilter ?? null],
    queryFn: () => taskApi.getHistory(userFilter ? { user_name: userFilter } : undefined),
  })

  // Username options for the filter dropdown (admins only).
  const { data: historyUsers } = useQuery({
    queryKey: ['historyUsers'],
    queryFn: taskApi.listHistoryUsers,
    enabled: canFilterByUser,
  })

  const handleExport = async () => {
    try {
      const blob = await taskApi.exportHistory(userFilter)
      triggerDownload(blob, userFilter ? `history-${userFilter}.csv` : 'history.csv')
      message.success('History exported as CSV')
    } catch (err) {
      message.error(getErrorDetail(err, 'Could not export history'))
    }
  }

  const handleRebuild = (record: HistoryItem) => {
    if (!record.config) return
    navigate('/create', {
      state: {
        rebuild: true,
        projectName: record.project_name,
        config: record.config,
      },
    })
  }

  const handleDownload = async (record: HistoryItem) => {
    setDownloadingId(record.task_id)
    try {
      triggerUrlDownload(await taskApi.getOutputDownloadUrl(record.task_id))
      message.success('Download started')
    } catch (err: unknown) {
      message.error(getErrorDetail(err, 'Could not download the output'))
    } finally {
      setDownloadingId(null)
    }
  }

  const canDownload = (record: HistoryItem) =>
    record.status === 'completed'

  // Relative time in the GitHub Actions register ("3 minutes ago").
  const timeAgo = (iso: string): string => {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
    if (s < 60) return 'just now'
    const m = Math.floor(s / 60)
    if (m < 60) return `${m} min ago`
    const h = Math.floor(m / 60)
    if (h < 24) return `${h} h ago`
    const d = Math.floor(h / 24)
    if (d < 30) return `${d} d ago`
    return new Date(iso).toLocaleDateString()
  }

  const formatDuration = (start: string, end: string | null): string => {
    if (!end) return '-'
    const ms = new Date(end).getTime() - new Date(start).getTime()
    if (ms < 0) return '-'
    const seconds = Math.floor(ms / 1000)
    if (seconds < 60) return `${seconds}s`
    const minutes = Math.floor(seconds / 60)
    const remainSeconds = seconds % 60
    if (minutes < 60) return `${minutes}m ${remainSeconds}s`
    const hours = Math.floor(minutes / 60)
    const remainMinutes = minutes % 60
    return `${hours}h ${remainMinutes}m`
  }

  const columns: ColumnsType<HistoryItem> = [
    {
      // Status glyph leads the row, GitHub-Actions style.
      title: '',
      dataIndex: 'status',
      key: 'status',
      width: 44,
      align: 'center',
      render: (status: TaskStatus) => {
        const m = STATUS_META[status]
        return (
          <Tooltip title={m.label}>
            <span style={{ color: m.color, fontSize: 18, display: 'inline-flex' }}>{m.icon}</span>
          </Tooltip>
        )
      },
      filters: [
        { text: 'Running', value: 'running' },
        { text: 'Passed', value: 'completed' },
        { text: 'Failed', value: 'failed' },
        { text: 'Cancelled', value: 'cancelled' },
      ],
      onFilter: (value, record) => record.status === value,
    },
    {
      // Run identity: project name + a muted meta line (type / docker / who ran it).
      title: 'Build',
      key: 'run',
      render: (_, record) => {
        const type = record.config?.project_type
        const cfg = type ? projectTypeConfig[type] : null
        return (
          <div className="min-w-0">
            <a
              onClick={(e) => {
                e.stopPropagation()
                navigate(`/history/${record.task_id}`)
              }}
              style={{ color: 'var(--ink)', fontWeight: 600, cursor: 'pointer' }}
              className="hover:!text-cyber-300"
            >
              {record.project_name}
            </a>
            <div className="flex items-center gap-2 mt-0.5 text-xs" style={{ color: 'var(--ink-faint)' }}>
              {cfg && <span style={{ color: cfg.color }}>{cfg.label}</span>}
              {record.config?.docker_enabled && (
                <Tooltip title={record.config.docker_image_name || 'Docker enabled'}>
                  <DockerOutlined style={{ color: '#4c8df0' }} aria-label="Docker enabled" />
                </Tooltip>
              )}
              <span aria-hidden>·</span>
              <span>{record.user_name}</span>
            </div>
          </div>
        )
      },
      filters: [
        { text: 'Backend', value: 'backend_only' },
        { text: 'Frontend', value: 'frontend_only' },
        { text: 'Full stack', value: 'fullstack' },
      ],
      onFilter: (value, record) => record.config?.project_type === value,
    },
    {
      title: 'Started',
      dataIndex: 'start_time',
      key: 'start_time',
      width: 130,
      render: (time: string) => (
        <Tooltip title={new Date(time).toLocaleString()}>
          <span style={{ color: 'var(--ink-muted)' }}>{timeAgo(time)}</span>
        </Tooltip>
      ),
      sorter: (a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime(),
      defaultSortOrder: 'descend',
    },
    {
      title: 'Duration',
      key: 'duration',
      width: 100,
      render: (_, record) => (
        <span style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '0.85em', color: 'var(--ink-muted)' }}>
          {formatDuration(record.start_time, record.end_time)}
        </span>
      ),
      sorter: (a, b) => {
        const durationA = a.end_time ? new Date(a.end_time).getTime() - new Date(a.start_time).getTime() : 0
        const durationB = b.end_time ? new Date(b.end_time).getTime() - new Date(b.start_time).getTime() : 0
        return durationA - durationB
      },
    },
    {
      title: '',
      key: 'actions',
      width: 150,
      align: 'right',
      render: (_, record) => (
        <Space size="small">
          {record.config && (
            <Button
              size="small"
              icon={<RedoOutlined />}
              onClick={(e) => {
                e.stopPropagation()
                handleRebuild(record)
              }}
            >
              Rebuild
            </Button>
          )}
          {canDownload(record) && (
            <Tooltip title="Download output">
              <Button
                size="small"
                aria-label="Download output"
                icon={<DownloadOutlined />}
                loading={downloadingId === record.task_id}
                onClick={(e) => {
                  e.stopPropagation()
                  handleDownload(record)
                }}
              />
            </Tooltip>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Space
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          marginBottom: 24,
        }}
      >
        <Title level={3} style={{ margin: 0 }}>
          Builds
        </Title>
        <Space>
          {canFilterByUser && (
            <Select
              allowClear
              showSearch
              placeholder="Filter by user"
              style={{ width: 220 }}
              value={userFilter}
              onChange={(v) => setUserFilter(v)}
              options={(historyUsers ?? []).map((u) => ({ label: u, value: u }))}
              filterOption={(input, option) =>
                (option?.value as string)?.toLowerCase().includes(input.toLowerCase())
              }
            />
          )}
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            onClick={handleExport}
          >
            Export CSV
          </Button>
        </Space>
      </Space>

      <Table
        columns={columns}
        dataSource={history}
        rowKey="task_id"
        loading={isLoading}
        locale={{
          emptyText: (
            <Empty
              description={<span style={{ color: 'var(--ink-muted)' }}>No builds yet</span>}
              style={{ padding: 32 }}
            >
              <Button type="primary" icon={<RocketOutlined />} onClick={() => navigate('/create')}>
                Start your first build
              </Button>
            </Empty>
          ),
        }}
        pagination={{
          pageSize: 20,
          showSizeChanger: true,
          showTotal: (total) => `${total} builds`,
        }}
        scroll={{ x: 1100 }}
        onRow={(record) => ({
          onClick: () => navigate(`/history/${record.task_id}`),
          style: { cursor: 'pointer' },
        })}
      />
    </div>
  )
}
