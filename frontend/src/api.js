// Тонкий клиент REST API: fetch + разбор ошибок ({error} из JSON).
// Все пути бэкенда собраны здесь, компоненты работают только через api.*
const BASE = ''

const TOKEN_STORAGE_KEY = 'pdftransl.apiToken'

function initialToken() {
  if (typeof window === 'undefined') return ''
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const supplied = fragment.get('token')
  if (supplied) {
    window.sessionStorage.setItem(TOKEN_STORAGE_KEY, supplied)
    // Do not leave a credential in the address bar or browser history.
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
  }
  return window.sessionStorage.getItem(TOKEN_STORAGE_KEY) || ''
}

let apiToken = initialToken()

function authHeaders(headers = {}) {
  return apiToken ? { ...headers, Authorization: `Bearer ${apiToken}` } : headers
}

async function json(url, options) {
  const res = await fetch(BASE + url, {
    ...options,
    headers: authHeaders(options?.headers),
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`)
  return body
}

export const api = {
  setToken: (token) => {
    apiToken = token.trim()
    if (apiToken) window.sessionStorage.setItem(TOKEN_STORAGE_KEY, apiToken)
    else window.sessionStorage.removeItem(TOKEN_STORAGE_KEY)
  },
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
  pauseJob: (id) => json(`/api/jobs/${id}/pause/`, { method: 'POST' }),
  resumeJob: (id) => json(`/api/jobs/${id}/resume/`, { method: 'POST' }),
  tmStats: () => json('/api/tm/stats/'),
  glossary: () => json('/api/glossary/'),
  addTerm: (term, translation, sourceLang, targetLang) =>
    json('/api/glossary/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        term,
        translation,
        ...(sourceLang ? { source_lang: sourceLang } : {}),
        ...(targetLang ? { target_lang: targetLang } : {}),
      }),
    }),
  deleteTerm: (term, sourceLang, targetLang) =>
    json('/api/glossary/', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        term,
        ...(sourceLang ? { source_lang: sourceLang } : {}),
        ...(targetLang ? { target_lang: targetLang } : {}),
      }),
    }),
  deleteJob: (id) => json(`/api/jobs/${id}/`, { method: 'DELETE' }),
  settings: () => json('/api/settings/'),
  saveSettings: (data) =>
    json('/api/settings/', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  download: (id, format) => download(`/api/jobs/${id}/download/?format=${format}`),
}

async function download(url) {
  const res = await fetch(BASE + url, { headers: authHeaders() })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.error || `HTTP ${res.status}`)
  }
  const disposition = res.headers.get('Content-Disposition') || ''
  const filename = /filename="?([^";]+)"?/i.exec(disposition)?.[1] || 'download'
  return { blob: await res.blob(), filename }
}
