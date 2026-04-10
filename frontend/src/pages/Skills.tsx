import { useCallback, useState, useEffect } from 'react'
import { api, SkillItem, SkillsSummary } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useLang } from '../hooks/useLang'

interface SyncStatus {
  timestamp: string | null
  status: string
  added: number
  updated: number
  error: string | null
}

const CATEGORY_LABELS: Record<string, { it: string; en: string }> = {
  'crypto-trading': { it: 'Crypto Trading', en: 'Crypto Trading' },
  'technical-strategies': { it: 'Strategie Tecniche', en: 'Technical Strategies' },
  'risk-management': { it: 'Gestione Rischio', en: 'Risk Management' },
  'chart-patterns': { it: 'Pattern Grafici', en: 'Chart Patterns' },
  'day-trading': { it: 'Day Trading', en: 'Day Trading' },
  'fundamental-analysis': { it: 'Analisi Fondamentale', en: 'Fundamental Analysis' },
  'ict-smart-money': { it: 'ICT / Smart Money', en: 'ICT / Smart Money' },
}

export default function Skills() {
  const { lang } = useLang()
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState<string | null>(null)
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null)

  const fetchSummary = useCallback(() => api.getSkillsSummary(), [])
  const fetchSkills = useCallback(
    () => api.getSkills(selectedCategory ?? undefined),
    [selectedCategory]
  )

  const [summary, , , refetchSummary] = usePolling<SkillsSummary>(fetchSummary, 60000)
  const [skills, , , refetchSkills] = usePolling<SkillItem[]>(fetchSkills, 60000)

  const t = (it: string, en: string) => (lang === 'it' ? it : en)

  // Load sync status on mount
  useEffect(() => {
    fetch('/api/skills/sync/status', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setSyncStatus(data) })
      .catch(() => {})
  }, [])

  const handleSync = async () => {
    setSyncing(true)
    setSyncResult(null)
    try {
      const res = await fetch('/api/skills/sync', { method: 'POST', credentials: 'include' })
      const data = await res.json()
      if (data.status === 'ok') {
        setSyncResult(`${data.added} added, ${data.updated} updated`)
        setSyncStatus({ timestamp: new Date().toISOString(), status: 'ok', added: data.added, updated: data.updated, error: null })
        refetchSummary()
        refetchSkills()
      } else {
        setSyncResult(`Error: ${data.details || 'unknown'}`)
      }
      setTimeout(() => setSyncResult(null), 5000)
    } catch (e) {
      setSyncResult(`Error: ${e}`)
    } finally {
      setSyncing(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold text-white mb-1">
            {t('Knowledge Base Embient', 'Embient Knowledge Base')}
          </h2>
          <p className="text-sm text-gray-400">
            {t(
              `${summary?.total_skills ?? 0} skill di trading caricate da agent-trading-skills (Embient AI). Queste regole e conoscenze alimentano la strategia "Embient Enhanced".`,
              `${summary?.total_skills ?? 0} trading skills loaded from agent-trading-skills (Embient AI). These rules and knowledge power the "Embient Enhanced" strategy.`
            )}
          </p>
          {syncStatus?.timestamp && (
            <p className="text-[11px] text-gray-600 mt-1">
              {t('Ultimo sync', 'Last sync')}: {new Date(syncStatus.timestamp).toLocaleString()}
              {syncStatus.status === 'ok'
                ? ` — ${syncStatus.added} added, ${syncStatus.updated} updated`
                : syncStatus.error ? ` — ${syncStatus.error}` : ''}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {syncResult && (
            <span className={`text-xs ${syncResult.startsWith('Error') ? 'text-red-400' : 'text-emerald-400'}`}>
              {syncResult}
            </span>
          )}
          <button
            onClick={handleSync}
            disabled={syncing}
            className="px-3 py-1.5 bg-blue-900/60 hover:bg-blue-800/70 border border-blue-800/50 text-blue-300 text-xs rounded transition-colors disabled:opacity-40 whitespace-nowrap"
          >
            {syncing ? t('Sincronizzazione...', 'Syncing...') : t('Sync da repo', 'Sync from repo')}
          </button>
        </div>
      </div>

      {/* Category filter */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setSelectedCategory(null)}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
            selectedCategory === null
              ? 'bg-blue-600 text-white'
              : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
          }`}
        >
          {t('Tutte', 'All')} ({summary?.total_skills ?? 0})
        </button>
        {summary &&
          Object.entries(summary.categories).map(([cat, count]) => (
            <button
              key={cat}
              onClick={() => setSelectedCategory(cat === selectedCategory ? null : cat)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                selectedCategory === cat
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
              }`}
            >
              {CATEGORY_LABELS[cat]?.[lang] ?? cat} ({count})
            </button>
          ))}
      </div>

      {/* Skills list */}
      <div className="space-y-3">
        {skills?.map((skill) => (
          <div
            key={skill.name}
            className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden"
          >
            {/* Header — always visible */}
            <button
              onClick={() =>
                setExpandedSkill(expandedSkill === skill.name ? null : skill.name)
              }
              className="w-full text-left px-4 py-3 flex items-center justify-between hover:bg-gray-800/50 transition-colors"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-white font-medium">
                    {skill.name.replace(/-/g, ' ')}
                  </span>
                  <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-gray-800 text-gray-400 uppercase">
                    {CATEGORY_LABELS[skill.category]?.[lang] ?? skill.category}
                  </span>
                </div>
                <p className="text-xs text-gray-400 mt-0.5 truncate">
                  {skill.description}
                </p>
              </div>
              <svg
                className={`w-4 h-4 text-gray-500 ml-3 flex-shrink-0 transition-transform ${
                  expandedSkill === skill.name ? 'rotate-180' : ''
                }`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {/* Expanded content */}
            {expandedSkill === skill.name && (
              <div className="border-t border-gray-800 px-4 py-4 space-y-4">
                {/* Description */}
                <p className="text-sm text-gray-300">{skill.description}</p>

                {/* Key rules */}
                {skill.key_rules.length > 0 && (
                  <div>
                    <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
                      {t('Regole Chiave', 'Key Rules')}
                    </h4>
                    <ul className="space-y-1">
                      {skill.key_rules.map((rule, i) => (
                        <li key={i} className="text-sm text-gray-300 flex gap-2">
                          <span className={`flex-shrink-0 ${
                            rule.toLowerCase().startsWith('never')
                              ? 'text-red-400'
                              : rule.toLowerCase().startsWith('always')
                              ? 'text-emerald-400'
                              : 'text-blue-400'
                          }`}>
                            {rule.toLowerCase().startsWith('never') ? '!' :
                             rule.toLowerCase().startsWith('always') ? '+' : '-'}
                          </span>
                          <span>{rule}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Full body (rendered as preformatted for now) */}
                <details className="group">
                  <summary className="text-xs text-blue-400 cursor-pointer hover:text-blue-300">
                    {t('Mostra contenuto completo', 'Show full content')}
                  </summary>
                  <pre className="mt-2 p-3 bg-gray-950 border border-gray-800 rounded text-xs text-gray-400 overflow-x-auto whitespace-pre-wrap max-h-96 overflow-y-auto">
                    {skill.body}
                  </pre>
                </details>

                {/* Meta */}
                <div className="text-[10px] text-gray-600 flex gap-4">
                  <span>v{skill.version}</span>
                  {skill.author && <span>{t('Autore', 'Author')}: {skill.author}</span>}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
