import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Typography, Table, Tag, Button, Space, Tooltip, message, Select, Empty } from 'antd'
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
  backend_only: { label: '後端', color: 'blue', icon: <CloudServerOutlined /> },
  frontend_only: { label: '前端', color: 'green', icon: <DesktopOutlined /> },
  fullstack: { label: '全端', color: 'purple', icon: <AppstoreOutlined /> },
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
      message.success('已匯出歷史紀錄 CSV')
    } catch (err) {
      message.error(getErrorDetail(err, '匯出歷史紀錄失敗'))
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
      message.success('已開始下載產出')
    } catch (err: unknown) {
      message.error(getErrorDetail(err, '下載產出失敗'))
    } finally {
      setDownloadingId(null)
    }
  }

  const canDownload = (record: HistoryItem) =>
    record.status === 'completed'

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
      title: '專案',
      dataIndex: 'project_name',
      key: 'project_name',
      width: 160,
      render: (name: string, record) => (
        <a
          onClick={(e) => {
            e.stopPropagation()
            navigate(`/history/${record.task_id}`)
          }}
          style={{ color: '#6ba6f7', cursor: 'pointer' }}
        >
          {name}
        </a>
      ),
    },
    {
      title: '類型',
      key: 'project_type',
      width: 120,
      render: (_, record) => {
        const type = record.config?.project_type
        if (!type) return <Tag>未知</Tag>
        const cfg = projectTypeConfig[type]
        return (
          <Tag icon={cfg.icon} color={cfg.color}>
            {cfg.label}
          </Tag>
        )
      },
      filters: [
        { text: '後端', value: 'backend_only' },
        { text: '前端', value: 'frontend_only' },
        { text: '全端', value: 'fullstack' },
      ],
      onFilter: (value, record) => record.config?.project_type === value,
    },
    {
      title: 'Docker',
      key: 'docker',
      width: 70,
      align: 'center',
      render: (_, record) => {
        if (!record.config?.docker_enabled) return <span style={{ color: '#4b5563' }}>-</span>
        return (
          <Tooltip title={record.config.docker_image_name || '已啟用 Docker'}>
            <DockerOutlined style={{ color: '#2496ED', fontSize: 16 }} aria-label="已啟用 Docker" />
          </Tooltip>
        )
      },
      filters: [
        { text: '有 Docker', value: true },
        { text: '無 Docker', value: false },
      ],
      onFilter: (value, record) => (record.config?.docker_enabled ?? false) === value,
    },
    {
      title: '使用者',
      dataIndex: 'user_name',
      key: 'user_name',
      width: 100,
    },
    {
      title: '狀態',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: TaskStatus) => {
        const m = STATUS_META[status]
        return (
          <Tag color={m.color} icon={m.icon}>
            {m.label}
          </Tag>
        )
      },
      filters: [
        { text: '已完成', value: 'completed' },
        { text: '失敗', value: 'failed' },
        { text: '已取消', value: 'cancelled' },
      ],
      onFilter: (value, record) => record.status === value,
    },
    {
      title: '開始時間',
      dataIndex: 'start_time',
      key: 'start_time',
      width: 160,
      render: (time: string) => new Date(time).toLocaleString(),
      sorter: (a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime(),
      defaultSortOrder: 'descend',
    },
    {
      title: '耗時',
      key: 'duration',
      width: 100,
      render: (_, record) => (
        <span style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '0.85em' }}>
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
      title: '操作',
      key: 'actions',
      width: 160,
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
              重建
            </Button>
          )}
          {canDownload(record) && (
            <Tooltip title="下載產出">
              <Button
                size="small"
                aria-label="下載產出"
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
          建置歷史
        </Title>
        <Space>
          {canFilterByUser && (
            <Select
              allowClear
              showSearch
              placeholder="篩選使用者"
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
            匯出 CSV
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
              description={<span className="text-gray-400">尚無建置紀錄</span>}
              style={{ padding: 32 }}
            >
              <Button type="primary" icon={<RocketOutlined />} onClick={() => navigate('/create')}>
                建立第一個任務
              </Button>
            </Empty>
          ),
        }}
        pagination={{
          pageSize: 20,
          showSizeChanger: true,
          showTotal: (total) => `共 ${total} 筆紀錄`,
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
