import type { DetectedPython, GitRefs } from '../../api/types'

export interface PythonMismatch {
  /** Version the project's compiled extensions were built for. */
  detected: string
  /** Version the user explicitly picked. */
  chosen: string
  /** Which .so file the detection came from — the warning has to show its
   *  evidence, since it is asking someone to override their own choice. */
  detail: string
}

/**
 * Decide whether to warn that the selected Python cannot load this project.
 *
 * The failure modes are asymmetric, which is what the conditions below are
 * really about. Missing a real mismatch costs one failed build — the same
 * outcome as before this check existed. Firing on a healthy project costs far
 * more: people learn to dismiss the warning, and the next one that matters
 * gets dismissed with it.
 *
 * So it stays silent unless there is genuinely something to contradict:
 *   - "auto" is never a mismatch; it is the fix
 *   - a project with no compiled extensions runs on any version
 *   - a detection that failed has nothing to assert
 */
export function findPythonMismatch(
  detectedPython: DetectedPython | undefined,
  chosenVersion: string | undefined,
): PythonMismatch | null {
  const detected = detectedPython?.version
  if (!detected) return null
  if (!chosenVersion || chosenVersion === 'auto') return null
  if (chosenVersion === detected) return null
  return { detected, chosen: chosenVersion, detail: detectedPython?.detail ?? '' }
}

/**
 * The branch to preselect once a repo's refs load.
 *
 * Preference order is main, then master, then whatever came first. Picking
 * arbitrarily would routinely queue a build of a stale or unrelated branch,
 * and the user would only find out from the build log.
 */
export function pickDefaultRef(refs: GitRefs, currentRef?: string): string | null {
  if (currentRef) return null // never overwrite an explicit choice
  return (
    refs.branches.find((b) => b === 'main')
    ?? refs.branches.find((b) => b === 'master')
    ?? refs.branches[0]
    ?? null
  )
}
