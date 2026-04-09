import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../api'

/*
 * Diagnostics page — admin-only.
 *
 * Fetches /api/diagnostics (log + adaptive status) and injects the data
 * into the self-contained diagnostics dashboard loaded in an iframe.
 * The dashboard HTML is served as a static file from /dashboard/diagnostics.html,
 * but we load it via srcdoc to inject data programmatically.
 */

export default function Diagnostics() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [dashHtml, setDashHtml] = useState('')
  const [hasData, setHasData] = useState(false)
  const iframeRef = useRef<HTMLIFrameElement>(null)

  // Load the dashboard HTML template on mount
  useEffect(() => {
    fetch('/dashboard/diagnostics.html')
      .then(r => {
        if (!r.ok) throw new Error(`Failed to load dashboard: ${r.status}`)
        return r.text()
      })
      .then(html => setDashHtml(html))
      .catch(e => setError(`Cannot load dashboard template: ${e.message}`))
  }, [])

  const loadData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await api.getDiagnostics(3000)
      if (!dashHtml) {
        setError('Dashboard template not loaded yet')
        return
      }

      // Inject a script that auto-loads the data and calls analyze()
      const initScript = `
<script>
window.addEventListener('DOMContentLoaded', function() {
  // Load JSON status
  var statusData = ${JSON.stringify(data.status)};
  loadJsonStatus(statusData);

  // Parse log lines
  var logText = ${JSON.stringify(data.log)};
  if (logText) parseLog(logText);

  // Hide drop zone and auto-analyze
  document.getElementById('dropZone').classList.add('hidden');
  document.getElementById('btnAnalyze').disabled = false;
  analyze();
});
<\/script>`

      // Insert the init script before </body>
      const modifiedHtml = dashHtml.replace('</body>', initScript + '\n</body>')
      setHasData(true)

      // Update iframe srcdoc
      if (iframeRef.current) {
        iframeRef.current.srcdoc = modifiedHtml
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [dashHtml])

  // Auto-load on mount once template is ready
  useEffect(() => {
    if (dashHtml && !hasData) {
      loadData()
    }
  }, [dashHtml, hasData, loadData])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Diagnostics</h2>
        <div className="flex gap-2">
          <button
            onClick={loadData}
            disabled={loading || !dashHtml}
            className="px-3 py-1.5 bg-blue-900/60 hover:bg-blue-800/70 border border-blue-800/50 text-blue-300 text-xs rounded transition-colors disabled:opacity-40"
          >
            {loading ? 'Loading...' : 'Refresh'}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-900/30 border border-red-800/50 text-red-300 px-4 py-2 rounded text-sm">
          {error}
        </div>
      )}

      {!dashHtml && !error && (
        <div className="text-gray-500 text-sm">Loading dashboard template...</div>
      )}

      <iframe
        ref={iframeRef}
        className="w-full border border-gray-800 rounded-lg bg-[#0d1117]"
        style={{ height: 'calc(100vh - 160px)', minHeight: '600px' }}
        sandbox="allow-scripts allow-same-origin"
        title="Diagnostics Dashboard"
      />
    </div>
  )
}
