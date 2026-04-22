import { useCallback, useState } from 'react'
import { api, ApprovalRequestItem } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useLang } from '../hooks/useLang'
import { profileBadgeClass } from '../utils/colors'

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function statusBadgeClass(status: string | undefined): string {
  switch (status) {
    case 'pending':
      return 'bg-amber-900/50 text-amber-300 border border-amber-800/50'
    case 'approved':
      return 'bg-emerald-900/50 text-emerald-300 border border-emerald-800/50'
    case 'rejected':
      return 'bg-red-900/50 text-red-300 border border-red-800/50'
    case 'expired':
      return 'bg-gray-800 text-gray-400 border border-gray-700'
    default:
      return 'bg-gray-800 text-gray-400 border border-gray-700'
  }
}

export default function Approvals() {
  const { l } = useLang()

  const fetchPending = useCallback(() => api.getPendingApprovals(), [])
  const fetchAll = useCallback(() => api.getApprovals(), [])

  const [pending, , , refetchPending] = usePolling<ApprovalRequestItem[]>(fetchPending, 15000)
  const [all, loadingAll, , refetchAll] = usePolling<ApprovalRequestItem[]>(fetchAll, 30000)

  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState('')

  const handleAction = async (id: number, action: 'approve' | 'reject') => {
    setBusyId(id)
    setError('')
    try {
      if (action === 'approve') await api.approveRequest(id)
      else await api.rejectRequest(id)
      refetchPending()
      refetchAll()
    } catch (e) {
      setError(`${l('Azione fallita', 'Action failed')}: ${e}`)
    } finally {
      setBusyId(null)
    }
  }

  const pendingList = pending || []
  const history = (all || []).filter(r => r.status !== 'pending')

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold text-white">
          {l('Approvazioni', 'Approvals')}
        </h1>
        <p className="text-sm text-gray-400 mt-1">
          {l(
            'Richieste di cambio profilo create dalle switching rules quando richiedono conferma manuale.',
            'Profile-switch requests created by the switching rules when manual confirmation is required.'
          )}
        </p>
      </header>

      {error && (
        <div role="alert" className="bg-red-900/30 border border-red-800/50 text-red-300 px-4 py-2 rounded text-sm">
          {error}
        </div>
      )}

      {/* Pending section */}
      <section>
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
          {l('Pendenti', 'Pending')}
          {pendingList.length > 0 && (
            <span className="ml-2 px-2 py-0.5 rounded-full text-xs bg-amber-900/50 text-amber-300 border border-amber-800/50">
              {pendingList.length}
            </span>
          )}
        </h2>

        {pendingList.length === 0 ? (
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 text-sm text-gray-500">
            {l(
              'Nessuna richiesta pendente. Le richieste compaiono quando le switching rules propongono un profilo che richiede approvazione (es. aggressive_trend).',
              'No pending requests. Requests appear when switching rules propose a profile that requires approval (e.g. aggressive_trend).'
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {pendingList.map(req => (
              <div key={req.id} className="bg-gray-900 border border-gray-800 rounded-lg p-4">
                <div className="flex flex-wrap items-center gap-3 mb-3">
                  {req.from_profile && (
                    <span className={`px-2.5 py-1 rounded-full text-xs font-bold uppercase tracking-wider ${profileBadgeClass(req.from_profile)}`}>
                      {req.from_profile}
                    </span>
                  )}
                  <span className="text-gray-500">→</span>
                  {req.to_profile && (
                    <span className={`px-2.5 py-1 rounded-full text-xs font-bold uppercase tracking-wider ${profileBadgeClass(req.to_profile)}`}>
                      {req.to_profile}
                    </span>
                  )}
                  <span className="ml-auto text-xs text-gray-500">
                    #{req.id} · {l('creata', 'created')} {formatDate(req.created_at)}
                  </span>
                </div>

                {req.reason && (
                  <p className="text-sm text-gray-300 mb-3">{req.reason}</p>
                )}

                {req.expires_at && (
                  <p className="text-xs text-gray-500 mb-3">
                    {l('Scade', 'Expires')}: {formatDate(req.expires_at)}
                  </p>
                )}

                <div className="flex gap-2">
                  <button
                    onClick={() => handleAction(req.id, 'approve')}
                    disabled={busyId === req.id}
                    className="px-4 py-2 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
                  >
                    {busyId === req.id ? l('...', '...') : l('Approva', 'Approve')}
                  </button>
                  <button
                    onClick={() => handleAction(req.id, 'reject')}
                    disabled={busyId === req.id}
                    className="px-4 py-2 bg-red-800 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
                  >
                    {l('Rifiuta', 'Reject')}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* History section */}
      <section>
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
          {l('Storico', 'History')}
        </h2>

        <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
          {loadingAll && history.length === 0 ? (
            <div className="p-6 text-sm text-gray-500">{l('Caricamento...', 'Loading...')}</div>
          ) : history.length === 0 ? (
            <div className="p-6 text-sm text-gray-500">
              {l('Nessuno storico disponibile.', 'No history available.')}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-800/50">
                  <tr className="text-left text-xs text-gray-400 uppercase tracking-wider">
                    <th className="px-4 py-2">ID</th>
                    <th className="px-4 py-2">{l('Da → A', 'From → To')}</th>
                    <th className="px-4 py-2">Status</th>
                    <th className="px-4 py-2">{l('Risolto da', 'Resolved by')}</th>
                    <th className="px-4 py-2">{l('Risolto il', 'Resolved at')}</th>
                    <th className="px-4 py-2">{l('Motivo', 'Reason')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800">
                  {history.map(r => (
                    <tr key={r.id} className="text-gray-300">
                      <td className="px-4 py-2 text-gray-500">#{r.id}</td>
                      <td className="px-4 py-2">
                        <span className="text-xs text-gray-400">{r.from_profile}</span>
                        <span className="text-gray-600 mx-1">→</span>
                        <span className="text-xs text-gray-200">{r.to_profile}</span>
                      </td>
                      <td className="px-4 py-2">
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium uppercase ${statusBadgeClass(r.status)}`}>
                          {r.status}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-xs text-gray-400">{r.resolved_by || '-'}</td>
                      <td className="px-4 py-2 text-xs text-gray-500">{formatDate(r.resolved_at)}</td>
                      <td className="px-4 py-2 text-xs text-gray-400 max-w-md truncate" title={r.reason || ''}>
                        {r.reason || '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
