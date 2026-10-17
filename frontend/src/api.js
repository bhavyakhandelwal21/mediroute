// Tiny helper for talking to the FastAPI backend.
const qs = (p) => new URLSearchParams(p).toString()
async function call(url, opts) {
  const r = await fetch(url, opts)
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Request failed (${r.status})`)
  return r.json()
}
export const api = {
  analysis: (p) => call('/api/analysis?' + qs(p)),
  forecast: (facility, medicine, p) => call('/api/forecast?' + qs({ facility, medicine, ...p })),
  evaluation: (p) => call('/api/evaluation?' + qs(p)),
  records: () => call('/api/records'),
  addRecord: (body) => call('/api/records', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  deleteRecord: (id) => call('/api/records/' + id, { method: 'DELETE' }),
  sample: (seed) => call('/api/sample?seed_value=' + seed, { method: 'POST' }),
  facilities: () => call('/api/facilities'),
  uploadPdf: (file, replace) => { const fd = new FormData(); fd.append('file', file); fd.append('replace', replace); return call('/api/upload-pdf', { method: 'POST', body: fd }) },
  clear: () => call('/api/clear', { method: 'POST' }),
}
