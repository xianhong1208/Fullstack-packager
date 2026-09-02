import { create } from 'zustand'
import type { TaskResponse, TaskStatus, WebSocketMessage } from '../api/types'

interface TaskState {
  tasks: Map<string, TaskResponse>
  currentTaskId: string | null

  // Actions
  setTasks: (tasks: TaskResponse[]) => void
  addTask: (task: TaskResponse) => void
  updateTask: (taskId: string, updates: Partial<TaskResponse>) => void
  removeTask: (taskId: string) => void
  appendLog: (taskId: string, log: string) => void
  setCurrentTask: (taskId: string | null) => void
  handleWebSocketMessage: (message: WebSocketMessage) => void

  // Selectors
  getTask: (taskId: string) => TaskResponse | undefined
  getActiveTasks: () => TaskResponse[]
}

export const useTaskStore = create<TaskState>((set, get) => ({
  tasks: new Map(),
  currentTaskId: null,

  setTasks: (tasks) => {
    const taskMap = new Map<string, TaskResponse>()
    tasks.forEach((task) => taskMap.set(task.id, task))
    set({ tasks: taskMap })
  },

  addTask: (task) => {
    set((state) => {
      const newTasks = new Map(state.tasks)
      newTasks.set(task.id, task)
      return { tasks: newTasks }
    })
  },

  updateTask: (taskId, updates) => {
    set((state) => {
      const task = state.tasks.get(taskId)
      if (!task) return state

      const newTasks = new Map(state.tasks)
      newTasks.set(taskId, { ...task, ...updates })
      return { tasks: newTasks }
    })
  },

  removeTask: (taskId) => {
    set((state) => {
      const newTasks = new Map(state.tasks)
      newTasks.delete(taskId)
      return { tasks: newTasks }
    })
  },

  appendLog: (taskId, log) => {
    set((state) => {
      const task = state.tasks.get(taskId)
      if (!task) return state

      const newTasks = new Map(state.tasks)
      newTasks.set(taskId, {
        ...task,
        logs: [...task.logs, log],
      })
      return { tasks: newTasks }
    })
  },

  setCurrentTask: (taskId) => {
    set({ currentTaskId: taskId })
  },

  handleWebSocketMessage: (message) => {
    const { type, task_id, data } = message

    switch (type) {
      case 'log':
        get().appendLog(task_id, data as string)
        break
      case 'status':
        get().updateTask(task_id, { status: data as TaskStatus })
        break
      case 'progress':
        get().updateTask(task_id, { progress: data as number })
        break
      case 'status_msg':
        get().updateTask(task_id, { status_msg: data as string })
        break
      default:
        break
    }
  },

  getTask: (taskId) => {
    return get().tasks.get(taskId)
  },

  getActiveTasks: () => {
    return Array.from(get().tasks.values()).filter(
      (task) => task.status === 'pending' || task.status === 'running'
    )
  },
}))
