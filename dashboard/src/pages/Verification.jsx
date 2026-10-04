import { useState, useEffect } from 'react'
import { fetchAuditStats } from '../api/client.js'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, AreaChart, Area, LineChart, Line,
} from 'recharts'
import { Activity, ShieldAlert, Zap, Eye } from 'lucide-react'

const COLORS = ['#4f8cff', '#2ecc71', '#e74c3c', '#f39c12', '#9b59b6', '#1abc9c', '#34495e', '#e67e22']

export default function Verification() {
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [hours, setHours] = useState(24)

  useEffect(() => {
    loadStats()
    const interval = setInterval(loadStats, 30000) // auto-refresh every 30s
    return () => clearInterval(interval)
  }, [hours])

  async function loadStats() {
    try {
      const data = await fetchAuditStats(hours)
      setStats(data)
    } catch (err) {
      console.error('Failed to load stats:', err)
    } finally {
      setLoading(false)
    }
  }

  if (loading) return <div className="loading-state">Loading statistics...</div>
  if (!stats) return <div className="empty-state">No data available yet</div>

  const entityData = Object.entries(stats.entity_type_breakdown || {}).map(([name, value]) => ({ name, value }))
  const eventData = Object.entries(stats.by_event_type || {}).map(([name, count]) => ({ name, count }))

  const statCards = [
    { label: 'Total Events', value: stats.total_events, icon: Activity, color: '#4f8cff' },
    { label: 'Entities Masked', value: stats.total_entities_masked, icon: Eye, color: '#2ecc71' },
    { label: 'Fail-Closed Blocks', value: stats.fail_closed_events, icon: ShieldAlert, color: stats.fail_closed_events > 0 ? '#e74c3c' : '#2ecc71' },
    { label: 'Avg Latency (ms)', value: stats.avg_latency_ms || '—', icon: Zap, color: '#f39c12' },
  ]

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Verification & Statistics</h1>
        <div className="time-selector">
          {[1, 24, 168, 720].map(h => (
            <button
              key={h}
              className={`time-btn ${hours === h ? 'active' : ''}`}
              onClick={() => setHours(h)}
            >
              {h === 1 ? '1H' : h === 24 ? '24H' : h === 168 ? '7D' : '30D'}
            </button>
          ))}
        </div>
      </div>

      {/* Stat Cards */}
      <div className="stat-grid">
        {statCards.map((card, i) => {
          const Icon = card.icon
          return (
            <div key={i} className="stat-card">
              <div className="stat-card-top">
                <div className="stat-icon-wrap" style={{ background: card.color + '15' }}>
                  <Icon size={20} color={card.color} />
                </div>
                <div className="stat-label">{card.label}</div>
              </div>
              <div className="stat-value" style={{ color: card.color }}>{card.value}</div>
            </div>
          )
        })}
      </div>

      {/* Charts */}
      <div className="chart-grid">
        <div className="chart-card">
          <div className="chart-header">
            <h3>Events by Type</h3>
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={eventData} barSize={40}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#888' }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fontSize: 11, fill: '#888' }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{ borderRadius: 8, border: '1px solid #eee', fontSize: 13 }}
                cursor={{ fill: '#f8f9fa' }}
              />
              <Bar dataKey="count" fill="#4f8cff" radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-card">
          <div className="chart-header">
            <h3>Entity Types Masked</h3>
          </div>
          {entityData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie
                  data={entityData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  outerRadius={90}
                  innerRadius={50}
                  paddingAngle={3}
                >
                  {entityData.map((_, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #eee', fontSize: 13 }} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div className="chart-empty">No masking events yet</div>
          )}
          {entityData.length > 0 && (
            <div className="legend-row">
              {entityData.map((e, i) => (
                <div key={e.name} className="legend-item">
                  <span className="legend-dot" style={{ background: COLORS[i % COLORS.length] }}></span>
                  <span className="legend-text">{e.name}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
