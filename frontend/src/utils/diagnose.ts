// Frontend companion to the backend error diagnoser. The backend attaches a
// structured `result.diagnosis` (problem + suggestion) for recognized failures;
// this pulls the raw "smoking gun" lines out of the log tail so the failure
// callout can show the actual error text alongside the friendly explanation —
// and still shows something useful even when the backend recognized nothing.
// Real failure signatures. Deliberately specific so a module merely NAMED
// "urllib.error" / "traceback" in a progress line isn't mistaken for an error.
const ERROR_MARKERS =
  /(traceback \(most recent call last\)|[a-z_]*error:|modulenotfounderror|no module named|not found|errno\s*\d|assertionerror|unboundlocalerror|[a-z_]*exception:|permission denied|no space left|killed|failed to solve|failed to compute|=>\s*error|✗|^error\b)/i

// Build-tool progress/noise that often CONTAINS words like "error"/"traceback"
// as module names — never treat these as failure lines. (Docker's `=> ERROR`
// is a real failure and is matched above, so it must NOT be caught here.)
const NOISE =
  /nuitka-progress|modules\/s|^pass \d+:|optimizing module|dependency considerations|scons:|transferring/i

export function extractErrorLines(logs: string[] | undefined, max = 8): string[] {
  if (!logs || logs.length === 0) return []
  const hits: string[] = []
  // Deduplicate: a retry loop or a per-file failure prints the same line
  // hundreds of times, and without this it fills every one of the `max` slots
  // with one message — crowding out the lines that actually differ. Matches
  // what the server-side extractor does.
  const seen = new Set<string>()
  for (const line of logs) {
    const t = line.trim()
    if (!t || NOISE.test(t)) continue
    if (!ERROR_MARKERS.test(t)) continue
    if (seen.has(t)) continue
    seen.add(t)
    hits.push(t)
  }
  // The most relevant lines are usually the last ones before the process died.
  return hits.slice(-max)
}
