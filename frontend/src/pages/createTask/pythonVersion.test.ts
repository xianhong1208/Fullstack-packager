import { describe, it, expect } from 'vitest'
import { findPythonMismatch, pickDefaultRef } from './pythonVersion'

const detected = (version: string | null, detail = 'from _cffi_backend.cpython-312-x86_64-linux-gnu.so') => ({
  version,
  detail,
  inconsistent: false,
})

/**
 * Whether to warn that the chosen Python cannot load this project.
 *
 * The failure modes are asymmetric. A missed mismatch costs one failed build —
 * exactly what happened before this check existed. A false alarm costs much
 * more: people learn to dismiss the warning, and the next real one goes with
 * it. So most of these tests are about staying quiet.
 */
describe('findPythonMismatch — warns when it should', () => {
  it('flags an explicit choice that contradicts the detection', () => {
    // The real case from history: Agentic_RAG's packages are built for 3.12
    // and the task requested 3.13.
    const m = findPythonMismatch(detected('3.12'), '3.13')
    expect(m).toEqual({
      detected: '3.12',
      chosen: '3.13',
      detail: expect.stringContaining('cpython-312'),
    })
  })

  it('carries the evidence, not just a verdict', () => {
    // It is asking someone to override their own choice, so it has to show
    // which file it read.
    const m = findPythonMismatch(detected('3.12', 'from _x.cpython-312-...so'), '3.14')
    expect(m!.detail).toContain('cpython-312')
  })
})

describe('findPythonMismatch — stays silent when it should', () => {
  it('auto is never a mismatch', () => {
    // auto IS the fix; warning about it would be telling the user to apply
    // the thing they already applied.
    expect(findPythonMismatch(detected('3.12'), 'auto')).toBeNull()
  })

  it('says nothing when the choice matches', () => {
    expect(findPythonMismatch(detected('3.13'), '3.13')).toBeNull()
  })

  it('says nothing for a pure-Python project', () => {
    // No compiled extensions means no ABI to violate — it runs on anything.
    expect(findPythonMismatch(detected(null, 'no compiled extensions'), '3.13')).toBeNull()
  })

  it('says nothing when detection has not run yet', () => {
    // The analysis is fetched after the path is typed; warning before it
    // arrives would flash a false alarm on every keystroke.
    expect(findPythonMismatch(undefined, '3.13')).toBeNull()
  })

  it('says nothing when no version is chosen yet', () => {
    expect(findPythonMismatch(detected('3.12'), undefined)).toBeNull()
    expect(findPythonMismatch(detected('3.12'), '')).toBeNull()
  })
})

/**
 * Which branch to preselect once refs load. Choosing arbitrarily would
 * routinely queue a build of a stale branch, discovered only from the log.
 */
describe('pickDefaultRef', () => {
  const refs = (branches: string[], tags: string[] = []) => ({ branches, tags })

  it('prefers main', () => {
    expect(pickDefaultRef(refs(['develop', 'master', 'main']))).toBe('main')
  })

  it('falls back to master when there is no main', () => {
    expect(pickDefaultRef(refs(['develop', 'master']))).toBe('master')
  })

  it('falls back to the first branch when neither exists', () => {
    expect(pickDefaultRef(refs(['release/1.0', 'develop']))).toBe('release/1.0')
  })

  it('never overwrites a branch the user already chose', () => {
    // Refs are refetched when the URL is edited; clobbering the selection
    // would silently build something other than what is on screen.
    expect(pickDefaultRef(refs(['main']), 'feature/x')).toBeNull()
  })

  it('returns null for a repo with no branches', () => {
    expect(pickDefaultRef(refs([], ['v1.0']))).toBeNull()
  })
})
