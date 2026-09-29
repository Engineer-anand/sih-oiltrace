const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '') || (window.location.origin.includes(':5173') ? 'http://localhost:8000' : window.location.origin)

export async function apiFetch(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options)
  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try {
      const data = await response.json()
      const detail = data.detail
      if (typeof detail === 'string') message = detail
      else if (detail && typeof detail === 'object') {
        // RFC-7807 problem details are objects; surface their human text,
        // never "[object Object]".
        message = detail.detail || detail.title || JSON.stringify(detail)
      } else if (typeof data.message === 'string') message = data.message
      else if (detail != null) message = String(detail)
    } catch {}
    throw new Error(message)
  }
  return response
}

export async function apiJson(path, options) {
  const r = await apiFetch(path, options)
  return r.json()
}

export async function downloadFile(path, filename) {
  const r = await apiFetch(path)
  const blob = await r.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export { API_BASE }
