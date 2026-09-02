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
  pending: { color: '#9ca3af', bgColor: 'rgba(156,163,175,0.15)', label: '等待中', icon: <ClockCircleOutlined /> },
  running: { color: '#f09a5e', bgColor: 'rgba(6,182,212,0.15)', label: '執行中', icon: <SyncOutlined spin /> },
  completed: { color: '#4ade80', bgColor: 'rgba(34,197,94,0.15)', label: '已完成', icon: <CheckCircleOutlined /> },
  failed: { color: '#f87171', bgColor: 'rgba(239,68,68,0.15)', label: '失敗', icon: <CloseCircleOutlined /> },
  cancelled: { color: '#fbbf24', bgColor: 'rgba(245,158,11,0.15)', label: '已取消', icon: <StopOutlined /> },
}

export interface VerifyMeta {
  color: string
  bg: string
  label: string
  icon: ReactNode
}

export const VERIFY_META: Record<VerifyStatus, VerifyMeta> = {
  pass: { color: '#4ade80', bg: 'rgba(34,197,94,0.15)', label: '驗證通過', icon: <CheckCircleOutlined /> },
  warn: { color: '#fbbf24', bg: 'rgba(245,158,11,0.15)', label: '驗證警告', icon: <WarningOutlined /> },
  fail: { color: '#f87171', bg: 'rgba(239,68,68,0.15)', label: '驗證失敗', icon: <CloseCircleOutlined /> },
  skipped: { color: '#9ca3af', bg: 'rgba(156,163,175,0.15)', label: '未驗證', icon: <MinusCircleOutlined /> },
}

// True once a task will no longer change — used to stop polling / WS.
export const TERMINAL_STATUSES: TaskStatus[] = ['completed', 'failed', 'cancelled']
export const isTerminal = (s: TaskStatus | undefined): boolean =>
  s !== undefined && TERMINAL_STATUSES.includes(s)
