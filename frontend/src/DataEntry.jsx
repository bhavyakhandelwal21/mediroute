import { useEffect, useState } from 'react'
import { api } from './api'

const empty = { facility: '', medicine: '', stock: '', expiry_days: '', lead_days: '', incoming_qty: '', locality: '', latitude: '', longitude: '', usage: '' }
const num = (v) => (v === '' ? null : +v)

export default function DataEntry({ onChange }) {
  const [f, setF] = useState(empty)
  const [recs, setRecs] = useState([])
  const [msg, setMsg] = useState('')
  const [pdf, setPdf] = useState(null)
  const [replace, setReplace] = useState(true)
  const [report, setReport] = useState(null)
  const load = () => api.records().then(setRecs)
  useEffect(() => { load() }, [])
  const done = (m) => { setMsg(m); load(); onChange() }
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })

  const add = async () => {
    if (!f.facility.trim() || !f.medicine.trim()) return setMsg('Enter a facility and a medicine name.')
    const usage = f.usage.split(/[\s,;]+/).filter(Boolean).map((x) => (x === '-' || isNaN(+x) ? null : +x))
    await api.addRecord({ facility: f.facility.trim(), medicine: f.medicine.trim(), stock: num(f.stock), expiry_days: num(f.expiry_days),
      lead_days: num(f.lead_days) || 5, incoming_qty: num(f.incoming_qty) || 0, usage,
      locality: f.locality.trim() || null, latitude: num(f.latitude), longitude: num(f.longitude) })
    setF(empty)
    done(`Added ${f.medicine} at ${f.facility}.` + (usage.filter((x) => x != null).length < 7 && usage.length ? ' Fewer than 7 usage days: marked Uncertain.' : ''))
  }
  const Field = ({ k, label, type = 'number', ph }) => (
    <label>{label}<br /><input className="fld" type={type} step={['latitude','longitude'].includes(k) ? 'any' : undefined} min={type === 'number' && !['latitude','longitude'].includes(k) ? 0 : undefined} placeholder={ph} value={f[k]} onChange={set(k)} /></label>)

  const upload = async () => {
    if (!pdf) return setMsg('Choose a PDF first.')
    try { const r = await api.uploadPdf(pdf, replace); setReport(r); done(`Read ${r.stock_rows} stock rows and ${r.usage_rows} usage rows from the PDF.`) }
    catch (e) { setReport(null); setMsg(e.message) }
  }

  return <>
    <h3 style={{ margin: '0 0 4px' }}>Upload a PDF</h3>
    <p className="note">Use a text-based PDF (exported from Excel or Word, not a scan) with a stock table (Facility, Medicine, Stock; optional Expiry, Lead days, Incoming, Locality, Latitude, Longitude, Usage)
      and/or a usage table (Facility, Medicine, Date, Used). <a href="/api/sample-pdf">Download a sample PDF</a>. Dates like 31/03/2026 are read day first.</p>
    <div className="row"><input type="file" accept="application/pdf,.pdf" onChange={(e) => setPdf(e.target.files[0] || null)} />
      <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />Replace existing data</label>
      <button className="p" onClick={upload}>Read PDF and analyse</button></div>
    {report && <p className="why">Loaded {report.pairs} medicine-facility pairs.{report.warnings.map((w, i) => <span key={i}><br />Note: {w}</span>)}</p>}
    <h3 style={{ margin: '16px 0 4px' }}>Or type a record</h3>
    <p className="note">One medicine at one facility per record. Enter the same facility and medicine twice to add a second batch. Records are saved in SQLite.
      Leave stock or expiry blank if unknown: the record is flagged, not guessed. Add the same medicine at two or more facilities to see transfers.</p>
    <div className="params" style={{ margin: '10px 0' }}>
      <Field k="facility" label="Facility" type="text" ph="e.g. Town Hospital" /><Field k="medicine" label="Medicine" type="text" ph="e.g. Paracetamol" />
      <Field k="stock" label="Current stock (units)" ph="e.g. 400" /><Field k="expiry_days" label="Days until expiry" ph="e.g. 60" />
      <Field k="lead_days" label="Delivery time (days)" ph="e.g. 5" /><Field k="incoming_qty" label="Incoming units (optional)" ph="e.g. 100" />
      <Field k="locality" label="Locality (for the map)" type="text" ph="e.g. Indirapuram" /><Field k="latitude" label="Latitude (optional)" ph="e.g. 28.643" /><Field k="longitude" label="Longitude (optional)" ph="e.g. 77.369" /></div>
    <label>Daily usage, oldest to newest. Spaces or commas; a dash for a missing day.<br />
      <textarea className="fld" rows={3} value={f.usage} onChange={set('usage')} placeholder="e.g. 20 22 19 25 - 21 23 18 ... (at least 7 days; the last 60 are used)" /></label>
    <div className="row"><button className="p" onClick={add}>Add record</button>
      <button onClick={() => api.sample(Math.floor(Math.random() * 9999)).then(() => done('New synthetic sample data loaded.'))}>Load sample data</button>
      <button onClick={() => api.clear().then(() => done('All data cleared.'))}>Clear all data</button></div>
    <p className="note" aria-live="polite">{msg}</p>
    {recs.length > 0 && <table><thead><tr><th>Facility</th><th>Medicine</th><th>Stock</th><th>Expiry (days)</th><th>Delivery (days)</th><th>Usage days</th><th /></tr></thead>
      <tbody>{recs.map((r) => <tr key={r.id}><td>{r.facility}</td><td>{r.medicine}</td><td>{r.stock ?? 'missing'}</td><td>{r.expiry_days ?? 'missing'}</td>
        <td>{r.lead_days}</td><td>{r.usage_days}</td><td><button onClick={() => api.deleteRecord(r.id).then(() => done('Record removed.'))}>Delete</button></td></tr>)}</tbody></table>}
  </>
}
