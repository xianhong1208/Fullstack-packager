import { describe, it, expect } from 'vitest'
import { extractErrorLines } from './diagnose'

/**
 * Client-side error-line extraction.
 *
 * This runs while a build is still going, before the server has written its own
 * error_lines — so it is the only thing a user watching a live failure sees.
 * The value is in what it EXCLUDES: a build log is thousands of lines of normal
 * progress output, and surfacing the wrong ones is no better than surfacing
 * none.
 */
describe('extractErrorLines', () => {
  it('picks out error-bearing lines and ignores progress output', () => {
    const logs = [
      'Nuitka-Options: Used command line options:',
      'Nuitka: Starting Python compilation',
      'ERROR: linker step failed',
      'Nuitka: Completed C compilation',
    ]
    expect(extractErrorLines(logs)).toEqual(['ERROR: linker step failed'])
  })

  it('returns nothing for a clean build', () => {
    // The other direction matters as much: inventing errors in a successful
    // build would train people to ignore the panel.
    const logs = ['Compiling module a', 'Compiling module b', 'Done']
    expect(extractErrorLines(logs)).toEqual([])
  })

  it('handles undefined logs without throwing', () => {
    // Called during render on a task whose logs have not loaded yet.
    expect(extractErrorLines(undefined)).toEqual([])
  })

  it('handles an empty array', () => {
    expect(extractErrorLines([])).toEqual([])
  })

  it('respects the max count', () => {
    const logs = Array.from({ length: 50 }, (_, i) => `ERROR: problem ${i}`)
    expect(extractErrorLines(logs, 3)).toHaveLength(3)
  })

  it('does not duplicate a message repeated many times', () => {
    // A retry loop can print the same failure hundreds of times; showing it
    // once leaves room for the lines that differ.
    const logs = Array.from({ length: 40 }, () => 'ERROR: same failure')
    expect(new Set(extractErrorLines(logs)).size).toBe(extractErrorLines(logs).length)
  })
})
