import type { ReactNode } from 'react'
import {
  ClockCircleOutlined,
  SyncOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  StopOutlined,
  WarningOutlined,
  MinusCircleOutlined,
} from '@ant-design/icons'
import type { TaskStatus, VerifyStatus } from '../api/types'

// Single source of truth for task-status presentation. Previously copied
// verbatim into TaskDetail / HistoryDetail / TaskCard / History with three
// different label styles (English "Running" vs Traditional Chinese vs raw enum). Unified here.
export interface StatusMeta {
  color: string
  bgColor: string
  label: string // Traditional Chinese display label
  icon: ReactNode
}

export const STATUS_META: Record<TaskStatus, StatusMeta> = {
  pending: { color: '#93a0b0', bgColor: 'rgba(147,160,176,0.14)', label: '等待中', icon: <ClockCircleOutlined /> },
  running: { color: '#6ba6f7', bgColor: 'rgba(76,141,240,0.15)', label: '執行中', icon: <SyncOutlined spin /> },
  completed: { color: '#56d6a1', bgColor: 'rgba(51,196,137,0.15)', label: '已完成', icon: <CheckCircleOutlined /> },
  failed: { color: '#f27d7d', bgColor: 'rgba(236,95,95,0.15)', label: '失敗', icon: <CloseCircleOutlined /> },
  cancelled: { color: '#f0bd5e', bgColor: 'rgba(230,165,58,0.15)', label: '已取消', icon: <StopOutlined /> },
}

export interface VerifyMeta {
  color: string
  bg: string
  label: string
  icon: ReactNode
}

export const VERIFY_META: Record<VerifyStatus, VerifyMeta> = {
  pass: { color: '#56d6a1', bg: 'rgba(51,196,137,0.15)', label: '驗證通過', icon: <CheckCircleOutlined /> },
  warn: { color: '#f0bd5e', bg: 'rgba(230,165,58,0.15)', label: '驗證警告', icon: <WarningOutlined /> },
  fail: { color: '#f27d7d', bg: 'rgba(236,95,95,0.15)', label: '驗證失敗', icon: <CloseCircleOutlined /> },
  skipped: { color: '#93a0b0', bg: 'rgba(147,160,176,0.14)', label: '未驗證', icon: <MinusCircleOutlined /> },
}

// True once a task will no longer change — used to stop polling / WS.
export const TERMINAL_STATUSES: TaskStatus[] = ['completed', 'failed', 'cancelled']
export const isTerminal = (s: TaskStatus | undefined): boolean =>
  s !== undefined && TERMINAL_STATUSES.includes(s)
