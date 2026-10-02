import { useState, useEffect } from 'react'
import axios from 'axios'
import { Trash2 } from 'lucide-react'

const API = axios.create({ baseURL: '/api' })

const EVENT_TYPES = {
  mask: { label: 'Mask', color: '#4f8cff' },
  detect: { label: 'Detect', color: '#2ecc71' },
  file: { label: 'File', color: '#f39c12' },
  fail_closed: { label: 'Fail-Closed', color: '#e74c3c' },
}

export default function AuditLog() {
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [filterType, setFilterType] = useState('')
  const [hours, setHours] = useState(24)

  useEffect(() => {
    loadEvents()
  }, [hours, filterType])

  async function loadEvents() {
    try {
      let url = `/audit?hours=${hours}&limit=200`
      if (filterType) url += `&event_type=${filterType}`
      const res = await API.get(url)
      setEvents(res.data)
    } catch (err) {
      console.error('Failed to load audit log:', err)
    } finally {
      setLoading(false)
    }
  }

  async function deleteEvent(id) {
    if (!confirm('Delete this audit event? This cannot be undone.')) return
    try {
      await API.delete(`/audit/${id}`)
      loadEvents()
    } catch (err) {
      alert('Failed to delete: ' + err.message)
    }
  }

  if (loading) return <div className="loading-state">Loading audit log...</div>

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Audit Log</h1>
        <div className="audit-filters">
          <select
            value={filterType}
            onChange={(e) => setFilterType(e.target.value)}
            className="filter-select"
          >
            <option value="">All event types</option>
            {Object.entries(EVENT_TYPES).map(([key, val]) => (
              <option key={key} value={key}>{val.label}</option>
            ))}
          </select>
          <select
            value={hours}
            onChange={(e) => setHours(parseInt(e.target.value))}
            className="filter-select"
          >
            <option value={1}>Last hour</option>
            <option value={24}>Last 24 hours</option>
            <option value={168}>Last 7 days</option>
            <option value={720}>Last 30 days</option>
          </select>
        </div>
      </div>

      <div className="table-card">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Event Type</th>
              <th>Entity Types</th>
              <th>Count</th>
              <th>Latency</th>
              <th>Conversation</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 ? (
              <tr>
                <td colSpan={7} style={{ textAlign: 'center', padding: 40, color: '#999' }}>
                  No audit events in this time window
                </td>
              </tr>
            ) : (
              events.map(e => {
                const typeInfo = EVENT_TYPES[e.event_type] || { label: e.event_type, color: '#888' }
                return (
                  <tr key={e.id}>
                    <td className="timestamp">{new Date(e.timestamp).toLocaleString()}</td>
                    <td>
                      <span
                        className="event-badge"
                        style={{ background: typeInfo.color + '15', color: typeInfo.color }}
                      >
                        {typeInfo.label}
                      </span>
                    </td>
                    <td>
                      {e.entity_types ? (
                        <div className="entity-tags">
                          {Object.entries(e.entity_types).map(([type, count]) => (
                            <span key={type} className="entity-tag">
                              {type}: {count}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="mono">{e.entity_count || '—'}</td>
                    <td className="mono">{e.latency_ms ? `${e.latency_ms}ms` : '—'}</td>
                    <td className="mono muted">{e.conversation_id ? e.conversation_id.slice(0, 12) + '...' : '—'}</td>
                    <td>
                      <button className="btn-icon-delete" onClick={() => deleteEvent(e.id)}>
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="table-footer">
        Showing {events.length} event{events.length !== 1 ? 's' : ''}
      </div>
    </div>
  )
}
