import { Card, Progress, Tag, Button, Tooltip, Space } from 'antd'
import { CloseOutlined, EyeOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import type { TaskResponse } from '../api/types'
import { STATUS_META } from '../utils/statusMeta'

interface TaskCardProps {
  task: TaskResponse
  onCancel?: (taskId: string) => void
}

const PROJECT_TYPE_LABEL: Record<string, string> = {
  backend_only: '後端',
  frontend_only: '前端',
  fullstack: '全端',
}

const PACK_MODE_LABEL: Record<string, string> = {
  full: 'Full',
  external: 'External',
}

export default function TaskCard({ task, onCancel }: TaskCardProps) {
  const navigate = useNavigate()
  const config = STATUS_META[task.status]

  const isActive = task.status === 'pending' || task.status === 'running'

  return (
    <Card
      size="small"
      title={
        <Space>
          <span>{task.project_name}</span>
          <Tag color={config.color} icon={config.icon}>
            {config.label}
          </Tag>
        </Space>
      }
      extra={
        <Space>
          <Tooltip title="查看詳情">
            <Button
              type="text"
              aria-label="查看任務詳情"
              icon={<EyeOutlined />}
              onClick={() => navigate(`/task/${task.id}`)}
            />
          </Tooltip>
          {isActive && (
            <Tooltip title="取消任務">
              <Button
                type="text"
                danger
                aria-label="取消任務"
                icon={<CloseOutlined />}
                onClick={() => onCancel?.(task.id)}
              />
            </Tooltip>
          )}
        </Space>
      }
      style={{ marginBottom: 16 }}
    >
      <div style={{ marginBottom: 8 }}>
        <Space size="small" style={{ marginBottom: 4 }}>
          <Tag>{PROJECT_TYPE_LABEL[task.config.project_type] ?? task.config.project_type}</Tag>
          <Tag>{PACK_MODE_LABEL[task.config.pack_mode] ?? task.config.pack_mode}</Tag>
        </Space>
        <br />
        <Space split="·" size="small" style={{ color: '#666', fontSize: 12 }}>
          <span>使用者：{task.user_name}</span>
          <span>{new Date(task.created_at).toLocaleString()}</span>
        </Space>
      </div>

      {isActive && (
        <>
          <Progress
            percent={task.progress}
            status={task.status === 'running' ? 'active' : 'normal'}
            size="small"
          />
          <div style={{ color: '#666', fontSize: 12, marginTop: 4 }}>
            {task.status_msg}
          </div>
        </>
      )}
    </Card>
  )
}
