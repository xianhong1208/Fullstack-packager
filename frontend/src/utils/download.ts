/** Trigger a browser file download from a Blob.
 *
 * Only appropriate for small, already-in-memory payloads (e.g. the history
 * CSV export). Build artifacts must NOT go through here — see
 * triggerUrlDownload.
 */
export function triggerDownload(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  // Delay cleanup so the browser has time to start the download
  setTimeout(() => {
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
  }, 100)
}

/** Hand a URL to the browser's own downloader.
 *
 * Build outputs run to several GB. Fetching one through XHR buffers the whole
 * response in page memory before the user sees anything — no resume, no
 * progress bar, and a tab that can die outright on a large Docker image. That
 * also wastes the Content-Length and HTTP Range support the server provides.
 * Letting the browser fetch the URL directly restores all of it: streamed to
 * disk, resumable, with the native progress UI.
 *
 * A hidden iframe rather than an <a> click: if the request fails (expired
 * ticket, artifact swept by the workspace TTL), the error response lands in the
 * iframe and the SPA keeps running. A link navigation would take the whole page
 * to a JSON error body.
 */
export function triggerUrlDownload(url: string) {
  const frame = document.createElement('iframe')
  frame.style.display = 'none'
  frame.src = url
  document.body.appendChild(frame)
  // Leave it long enough for the browser to commit the download, then reclaim.
  setTimeout(() => frame.remove(), 60_000)
}
