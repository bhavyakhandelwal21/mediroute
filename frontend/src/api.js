// Tiny helper for talking to the FastAPI backend.
const qs = (p) => new URLSearchParams(p).toString()
async function call(url, opts) {
  const r = await fetch(url, opts)
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail
    const error = new Error(Array.isArray(detail) ? detail.map((e) => `${e.loc?.slice(1).join('.') || 'Input'}: ${e.msg}`).join('; ') : detail || `Request failed (${r.status})`)
    error.status = r.status
    throw error
  }
  return r.json()
}
export const api = {
  checkArrivalTemperature: (body) => call('/api/temperature/check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  bloodCatalog: () => call('/api/blood/catalog'),
  bloodAnalysis: (hours) => call('/api/blood/analysis?' + qs({ transit_hours: hours })),
  uploadBloodPdf: (file, replace) => { const fd = new FormData(); fd.append('file', file); fd.append('replace', replace); return call('/api/blood/upload-pdf', { method: 'POST', body: fd }) },
  deleteBloodPosition: (id) => call('/api/blood/positions/' + id, { method: 'DELETE' }),
  saveBloodPosition: (body) => call('/api/blood/positions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  saveBloodBatch: (body) => call('/api/blood/batches', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  deleteBloodBatch: (id) => call('/api/blood/batches/' + id, { method: 'DELETE' }),
  analysis: (p) => call('/api/analysis?' + qs(p)),
  forecast: (facility, medicine, p) => call('/api/forecast?' + qs({ facility, medicine, ...p })),
  evaluation: (p) => call('/api/evaluation?' + qs(p)),
  records: () => call('/api/records'),
  addRecord: (body) => call('/api/records', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  updateColdChain: (body) => call('/api/cold-chain', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  deleteRecord: (id) => call('/api/records/' + id, { method: 'DELETE' }),
  sample: (seed) => call('/api/sample?seed_value=' + seed, { method: 'POST' }),
  facilities: () => call('/api/facilities'),
  uploadPdf: (file, replace) => { const fd = new FormData(); fd.append('file', file); fd.append('replace', replace); return call('/api/upload-pdf', { method: 'POST', body: fd }) },
  normalizeDrug: (raw_name) => call('/api/ai/normalize-drug', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raw_name }) }),
  parseOutbreak: (raw_text_report) => call('/api/ai/outbreak', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raw_text_report }) }),
  transferJustification: (facts) => call('/api/ai/transfer-justification', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(facts) }),
  clear: () => call('/api/clear', { method: 'POST' }),
}

