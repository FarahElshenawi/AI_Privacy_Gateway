// Thin client for cloud-backend /api/policy and /api/audit.
import axios from 'axios'

const API = axios.create({ baseURL: '/api' })

// === Policies ===

/** List all policies for an org. */
export async function fetchPolicy(orgId = 1) {
  const res = await API.get('/policies', { params: { org_id: orgId } })
  return res.data
}

/** Update a policy's action (faker | redact | keep). */
export async function updatePolicy(id, action) {
  const res = await API.put(`/policies/${id}`, { action })
  return res.data
}

// === Audit ===

/** Aggregated verification stats for the dashboard. */
export async function fetchAuditStats(hours = 24, orgId = 1) {
  const res = await API.get('/audit/stats', { params: { hours, org_id: orgId } })
  return res.data
}

/** List audit events with optional filters. */
export async function fetchAuditEvents({ hours = 24, eventType = '', limit = 200, orgId = 1 } = {}) {
  const params = { hours, limit, org_id: orgId }
  if (eventType) params.event_type = eventType
  const res = await API.get('/audit', { params })
  return res.data
}

/** Delete an audit event (GDPR right to erasure). */
export async function deleteAuditEvent(id) {
  await API.delete(`/audit/${id}`)
}
