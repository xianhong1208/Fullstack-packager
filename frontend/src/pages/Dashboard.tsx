import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Empty, message, notification, Spin, Button } from 'antd'
import { RocketOutlined } from '@ant-design/icons'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import TaskCard from '../components/TaskCard'
import BuildStatsPanel from '../components/BuildStatsPanel'
import { taskApi } from '../api/client'
import { useTaskStore } from '../store/taskStore'
import { getErrorDetail } from '../utils/errors'
import { STATUS_META } from '../utils/statusMeta'

/** A single KPI tile in the landing header, GitHub-Actions style: a big mono
 *  figure over a quiet uppercase label, colored by outcome. */
function Kpi({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="stat-card">
      <div
        className="text-xs uppercase tracking-wider"
        style={{ color: 'var(--ink-faint)', fontFamily: 'var(--font-mono)' }}
      >
        {label}
      </div>
      <div className="text-2xl font-semibold mt-1" style={{ color, fontFamily: 'var(--font-display)' }}>
        {value}
      </div>
    </div>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { setTasks, tasks } = useTaskStore()

  // Fetch active tasks
  const { data: activeTasks, isLoading } = useQuery({
    queryKey: ['activeTasks'],
    queryFn: taskApi.getActive,
    refetchInterval: 5000,
  })

  // Build outcome aggregates. Refreshed far less often than the active-task
  // poll — these move on the scale of hours, and a failed fetch must never
  // take the task list down with it.
  const { data: buildStats } = useQuery({
    queryKey: ['buildStats'],
    queryFn: () => taskApi.getBuildStats(30),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  // Update store when tasks are fetched
  useEffect(() => {
    if (activeTasks) {
      setTasks(activeTasks)
    }
  }, [activeTasks, setTasks])

  // Notify when a task leaves the active list (finished/failed/cancelled) so
  // the user isn't left staring at Dashboard with no idea it completed.
  const prevActiveRef = useRef<Map<string, string> | null>(null)
  useEffect(() => {
    if (!activeTasks) return
    const current = new Map(activeTasks.map((t) => [t.id, t.project_name]))
    if (prevActiveRef.current) {
      for (const [id, name] of prevActiveRef.current) {
        if (!current.has(id)) {
          notification.info({
            message: 'Task finished',
            description: `"${name}" left the in-progress list. Click to view the result.`,
            duration: 6,
            onClick: () => navigate(`/task/${id}`),
          })
        }
      }
    }
    prevActiveRef.current = current
  }, [activeTasks, navigate])

  // Cancel task mutation
  const cancelMutation = useMutation({
    mutationFn: taskApi.cancel,
    onSuccess: () => {
      message.success('Task cancelled')
      queryClient.invalidateQueries({ queryKey: ['activeTasks'] })
    },
    onError: (err) => {
      message.error(getErrorDetail(err, 'Failed to cancel task'))
    },
  })

  const handleCancel = (taskId: string) => {
    cancelMutation.mutate(taskId)
  }

  const taskList = Array.from(tasks.values())
  const runningCount = taskList.length
  const summary = buildStats?.summary
  const fmt = (n: number | undefined) => (n === undefined ? '—' : String(n))

  return (
    <div>
      {/* Page header — title + subtitle, matching the History landing. */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 24, fontWeight: 600, margin: 0, color: 'var(--ink)' }}>Dashboard</h1>
        <p style={{ margin: '4px 0 0', color: 'var(--ink-muted)' }}>
          An overview of your recent build activity and everything running right now.
        </p>
      </div>

      {/* KPI row — total / passed / failed / running, colored by outcome. */}
      <div
        className="grid gap-4 mb-6"
        style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}
      >
        <Kpi label="Total builds" value={fmt(summary?.total)} color="var(--ink)" />
        <Kpi label="Passed" value={fmt(summary?.completed)} color={STATUS_META.completed.color} />
        <Kpi label="Failed" value={fmt(summary?.failed)} color={STATUS_META.failed.color} />
        <Kpi label="Running" value={String(runningCount)} color={STATUS_META.running.color} />
      </div>

      {buildStats && <BuildStatsPanel stats={buildStats} />}

      <h2
        style={{ fontSize: 16, fontWeight: 600, margin: '0 0 12px', color: 'var(--ink)' }}
      >
        In progress
      </h2>

      {isLoading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin size="large" />
        </div>
      ) : taskList.length === 0 ? (
        <Empty
          description={
            <div>
              <div style={{ color: 'var(--ink-muted)' }}>No builds in progress</div>
              <div className="text-xs mt-1" style={{ color: 'var(--ink-faint)' }}>
                Start a build to package a project into an executable or a Docker image.
              </div>
            </div>
          }
          style={{ padding: 48 }}
        >
          <Button type="primary" icon={<RocketOutlined />} size="large" onClick={() => navigate('/create')}>
            Create your first build
          </Button>
        </Empty>
      ) : (
        <div className="flex flex-col gap-3">
          {taskList.map((task) => (
            <TaskCard key={task.id} task={task} onCancel={handleCancel} />
          ))}
        </div>
      )}
    </div>
  )
}
