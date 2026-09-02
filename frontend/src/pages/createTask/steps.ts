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
    title: 'Basics',
    description:
      'Start with three things: the project name, whether you\'re building the frontend or backend, and where the code lives (a local path or Git). This is the only required step.',
  },
  {
    key: 'backend',
    title: 'Backend',
    description:
      'Set how Python is compiled into a binary. Most fields have defaults — leave anything you\'re unsure about blank or at its default and click Next.',
    needsBackend: true,
  },
  {
    key: 'frontend',
    title: 'Frontend',
    description:
      'Set how the frontend is built and whether to write a .env file. Use Auto-detect to fill in the build tool and output directory for you.',
    needsFrontend: true,
  },
  {
    key: 'docker',
    title: 'Docker',
    description: 'Also output a Docker image? If you don\'t need one, leave it off and continue.',
  },
  {
    key: 'review',
    title: 'Review',
    description: 'Check everything one more time. Once you start the build, the task is queued immediately.',
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
