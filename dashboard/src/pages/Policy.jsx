import { useState, useEffect } from 'react'
import { fetchPolicy, updatePolicy } from '../api/client.js'
import { Save, RotateCcw } from 'lucide-react'


const ACTIONS = [
  { value: 'faker', label: 'Faker (substitute)', color: 'faker', desc: 'Replace with realistic fake value' },
  { value: 'redact', label: 'Redact (block)', color: 'redact', desc: 'Replace with [[REDACTED]]' },
  { value: 'keep', label: 'Keep (pass through)', color: 'keep', desc: 'No masking — leave as-is' },
]

export default function Policy() {
  const [policies, setPolicies] = useState([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState({})
  const [saving, setSaving] = useState(null)

  useEffect(() => {
    loadPolicies()
  }, [])

  async function loadPolicies() {
    try {
      const data = await fetchPolicy()
      setPolicies(data)
      const editState = {}
      data.forEach(p => { editState[p.id] = p.action })
      setEditing(editState)
    } catch (err) {
      console.error('Failed to load policies:', err)
    } finally {
      setLoading(false)
    }
  }

  async function savePolicy(id) {
    setSaving(id)
    try {
      await updatePolicy(id, editing[id])
      await loadPolicies()
    } catch (err) {
      alert('Failed to update policy: ' + (err.response?.data?.detail || err.message))
    } finally {
      setSaving(null)
    }
  }

  function resetPolicy(id, originalAction) {
    setEditing(prev => ({ ...prev, [id]: originalAction }))
  }

  if (loading) return <div className="loading-state">Loading policies...</div>

  const groups = {
    'Identity PII': policies.filter(p => ['PERSON', 'EMAIL', 'PHONE_NUMBER', 'USERNAME'].includes(p.entity_type)),
    'Financial / Secrets': policies.filter(p => ['CREDIT_CARD', 'IBAN', 'API_KEY', 'JWT', 'PEM_BLOCK', 'SSN'].includes(p.entity_type)),
    'Network / Other': policies.filter(p => ['URL', 'IPV4', 'IPV6', 'ORGANIZATION', 'ADDRESS'].includes(p.entity_type)),
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Policy Configuration</h1>
        <div className="header-info">
          Changes take effect on the next local backend sync cycle.
        </div>
      </div>

      {Object.entries(groups).map(([groupName, groupPolicies]) => (
        <div key={groupName} className="policy-group">
          <div className="policy-group-title">{groupName}</div>
          <div className="policy-table">
            <table>
              <thead>
                <tr>
                  <th>Entity Type</th>
                  <th>Current Action</th>
                  <th>Change To</th>
                  <th>Version</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {groupPolicies.map(p => {
                  const isDirty = editing[p.id] !== p.action
                  return (
                    <tr key={p.id} className={isDirty ? 'row-dirty' : ''}>
                      <td>
                        <code className="entity-code">{p.entity_type}</code>
                      </td>
                      <td>
                        <span className={`badge badge-${p.action}`}>{p.action}</span>
                      </td>
                      <td>
                        <select
                          value={editing[p.id] || p.action}
                          onChange={(e) => setEditing(prev => ({ ...prev, [p.id]: e.target.value }))}
                          className="action-select"
                        >
                          {ACTIONS.map(a => (
                            <option key={a.value} value={a.value}>{a.label}</option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <span className="version-tag">v{p.version}</span>
                      </td>
                      <td>
                        <div className="action-buttons">
                          <button
                            className="btn-icon btn-save"
                            disabled={!isDirty || saving === p.id}
                            onClick={() => savePolicy(p.id)}
                          >
                            <Save size={14} />
                            {saving === p.id ? 'Saving...' : 'Save'}
                          </button>
                          {isDirty && (
                            <button
                              className="btn-icon btn-reset"
                              onClick={() => resetPolicy(p.id, p.action)}
                            >
                              <RotateCcw size={14} />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  )
}
