import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Typography, Empty, Row, Col, message, notification, Spin, Button } from 'antd'
import { RocketOutlined } from '@ant-design/icons'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import TaskCard from '../components/TaskCard'
import BuildStatsPanel from '../components/BuildStatsPanel'
import { taskApi } from '../api/client'
import { useTaskStore } from '../store/taskStore'
import { getErrorDetail } from '../utils/errors'

const { Title } = Typography

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
            message: '任務已結束',
            description: `「${name}」已離開進行中清單，點擊查看結果。`,
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
      message.success('任務已取消')
      queryClient.invalidateQueries({ queryKey: ['activeTasks'] })
    },
    onError: (err) => {
      message.error(getErrorDetail(err, '取消任務失敗'))
    },
  })

  const handleCancel = (taskId: string) => {
    cancelMutation.mutate(taskId)
  }

  const taskList = Array.from(tasks.values())

  return (
    <div>
      <Row justify="space-between" align="middle" style={{ marginBottom: 24 }}>
        <Col>
          <Title level={3} style={{ margin: 0 }}>
            儀表板
          </Title>
        </Col>
      </Row>

      {buildStats && <BuildStatsPanel stats={buildStats} />}

      <Title level={5} style={{ marginBottom: 12 }}>
        進行中的任務
      </Title>

      {isLoading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin size="large" />
        </div>
      ) : taskList.length === 0 ? (
        <Empty
          description={
            <div>
              <div className="text-gray-300">目前沒有進行中的任務</div>
              <div className="text-gray-500 text-xs mt-1">建立一個任務,把專案打包成執行檔或 Docker image</div>
            </div>
          }
          style={{ padding: 48 }}
        >
          <Button type="primary" icon={<RocketOutlined />} size="large" onClick={() => navigate('/create')}>
            建立第一個任務
          </Button>
        </Empty>
      ) : (
        <Row gutter={[16, 16]}>
          {taskList.map((task) => (
            <Col xs={24} lg={12} xl={8} key={task.id}>
              <TaskCard task={task} onCancel={handleCancel} />
            </Col>
          ))}
        </Row>
      )}
    </div>
  )
}
