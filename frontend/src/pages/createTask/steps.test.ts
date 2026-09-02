import { describe, it, expect } from 'vitest'
import { ALL_STEPS, activeSteps, clampStepIndex, hasBackend, hasFrontend } from './steps'

/**
 * Which steps a project type gets.
 *
 * This is not presentation. A step that is wrongly hidden takes its required
 * fields with it and the build silently runs on defaults nobody chose; a step
 * wrongly shown asks someone packaging a static site to configure Nuitka.
 */
describe('activeSteps', () => {
  const keys = (t: Parameters<typeof activeSteps>[0]) => activeSteps(t).map((s) => s.key)

  it('backend_only skips the frontend step', () => {
    expect(keys('backend_only')).toEqual(['basic', 'backend', 'docker', 'review'])
  })

  it('frontend_only skips the backend step', () => {
    expect(keys('frontend_only')).toEqual(['basic', 'frontend', 'docker', 'review'])
  })

  it('fullstack gets every step', () => {
    expect(keys('fullstack')).toEqual(ALL_STEPS.map((s) => s.key))
  })

  it('always keeps the steps that apply to every project type', () => {
    // basic collects the required fields and review is the only way to submit;
    // losing either would strand the user regardless of project type.
    for (const t of ['backend_only', 'frontend_only', 'fullstack'] as const) {
      expect(keys(t)).toContain('basic')
      expect(keys(t)).toContain('review')
      expect(keys(t)).toContain('docker')
    }
  })

  it('falls back to the always-present steps for an unknown type', () => {
    // An older history record could carry a project_type this build does not
    // know. Showing the common steps beats rendering an empty wizard.
    expect(keys(undefined)).toEqual(['basic', 'docker', 'review'])
  })

  it('every step has a description written for a non-expert', () => {
    for (const s of ALL_STEPS) {
      expect(s.description.length).toBeGreaterThan(10)
      expect(s.title).toBeTruthy()
    }
  })
})

describe('hasBackend / hasFrontend', () => {
  it('fullstack counts as both', () => {
    expect(hasBackend('fullstack')).toBe(true)
    expect(hasFrontend('fullstack')).toBe(true)
  })

  it('single-sided types count as one', () => {
    expect(hasBackend('backend_only')).toBe(true)
    expect(hasFrontend('backend_only')).toBe(false)
    expect(hasFrontend('frontend_only')).toBe(true)
    expect(hasBackend('frontend_only')).toBe(false)
  })

  it('undefined counts as neither', () => {
    expect(hasBackend(undefined)).toBe(false)
    expect(hasFrontend(undefined)).toBe(false)
  })
})

describe('clampStepIndex', () => {
  it('keeps a valid index untouched', () => {
    expect(clampStepIndex(2, [1, 2, 3, 4])).toBe(2)
  })

  it('pulls an out-of-range index back to the last step', () => {
    // The real scenario: the user is on the frontend step of a Full Stack
    // build and switches to Backend Only. The list shrinks under them, and
    // without this the wizard renders a blank page.
    expect(clampStepIndex(4, [1, 2, 3])).toBe(2)
  })

  it('never returns a negative index', () => {
    expect(clampStepIndex(-3, [1, 2, 3])).toBe(0)
  })

  it('handles an empty step list', () => {
    expect(clampStepIndex(2, [])).toBe(0)
  })

  it('switching project type mid-form lands on a real step', () => {
    const before = activeSteps('fullstack')
    const idxOfFrontend = before.findIndex((s) => s.key === 'frontend')
    const after = activeSteps('backend_only')

    const clamped = clampStepIndex(idxOfFrontend, after)

    expect(after[clamped]).toBeDefined()
    expect(clamped).toBeLessThan(after.length)
  })
})
