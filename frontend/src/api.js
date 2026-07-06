const BASE = ''

async function json(url, options) {
  const res = await fetch(BASE + url, options)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`)
  return body
}

export const api = {
  providers: () => json('/api/providers/'),
  jobs: () => json('/api/jobs/'),
  job: (id) => json(`/api/jobs/${id}/`),
  createJob: (formData) => json('/api/jobs/', { method: 'POST', body: formData }),
  segments: (id, { offset = 0, limit = 50, flagged = false } = {}) =>
    json(`/api/jobs/${id}/segments/?offset=${offset}&limit=${limit}${flagged ? '&flagged=1' : ''}`),
  correct: (id, order, corrected) =>
    json(`/api/jobs/${id}/segments/${order}/correct/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ corrected }),
    }),
  rebuild: (id) => json(`/api/jobs/${id}/rebuild/`, { method: 'POST' }),
  tmStats: () => json('/api/tm/stats/'),
  glossary: () => json('/api/glossary/'),
  addTerm: (term, translation) =>
    json('/api/glossary/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ term, translation }),
    }),
  downloadUrl: (id, format) => `${BASE}/api/jobs/${id}/download/?format=${format}`,
}
