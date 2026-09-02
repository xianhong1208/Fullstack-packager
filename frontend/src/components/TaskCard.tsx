import { Progress, Button, Tooltip } from 'antd'
import { CloseOutlined, EyeOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import type { TaskResponse } from '../api/types'
import { STATUS_META } from '../utils/statusMeta'

interface TaskCardProps {
  task: TaskResponse
  onCancel?: (taskId: string) => void
}

const PROJECT_TYPE_LABEL: Record<string, string> = {
  backend_only: 'Backend',
  frontend_only: 'Frontend',
  fullstack: 'Full-stack',
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
    <div className="glass-card p-4 h-full flex flex-col">
      {/* Run identity: status glyph + project name, GitHub-Actions style. */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span style={{ color: config.color, fontSize: 16, display: 'inline-flex' }} aria-hidden="true">
              {config.icon}
            </span>
            <span
              className="font-semibold truncate"
              style={{ color: 'var(--ink)', fontFamily: 'var(--font-display)' }}
              title={task.project_name}
            >
              {task.project_name}
            </span>
          </div>
          <div
            className="flex items-center gap-2 mt-1 text-xs"
            style={{ color: 'var(--ink-faint)', fontFamily: 'var(--font-mono)' }}
          >
            <span style={{ color: config.color }}>{config.label}</span>
            <span aria-hidden>·</span>
            <span>{PROJECT_TYPE_LABEL[task.config.project_type] ?? task.config.project_type}</span>
            <span aria-hidden>·</span>
            <span>{PACK_MODE_LABEL[task.config.pack_mode] ?? task.config.pack_mode}</span>
          </div>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          <Tooltip title="View details">
            <Button
              type="text"
              aria-label="View task details"
              icon={<EyeOutlined />}
              onClick={() => navigate(`/task/${task.id}`)}
            />
          </Tooltip>
          {isActive && (
            <Tooltip title="Cancel task">
              <Button
                type="text"
                danger
                aria-label="Cancel task"
                icon={<CloseOutlined />}
                onClick={() => onCancel?.(task.id)}
              />
            </Tooltip>
          )}
        </div>
      </div>

      <div
        className="flex items-center gap-2 text-xs"
        style={{ color: 'var(--ink-faint)', fontFamily: 'var(--font-mono)' }}
      >
        <span>{task.user_name}</span>
        <span aria-hidden>·</span>
        <span>{new Date(task.created_at).toLocaleString()}</span>
      </div>

      {isActive && (
        <div className="mt-3">
          <Progress
            percent={task.progress}
            status={task.status === 'running' ? 'active' : 'normal'}
            size="small"
          />
          <div className="text-xs mt-1" style={{ color: 'var(--ink-muted)' }}>
            {task.status_msg}
          </div>
        </div>
      )}
    </div>
  )
}
