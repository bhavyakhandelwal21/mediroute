import { useEffect, useState } from 'react'
import { api } from './api'

const empty = { facility: '', medicine: '', stock: '', expiry_days: '', lead_days: '', incoming_qty: '', locality: '', latitude: '', longitude: '', usage: '', cold_chain_required: false, cold_chain_storage: false, cold_chain_transport: false }
const num = (v) => (v === '' ? null : +v)

function RecordField({ field, label, type = 'number', placeholder, value, onChange }) {
  return <label>{label}<br /><input className="fld" type={type}
    step={['latitude', 'longitude'].includes(field) ? 'any' : undefined}
    min={type === 'number' && !['latitude', 'longitude'].includes(field) ? 0 : undefined}
    placeholder={placeholder} value={value} onChange={onChange} /></label>
}

export default function DataEntry({ onChange, onDataAdded, onOutbreak }) {
  const [f, setF] = useState(empty)
  const [recs, setRecs] = useState([])
  const [msg, setMsg] = useState('')
  const [pdf, setPdf] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [replace, setReplace] = useState(false)
  const [report, setReport] = useState(null)
  const [match, setMatch] = useState(null)
  const [outbreakText, setOutbreakText] = useState('')
  const [outbreak, setOutbreak] = useState(null)
  const load = () => api.records().then(setRecs)
  useEffect(() => { load() }, [])
  const done = (m) => { setMsg(m); load(); onChange() }
  const set = (k) => (e) => setF((previous) => ({ ...previous, [k]: e.target.value }))
  const setCheck = (k) => (e) => setF((previous) => ({ ...previous, [k]: e.target.checked }))
  const setFacility = (e) => {
    const facility = e.target.value
    const existing = recs.find((r) => r.facility.toLowerCase() === facility.trim().toLowerCase())
    setF((previous) => ({ ...previous, facility,
      cold_chain_storage: Boolean(existing?.cold_chain_storage),
      cold_chain_transport: Boolean(existing?.cold_chain_transport) }))
  }
  const setMedicine = (e) => {
    const medicine = e.target.value
    const existing = recs.find((r) => r.medicine.toLowerCase() === medicine.trim().toLowerCase())
    setF((previous) => ({ ...previous, medicine, cold_chain_required: Boolean(existing?.cold_chain_required) }))
  }

  const add = async () => {
    if (!f.facility.trim() || !f.medicine.trim()) return setMsg('Enter a facility and a medicine name.')
    const usage = f.usage.split(/[\s,;]+/).filter(Boolean).map((x) => (x === '-' || isNaN(+x) ? null : +x))
    try {
      await api.addRecord({ facility: f.facility.trim(), medicine: f.medicine.trim(), stock: num(f.stock), expiry_days: num(f.expiry_days),
        lead_days: num(f.lead_days) || 5, incoming_qty: num(f.incoming_qty) || 0, usage,
        locality: f.locality.trim() || null, latitude: num(f.latitude), longitude: num(f.longitude),
        cold_chain_required: f.cold_chain_required, cold_chain_storage: f.cold_chain_storage, cold_chain_transport: f.cold_chain_transport })
      setF(empty)
      setMsg(`Added ${f.medicine} at ${f.facility}. Analysing all saved data…`)
      load()
      await onDataAdded()
    } catch (e) { setMsg(e.message) }
  }

  const saveColdChain = async () => {
    if (!f.facility.trim() || !f.medicine.trim()) return setMsg('Enter an existing facility and medicine name first.')
    try {
      await api.updateColdChain({ facility: f.facility.trim(), medicine: f.medicine.trim(),
        cold_chain_required: f.cold_chain_required, cold_chain_storage: f.cold_chain_storage,
        cold_chain_transport: f.cold_chain_transport })
      setMsg('Cold-chain settings saved without changing stock quantities.')
      await load()
      onChange()
    } catch (e) { setMsg(e.message) }
  }

  const upload = async () => {
    if (!pdf) return setMsg('Choose a PDF first.')
    if (uploading) return
    setUploading(true)
    try {
      const r = await api.uploadPdf(pdf, replace)
      setReport(r)
      setMsg(`Read ${r.stock_rows} stock rows and ${r.usage_rows} usage rows from the PDF.`)
      load()
      await onDataAdded()
    }
    catch (e) { setReport(null); setMsg(e.message) }
    finally { setUploading(false) }
  }

  return <>
    <h3 style={{ margin: '0 0 4px' }}>Upload a PDF</h3>
    <p className="note">Use a text-based PDF (exported from Excel or Word, not a scan) with a stock table (Facility, Medicine, Stock; optional Expiry, Lead days, Incoming, Locality, Latitude, Longitude, Usage, Cold chain required, Cold-chain storage, Cold-chain transport)
      and/or a usage table (Facility, Medicine, Date, Used). <a href="/api/sample-pdf">Download a sample PDF</a>. Dates like 31/03/2026 are read day first.</p>
    <div className="row"><input type="file" accept="application/pdf,.pdf" onChange={(e) => setPdf(e.target.files[0] || null)} />
      <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />Replace existing data (otherwise append)</label>
      <button className="p" onClick={upload} disabled={uploading}>{uploading ? 'Analysing PDF…' : 'Read PDF and analyse'}</button></div>
    {report && <p className="why">Loaded {report.pairs} medicine-facility pairs.{report.warnings.map((w, i) => <span key={i}><br />Note: {w}</span>)}</p>}
    <h3 style={{ margin: '16px 0 4px' }}>Or type a record</h3>
    <p className="note">One medicine at one facility per record. Enter the same facility and medicine twice to add a second batch. Records are saved in SQLite.
      Leave stock or expiry blank if unknown: the record is flagged, not guessed. Add the same medicine at two or more facilities to see transfers.</p>
    <div className="params" style={{ margin: '10px 0' }}>
      <RecordField field="facility" label="Facility" type="text" placeholder="e.g. Town Hospital" value={f.facility} onChange={setFacility} />
      <RecordField field="medicine" label="Medicine" type="text" placeholder="e.g. Paracetamol" value={f.medicine} onChange={setMedicine} />
      <button type="button" onClick={async () => { try { setMatch(await api.normalizeDrug(f.medicine)) } catch (e) { setMsg(e.message) } }}>Suggest NLEM name</button>
      <RecordField field="stock" label="Current stock (units)" placeholder="e.g. 400" value={f.stock} onChange={set('stock')} />
      <RecordField field="expiry_days" label="Days until expiry" placeholder="e.g. 60" value={f.expiry_days} onChange={set('expiry_days')} />
      <RecordField field="lead_days" label="Delivery time (days)" placeholder="e.g. 5" value={f.lead_days} onChange={set('lead_days')} />
      <RecordField field="incoming_qty" label="Incoming units (optional)" placeholder="e.g. 100" value={f.incoming_qty} onChange={set('incoming_qty')} />
      <RecordField field="locality" label="Locality (for the map)" type="text" placeholder="e.g. Indirapuram" value={f.locality} onChange={set('locality')} />
      <RecordField field="latitude" label="Latitude (optional)" placeholder="e.g. 28.643" value={f.latitude} onChange={set('latitude')} />
      <RecordField field="longitude" label="Longitude (optional)" placeholder="e.g. 77.369" value={f.longitude} onChange={set('longitude')} /></div>
    <div className="cold-chain-fields">
      <b>Cold-chain checks (2–8°C)</b>
      <p className="note">Mark the medicine requirement and each facility capability accurately. These are manual records, not live temperature checks.</p>
      <label><input type="checkbox" checked={f.cold_chain_required} onChange={setCheck('cold_chain_required')} /> This medicine requires cold-chain handling</label>
      <label><input type="checkbox" checked={f.cold_chain_storage} onChange={setCheck('cold_chain_storage')} /> This facility has verified cold storage</label>
      <label><input type="checkbox" checked={f.cold_chain_transport} onChange={setCheck('cold_chain_transport')} /> This facility can provide cold-chain transport</label>
      <div><button type="button" onClick={saveColdChain}>Save settings for existing record</button><span className="note"> Saves these checks without adding a stock batch.</span></div>
    </div>
    {match && <p className="note" aria-live="polite">Catalog suggestion: <b>{match.matched_name}</b> ({Math.round(match.confidence * 100)}% match; {match.catalog_version}). {match.accepted ? <button type="button" onClick={() => { setF((old) => ({ ...old, medicine: match.matched_name })); setMatch(null) }}>Use suggestion</button> : 'Please verify manually; low confidence.'}</p>}
    <label>Daily usage, oldest to newest. Spaces or commas; a dash for a missing day.<br />
      <textarea className="fld" rows={3} value={f.usage} onChange={set('usage')} placeholder="e.g. 20 22 19 25 - 21 23 18 ... (at least 7 days; the last 60 are used)" /></label>
    <div className="row"><button className="p" onClick={add}>Add record</button>
      <button onClick={() => api.sample(Math.floor(Math.random() * 9999)).then(() => done('New synthetic sample data loaded.'))}>Load sample data</button>
      <button onClick={() => api.clear().then(() => done('All data cleared.'))}>Clear all data</button></div>
    <p className="note" aria-live="polite">{msg}</p>
    <hr /><h3>Outbreak bulletin</h3>
    <p className="note">Paste a local advisory to extract a demand multiplier. Review the parsed locality and category before applying it.</p>
    <textarea className="fld" rows={3} value={outbreakText} onChange={(e) => setOutbreakText(e.target.value)} placeholder="e.g. East Delhi PHCs reporting a 45% surge in dengue cases" />
    <div className="row"><button type="button" onClick={async () => { try { const r = await api.parseOutbreak(outbreakText); setOutbreak(r) } catch (e) { setMsg(e.message) } }}>Parse bulletin</button>
      {outbreak && <button className="p" type="button" onClick={() => { onOutbreak(outbreak); setMsg(`Applied ${outbreak.surge_multiplier}x for ${outbreak.drug_category} in ${outbreak.region}.`); setOutbreak(null) }}>Apply parsed demand factor</button>}</div>
    {outbreak && <p className="note">{outbreak.region} · {outbreak.drug_category} · {outbreak.surge_multiplier}x · {Math.round(outbreak.confidence * 100)}% parser confidence</p>}
    {recs.length > 0 && <table><thead><tr><th>Facility</th><th>Medicine</th><th>Cold-chain status</th><th>Stock</th><th>Expiry (days)</th><th>Delivery (days)</th><th>Usage days</th><th /></tr></thead>
      <tbody>{recs.map((r) => <tr key={r.id}><td>{r.facility}</td><td>{r.medicine}</td>
        <td>{r.cold_chain_required ? `Required; storage ${r.cold_chain_storage ? 'ready' : 'not marked'}, transport ${r.cold_chain_transport ? 'ready' : 'not marked'}` : 'Not required'}</td>
        <td>{r.stock ?? 'missing'}</td><td>{r.expiry_days ?? 'missing'}</td><td>{r.lead_days}</td><td>{r.usage_days}</td>
        <td><button onClick={() => api.deleteRecord(r.id).then(() => done('Record removed.'))}>Delete</button></td></tr>)}</tbody></table>}
  </>
}
