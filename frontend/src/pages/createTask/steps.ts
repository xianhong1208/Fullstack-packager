import type { ProjectType } from '../../api/types'

export type StepKey = 'basic' | 'backend' | 'frontend' | 'docker' | 'review'

export interface StepDef {
  key: StepKey
  title: string
  /** Inline hint shown at the top of the step. Written for someone who does
   *  not know what the field names mean. */
  description: string
  needsBackend?: boolean
  needsFrontend?: boolean
}

export const ALL_STEPS: StepDef[] = [
  {
    key: 'basic',
    title: '基本資訊',
    description:
      '先填三件事:專案名稱、要打包前端還後端、程式碼放哪(本機路徑或 Git)。只有這頁是一定要填的。',
  },
  {
    key: 'backend',
    title: '後端設定',
    description:
      '設定怎麼把 Python 編成執行檔。大多有預設值,不確定的欄位直接留空或保持預設,按「下一步」即可。',
    needsBackend: true,
  },
  {
    key: 'frontend',
    title: '前端設定',
    description:
      '設定前端怎麼 build,以及要不要寫入 .env。可按「自動偵測」幫你帶入工具與輸出目錄。',
    needsFrontend: true,
  },
  {
    key: 'docker',
    title: 'Docker',
    description: '要不要額外輸出成 Docker image?不需要就保持「否」直接下一步。',
  },
  {
    key: 'review',
    title: '確認送出',
    description: '最後檢查一遍所有設定。按下送出後任務會立刻開始排隊。',
  },
]

export function hasBackend(projectType: ProjectType | undefined): boolean {
  return projectType === 'backend_only' || projectType === 'fullstack'
}

export function hasFrontend(projectType: ProjectType | undefined): boolean {
  return projectType === 'frontend_only' || projectType === 'fullstack'
}

/**
 * The steps a given project type actually needs.
 *
 * Getting this wrong is not cosmetic: a step that is wrongly hidden takes its
 * required fields with it, and the build runs with defaults the user never
 * saw. A step wrongly shown asks someone packaging a pure frontend to answer
 * questions about Nuitka.
 */
export function activeSteps(projectType: ProjectType | undefined): StepDef[] {
  const backend = hasBackend(projectType)
  const frontend = hasFrontend(projectType)
  return ALL_STEPS.filter(
    (s) => (!s.needsBackend || backend) && (!s.needsFrontend || frontend),
  )
}

/**
 * Keep a step index inside the bounds of the current step list.
 *
 * The list shrinks when the project type changes mid-form — switching from
 * Full Stack to Frontend Only removes a step. Without clamping, the index can
 * point past the end and the wizard renders nothing at all.
 */
export function clampStepIndex(index: number, steps: readonly unknown[]): number {
  if (steps.length === 0) return 0
  return Math.min(Math.max(0, index), steps.length - 1)
}
