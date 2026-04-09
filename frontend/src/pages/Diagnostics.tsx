import { useState } from 'react'

/**
 * Diagnostics page — admin-only.
 *
 * Renders the diagnostics dashboard served by GET /api/diagnostics.
 * The backend reads the dashboard HTML template, injects live data
 * (log + adaptive status), and returns a complete page.
 * The iframe sends the httpOnly auth cookie automatically (same-origin, path=/api).
 */
export default function Diagnostics() {
  const [key, setKey] = useState(0)

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Diagnostics</h2>
        <button
          onClick={() => setKey(k => k + 1)}
          className="px-3 py-1.5 bg-blue-900/60 hover:bg-blue-800/70 border border-blue-800/50 text-blue-300 text-xs rounded transition-colors"
        >
          Refresh
        </button>
      </div>
      <iframe
        key={key}
        src={`/api/diagnostics?_t=${key}`}
        className="w-full border border-gray-800 rounded-lg"
        style={{ height: 'calc(100vh - 140px)', minHeight: '600px', background: '#0d1117' }}
        title="Diagnostics Dashboard"
      />
    </div>
  )
}
