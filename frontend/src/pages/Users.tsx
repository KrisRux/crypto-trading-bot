import { useCallback, useState } from 'react'
import { api, UserItem } from '../api'
import { usePolling } from '../hooks/usePolling'
import { useLang } from '../hooks/useLang'

const ROLE_STYLES: Record<string, string> = {
  admin: 'bg-red-900 text-red-300',
  user: 'bg-blue-900 text-blue-300',
}

const ROLE_LABELS: Record<string, { it: string; en: string }> = {
  admin: { it: 'Admin — Accesso completo', en: 'Admin — Full access' },
  user: { it: 'Utente — Visualizza e configura', en: 'User — View & configure' },
}

export default function Users() {
  const { lang, l } = useLang()

  const fetchUsers = useCallback(() => api.getUsers(), [])
  const [users, , , refetch] = usePolling<UserItem[]>(fetchUsers, 15000)

  // New user form
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', display_name: '', role: 'user' })
  const [formError, setFormError] = useState('')

  // Edit state
  const [editId, setEditId] = useState<number | null>(null)
  const [editRole, setEditRole] = useState('')
  const [editPassword, setEditPassword] = useState('')

  const handleCreate = async () => {
    setFormError('')
    if (!form.username || !form.password) {
      setFormError(l('Username e password obbligatori', 'Username and password required'))
      return
    }
    try {
      await api.createUser({
        username: form.username,
        password: form.password,
        display_name: form.display_name || form.username,
        role: form.role,
      })
      setForm({ username: '', password: '', display_name: '', role: 'user' })
      setShowForm(false)
      refetch()
    } catch (e) {
      setFormError(String(e))
    }
  }

  const handleToggleActive = async (user: UserItem) => {
    try {
      await api.updateUser(user.id, { is_active: !user.is_active })
      refetch()
    } catch (e) {
      alert(String(e))
    }
  }

  const handleSaveEdit = async (userId: number) => {
    try {
      const update: Record<string, unknown> = {}
      if (editRole) update.role = editRole
      if (editPassword) update.password = editPassword
      await api.updateUser(userId, update)
      setEditId(null)
      setEditRole('')
      setEditPassword('')
      refetch()
    } catch (e) {
      alert(String(e))
    }
  }

  const handleDelete = async (user: UserItem) => {
    if (!confirm(l(
      `Eliminare l'utente "${user.username}"? Questa azione non e reversibile.`,
      `Delete user "${user.username}"? This cannot be undone.`
    ))) return
    try {
      await api.deleteUser(user.id)
      refetch()
    } catch (e) {
      alert(String(e))
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-white">
            {l('Gestione Utenti', 'User Management')}
          </h2>
          <p className="text-sm text-gray-400">
            {l('Crea e gestisci gli account che possono accedere al bot.', 'Create and manage accounts that can access the bot.')}
          </p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors"
        >
          {showForm ? l('Annulla', 'Cancel') : l('+ Nuovo Utente', '+ New User')}
        </button>
      </div>

      {/* Role legend */}
      <div className="flex flex-wrap gap-3 text-xs">
        {Object.entries(ROLE_LABELS).map(([role, labels]) => (
          <div key={role} className="flex items-center gap-1.5">
            <span className={`px-2 py-0.5 rounded font-bold uppercase ${ROLE_STYLES[role]}`}>{role}</span>
            <span className="text-gray-500">{labels[lang]}</span>
          </div>
        ))}
      </div>

      {/* Create user form */}
      {showForm && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3 max-w-lg">
          <h3 className="text-sm font-semibold text-white">{l('Nuovo Utente', 'New User')}</h3>
          <div className="grid grid-cols-2 gap-3">
            <input
              type="text"
              placeholder="Username"
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
            />
            <input
              type="password"
              placeholder="Password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
            />
            <input
              type="text"
              placeholder={l('Nome visualizzato', 'Display name')}
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
            />
            <select
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value })}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
            >
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          {formError && (
            <div className="text-sm text-red-400">{formError}</div>
          )}
          <button
            onClick={handleCreate}
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm rounded-lg transition-colors"
          >
            {l('Crea Utente', 'Create User')}
          </button>
        </div>
      )}

      {/* Users table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-400 border-b border-gray-800">
              <th className="text-left py-2 px-3">{l('Utente', 'User')}</th>
              <th className="text-left py-2 px-3">{l('Nome', 'Name')}</th>
              <th className="text-left py-2 px-3">{l('Ruolo', 'Role')}</th>
              <th className="text-left py-2 px-3">{l('Stato', 'Status')}</th>
              <th className="text-left py-2 px-3">{l('Ultimo accesso', 'Last login')}</th>
              <th className="text-right py-2 px-3">{l('Azioni', 'Actions')}</th>
            </tr>
          </thead>
          <tbody>
            {users?.map((u) => (
              <tr key={u.id} className="border-b border-gray-800/50 hover:bg-gray-900/50">
                <td className="py-2 px-3 font-medium text-white">{u.username}</td>
                <td className="py-2 px-3 text-gray-300">{u.display_name ?? '-'}</td>
                <td className="py-2 px-3">
                  {editId === u.id ? (
                    <select
                      value={editRole || u.role}
                      onChange={(e) => setEditRole(e.target.value)}
                      className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-white"
                    >
                      <option value="user">user</option>
                      <option value="admin">admin</option>
                    </select>
                  ) : (
                    <span className={`px-2 py-0.5 rounded text-xs font-bold uppercase ${ROLE_STYLES[u.role] ?? ROLE_STYLES.user}`}>
                      {u.role}
                    </span>
                  )}
                </td>
                <td className="py-2 px-3">
                  <button
                    onClick={() => handleToggleActive(u)}
                    className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
                      u.is_active
                        ? 'bg-emerald-900 text-emerald-300 hover:bg-emerald-800'
                        : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
                    }`}
                  >
                    {u.is_active ? l('Attivo', 'Active') : l('Disattivato', 'Disabled')}
                  </button>
                </td>
                <td className="py-2 px-3 text-gray-400 text-xs">
                  {u.last_login ? new Date(u.last_login).toLocaleString() : l('Mai', 'Never')}
                </td>
                <td className="py-2 px-3 text-right">
                  <div className="flex items-center justify-end gap-1">
                    {editId === u.id ? (
                      <>
                        <input
                          type="password"
                          placeholder={l('Nuova password', 'New password')}
                          value={editPassword}
                          onChange={(e) => setEditPassword(e.target.value)}
                          className="w-28 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-white"
                        />
                        <button
                          onClick={() => handleSaveEdit(u.id)}
                          className="px-2 py-1 bg-blue-600 hover:bg-blue-700 text-white text-xs rounded"
                        >
                          {l('Salva', 'Save')}
                        </button>
                        <button
                          onClick={() => { setEditId(null); setEditRole(''); setEditPassword('') }}
                          className="px-2 py-1 bg-gray-700 hover:bg-gray-600 text-white text-xs rounded"
                        >
                          {l('Annulla', 'Cancel')}
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() => { setEditId(u.id); setEditRole(u.role) }}
                          className="px-2 py-1 bg-gray-700 hover:bg-gray-600 text-white text-xs rounded"
                        >
                          {l('Modifica', 'Edit')}
                        </button>
                        <button
                          onClick={() => handleDelete(u)}
                          className="px-2 py-1 bg-red-900 hover:bg-red-800 text-red-300 text-xs rounded"
                        >
                          {l('Elimina', 'Delete')}
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
