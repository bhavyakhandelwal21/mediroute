import { useEffect, useMemo, useState } from 'react'
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, Tooltip, useMap } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import { api } from './api'

const initialPosition = { facility: '', locality: '', latitude: '', longitude: '', blood_group: 'A+', component: 'Red cells', target_units: 10, storage_verified: false, transport_verified: false }
const emptyBatch = { batch_code: '', units: '', expires_at: '', released: false }
const SUBTABS = [['data', 'Add blood data'], ['alerts', 'Alerts'], ['map', 'Map'], ['transfers', 'Transfers'], ['checks', 'Data checks']]
const GROUPS = ['All blood groups', 'A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']
const COMPONENTS = ['All components', 'Red cells', 'Whole blood', 'Platelets', 'Frozen plasma']
const title = (r) => `${r.facility} / ${r.blood_group} / ${r.component}`
const dateLabel = (s) => new Date(s).toLocaleString()
const localDate = (s) => { const d = new Date(s); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16) }
const statusColor = (r) => r.status === 'Shortage' ? '#c93636' : r.status === 'Sufficient' ? '#38814a' : '#858d94'
const rad = Math.PI / 180
const distanceKm = (a, b) => {
  const h = Math.sin((b.latitude - a.latitude) * rad / 2) ** 2 + Math.cos(a.latitude * rad) * Math.cos(b.latitude * rad) * Math.sin((b.longitude - a.longitude) * rad / 2) ** 2
  return 12742 * Math.asin(Math.sqrt(h))
}
function FitBounds({ points }) {
  const map = useMap()
  useEffect(() => { if (points.length > 1) map.fitBounds(points, { padding: [36, 36], maxZoom: 13 }); else if (points.length === 1) map.setView(points[0], 12) }, [JSON.stringify(points)])
  return null
}

export default function BloodBank({ offline = true }) {
  const [catalog, setCatalog] = useState(null)
  const [data, setData] = useState(null)
  const [screen, setScreen] = useState('data')
  const [position, setPosition] = useState(initialPosition)
  const [batch, setBatch] = useState(emptyBatch)
  const [file, setFile] = useState(null)
  const [replace, setReplace] = useState(false)
  const [hours, setHours] = useState(2)
  const [groupFilter, setGroupFilter] = useState('All blood groups')
  const [componentFilter, setComponentFilter] = useState('All components')
  const [routeSelection, setRouteSelection] = useState('')
  const [route, setRoute] = useState(null)
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadReport, setUploadReport] = useState(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = async () => {
    const [c, d] = await Promise.all([api.bloodCatalog(), api.bloodAnalysis(hours)])
    setCatalog(c); setData(d)
    return d
  }
  useEffect(() => { load().catch((e) => setError(e.message)) }, [])
  const action = async (fn, note) => {
    setBusy(true); setError(''); setMessage('')
    try { await fn(); const d = await load(); setScreen('alerts'); setMessage(note); return d }
    catch (e) { setError(e.message || 'Could not save blood inventory.'); return null }
    finally { setBusy(false) }
  }
  const changePosition = (key) => (e) => setPosition((old) => ({ ...old, [key]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))
  const changeBatch = (key) => (e) => setBatch((old) => ({ ...old, [key]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))
  const saveRecord = async (e) => {
    e.preventDefault()
    const hasBatch = Boolean(batch.batch_code || batch.units || batch.expires_at)
    if (hasBatch && (!batch.batch_code || !batch.units || !batch.expires_at)) {
      setError('To add a blood batch, fill in its reference, units, and expiry. Leave all three blank to save the hospital target; stock will remain unknown until you enter a count or batches.')
      return
    }
    await action(async () => {
      const saved = await api.saveBloodPosition({ ...position, target_units: Number(position.target_units), latitude: position.latitude === '' ? null : Number(position.latitude), longitude: position.longitude === '' ? null : Number(position.longitude) })
      if (hasBatch) await api.saveBloodBatch({ position_id: saved.id, batch_code: batch.batch_code.trim(), units: Number(batch.units), expires_at: new Date(batch.expires_at).toISOString(), released: batch.released })
      setBatch(emptyBatch)
    }, hasBatch ? 'Blood record saved and alerts updated.' : 'Hospital target saved. No batch was added, so stock is recorded as zero.')
  }
  const upload = async () => {
    if (!file || uploading) return
    setUploading(true); setError(''); setMessage('')
    try {
      const report = await api.uploadBloodPdf(file, replace)
      const d = await load()
      setScreen('alerts')
      setUploadReport(report)
      setMessage(`Read ${report.rows} rows: ${report.positions} hospital products and ${report.batches} batches. Blood alerts are updated.`)
      setFile(null)
      if (d) setData(d)
    } catch (e) { setError(e.message || 'Could not read this PDF.') }
    finally { setUploading(false) }
  }
  const rows = useMemo(() => (data?.rows || []).filter((r) => (groupFilter === 'All blood groups' || r.blood_group === groupFilter) && (componentFilter === 'All components' || r.component === componentFilter)), [data, groupFilter, componentFilter])
  const transfers = useMemo(() => (data?.transfers || []).filter((t) => (groupFilter === 'All blood groups' || t.blood_group === groupFilter) && (componentFilter === 'All components' || t.component === componentFilter)), [data, groupFilter, componentFilter])
  const facilities = useMemo(() => [...new Map(rows.filter((r) => r.latitude != null && r.longitude != null).map((r) => [r.facility.toLowerCase(), r])).values()], [rows])
  const routeTransfers = transfers.filter((t) => facilities.some((f) => f.facility === t.from_) && facilities.some((f) => f.facility === t.to))
  const selectedRoute = routeTransfers[Number(routeSelection) || 0]
  const mapPoints = facilities.map((f) => [f.latitude, f.longitude])

  const showRoute = async (transfer) => {
    const from = facilities.find((f) => f.facility === transfer.from_)
    const to = facilities.find((f) => f.facility === transfer.to)
    if (!from || !to) { setRoute(null); setError('Add latitude and longitude for both hospitals to show this transfer on the map.'); return }
    const directKm = distanceKm(from, to)
    let line = [[from.latitude, from.longitude], [to.latitude, to.longitude]], road = false, km = directKm, minutes = null
    if (!offline && navigator.onLine) {
      try {
        const url = `https://router.project-osrm.org/route/v1/driving/${from.longitude},${from.latitude};${to.longitude},${to.latitude}?overview=full&geometries=geojson&alternatives=true`
        const response = await fetch(url, { signal: AbortSignal.timeout(5000) })
        const result = await response.json()
        if (result.routes?.length) {
          const best = [...result.routes].sort((a, b) => a.distance - b.distance)[0]
          line = best.geometry.coordinates.map(([x, y]) => [y, x]); km = best.distance / 1000; minutes = best.duration / 60; road = true
        }
      } catch { /* Offline fallback: keep the direct line and distance. */ }
    }
    setRoute({ from: from.facility, to: to.facility, line, km, minutes, road, transfer })
    setError('')
  }
  useEffect(() => { if (screen === 'map' && selectedRoute) showRoute(selectedRoute) }, [screen, routeSelection, data, groupFilter, componentFilter])

  return <section className="blood-bank">
    <h2>Blood bank</h2>
    <p>Upload or enter blood inventory, then review shortages, safe transfer options and hospital locations.</p>
    <div className="tabs blood-subtabs" role="tablist" aria-label="Blood inventory sections">
      {SUBTABS.map(([key, label]) => <button key={key} role="tab" aria-selected={screen === key} onClick={() => setScreen(key)}>{label}</button>)}
    </div>
    {error && <div className="panel err" role="alert">{error}</div>}
    {message && <p className="blood-message" role="status">{message}</p>}
    {!catalog && <button className="p" onClick={() => load().catch((e) => setError(e.message))} disabled={busy}>Load blood inventory</button>}
    {catalog && <>
      <div className="blood-summary">
        <div><strong>{data?.summary.reported_units ?? 0}</strong><span>Units reported in inventory</span></div>
        <div><strong>{data?.summary.usable_units ?? 0}</strong><span>Verified usable units</span></div>
        <div><strong>{data?.summary.unknown_stock_positions ?? 0}</strong><span>Products missing a stock count</span></div>
        <div><strong>{data?.summary.shortage_positions ?? 0}</strong><span>Hospital products below target</span></div>
        <div><strong>{data?.summary.surplus_units ?? 0}</strong><span>Eligible surplus units</span></div>
        <div><strong>{data?.summary.transfer_units ?? 0}</strong><span>Units proposed to transfer</span></div>
        <div><strong>{data?.summary.unmet_units ?? 0}</strong><span>Units still needed after proposals</span></div>
      </div>
      {screen === 'alerts' && uploadReport && <section className="blood-entry-card blood-upload-results" aria-live="polite">
        <div className="blood-card-heading"><div><h3>Blood PDF analysis complete</h3>
          <p className="note">Loaded {uploadReport.rows} rows covering {uploadReport.positions} hospital products and {uploadReport.batches} batches. The results below include all saved blood data.</p></div>
          <div className="row"><button onClick={() => setScreen('transfers')}>Review transfer plan</button><button className="p" onClick={() => { const i=routeTransfers.findIndex((t) => t); setRouteSelection(i < 0 ? '' : String(i)); setScreen('map') }}>Open hospital map</button></div></div>
        <p className="note">Exact group and component matches are used. Proposed quantities use released, in-date batches and respect saved handling checks. A stock count without batch details is shown for review but is not eligible to move.</p>
      </section>}
      {(screen === 'data' || screen === 'alerts' || screen === 'transfers' || screen === 'checks') && <div className="blood-filters">
        <label>Blood group<select className="fld" value={groupFilter} onChange={(e) => setGroupFilter(e.target.value)}>{GROUPS.map((x) => <option key={x}>{x}</option>)}</select></label>
        <label>Component<select className="fld" value={componentFilter} onChange={(e) => setComponentFilter(e.target.value)}>{COMPONENTS.map((x) => <option key={x}>{x}</option>)}</select></label>
      </div>}

      {screen === 'data' && <>
        <section className="blood-entry-card">
          <h3>Upload blood inventory</h3>
          <p className="note">Upload a text-based PDF table with one row per hospital and blood product. Include Facility, Blood group, Component, Available units and Target units. “Target units” is the hospital’s minimum to keep; it lets MediRoute calculate exact shortages and eligible surplus. For transfer recommendations, include Batch reference, Expiry, Released, Storage verified and Transport verified. Add locality and coordinates for both hospitals to draw the route on the map.</p>
          <div className="row blood-upload-row"><input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files[0] || null)} />
            <label className="blood-inline-check"><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /> Replace saved blood data</label>
            <button className="p" onClick={upload} disabled={!file || uploading}>{uploading ? 'Reading PDF…' : 'Read PDF and analyse'}</button></div>
          <p className="note">Use Replace only to clear existing blood-bank records first. Otherwise, matching hospital products are updated and matching batch references are refreshed. Stock counts without batch details are reported but held out of the transfer plan until verified.</p>
        </section>

        <section className="blood-entry-card">
          <h3>Or add one blood record</h3>
          <p className="note">Add a hospital's minimum target and, if available, one dated blood batch. Save another batch separately using its own reference. Without a count or batch, stock remains unknown.</p>
          <form onSubmit={saveRecord}>
            <div className="blood-form-heading">Hospital and product</div>
            <div className="params blood-entry-grid">
              <label>Hospital<input className="fld" list="blood-hospital-names" required maxLength={120} value={position.facility} onChange={changePosition('facility')} placeholder="e.g. City Hospital" />
                <datalist id="blood-hospital-names">{[...new Set((data?.rows || []).map((r) => r.facility))].map((name) => <option key={name} value={name} />)}</datalist></label>
              <label>Locality<input className="fld" value={position.locality} onChange={changePosition('locality')} placeholder="e.g. Dwarka" /></label>
              <label>Blood group<select className="fld" value={position.blood_group} onChange={changePosition('blood_group')}>{catalog.groups.map((x) => <option key={x}>{x}</option>)}</select></label>
              <label>Blood component<select className="fld" value={position.component} onChange={changePosition('component')}>{catalog.components.map((x) => <option key={x}>{x}</option>)}</select></label>
              <label>Minimum units to keep<input className="fld" type="number" required min="0" max="100000" step="1" value={position.target_units} onChange={changePosition('target_units')} /></label>
              <label>Latitude (optional)<input className="fld" type="number" step="any" min="-90" max="90" value={position.latitude} onChange={changePosition('latitude')} placeholder="e.g. 28.6139" /></label>
              <label>Longitude (optional)<input className="fld" type="number" step="any" min="-180" max="180" value={position.longitude} onChange={changePosition('longitude')} placeholder="e.g. 77.2090" /></label>
            </div>
            <div className="blood-form-heading">Blood batch (optional)</div>
            <div className="params blood-entry-grid">
              <label>Batch reference<input className="fld" maxLength={120} value={batch.batch_code} onChange={changeBatch('batch_code')} placeholder="e.g. RBC-001" /></label>
              <label>Unreserved units<input className="fld" type="number" min="0" max="100000" step="1" value={batch.units} onChange={changeBatch('units')} placeholder="e.g. 12" /></label>
              <label>Labelled expiry<input className="fld" type="datetime-local" value={batch.expires_at} onChange={changeBatch('expires_at')} /></label>
            </div>
            <div className="blood-readiness">
              <b>Handling readiness</b>
              <p className="note">Confirm these with blood-bank staff. They are manual checks, not live temperature readings.</p>
              <div className="blood-checks">
                <label><input type="checkbox" checked={position.storage_verified} onChange={changePosition('storage_verified')} /> Product-specific storage verified</label>
                <label><input type="checkbox" checked={position.transport_verified} onChange={changePosition('transport_verified')} /> Transport verified</label>
                <label><input type="checkbox" checked={batch.released} onChange={changeBatch('released')} /> Batch released for distribution</label>
              </div>
            </div>
            <div className="row blood-form-actions"><button className="p" disabled={busy}>{busy ? 'Saving…' : 'Save record and analyse'}</button>
              <button type="button" disabled={busy} onClick={() => { setPosition(initialPosition); setBatch(emptyBatch); setError(''); setMessage('') }}>Clear form</button></div>
          </form>
        </section>
        <section className="blood-entry-card">
          <div className="blood-card-heading"><h3>Saved hospital products</h3><button disabled={busy} onClick={() => action(async () => {}, 'Blood analysis refreshed.')}>Refresh</button></div>
          {!rows.length ? <p className="empty">No blood records yet. Upload a PDF or add one record above.</p> : <div className="blood-table"><table>
            <thead><tr><th>Hospital / product</th><th>Locality</th><th>Reported / target</th><th>Verified usable</th><th>Status</th><th>Readiness</th><th>Actions</th></tr></thead>
            <tbody>{rows.map((r) => <tr key={r.id}><td><b>{r.facility}</b><br />{r.blood_group} · {r.component}</td><td>{r.locality || 'Not added'}{r.latitude != null && r.longitude != null ? <><br />{r.latitude}, {r.longitude}</> : ''}</td><td>{r.stock_known ? r.stock_units : 'Unknown'} / {r.target_units}</td><td>{r.usable_units}</td><td>{r.status}</td><td>{r.warnings.join(' ') || 'Recorded checks passed.'}</td><td><button onClick={() => { setPosition({ facility:r.facility, locality:r.locality || '', latitude:r.latitude ?? '', longitude:r.longitude ?? '', blood_group:r.blood_group, component:r.component, target_units:r.target_units, storage_verified:Boolean(r.storage_verified), transport_verified:Boolean(r.transport_verified) }); setScreen('data'); setMessage('Target loaded in the form for editing.') }}>Edit target</button> <button disabled={busy} onClick={() => { if (window.confirm(`Delete ${title(r)} and its saved batches?`)) action(() => api.deleteBloodPosition(r.id), 'Hospital product and its batches deleted.') }}>Delete</button></td></tr>)}</tbody>
          </table></div>}
          <h3>Saved batches</h3>
          {!rows.some((r) => r.batches.length) ? <p className="empty">No batch details saved.</p> : <div className="blood-table"><table><thead><tr><th>Hospital / product</th><th>Batch</th><th>Units</th><th>Expiry</th><th>Eligibility</th><th>Actions</th></tr></thead>
            <tbody>{rows.flatMap((r) => r.batches.map((b) => <tr key={b.id}><td>{title(r)}</td><td>{b.batch_code}</td><td>{b.units}</td><td>{dateLabel(b.expires_at)}</td><td>{b.superseded ? 'Older snapshot — excluded from current count' : b.expired ? 'Expired — excluded' : !b.released ? 'Not released — excluded' : !b.eligible ? 'Storage unverified — excluded' : 'Available'}</td><td><button onClick={() => { setPosition({ facility:r.facility, locality:r.locality || '', latitude:r.latitude ?? '', longitude:r.longitude ?? '', blood_group:r.blood_group, component:r.component, target_units:r.target_units, storage_verified:Boolean(r.storage_verified), transport_verified:Boolean(r.transport_verified) }); setBatch({ batch_code:b.batch_code, units:b.units, expires_at:localDate(b.expires_at), released:Boolean(b.released) }); setScreen('data'); setMessage('Target and batch loaded. Saving updates this batch reference.') }}>Edit</button> <button disabled={busy} onClick={() => { if (window.confirm(`Delete batch ${b.batch_code}?`)) action(() => api.deleteBloodBatch(b.id), 'Batch deleted.') }}>Delete</button></td></tr>))}</tbody>
          </table></div>}
        </section>
      </>}

      {screen === 'alerts' && <>
        <section className="blood-entry-card">
          <h3>Hospital stock and exact gaps</h3><p className="note">“Short by” compares usable stock (or a reported count if batches are not yet entered) with the hospital's minimum target. “Eligible surplus” only counts released, unexpired batches that pass the saved handling checks.</p>
          {!rows.length ? <p className="empty">No blood records match these filters.</p> : <div className="blood-table"><table><thead><tr><th>Hospital / product</th><th>Stock reported</th><th>Verified usable</th><th>Target</th><th>Short by</th><th>Surplus before plan</th><th>Planned out / in</th><th>Still short</th><th>Status / reason</th></tr></thead>
            <tbody>{rows.map((r) => <tr key={r.id}><td><b>{r.facility}</b><br />{r.blood_group} · {r.component}</td><td>{r.stock_known ? r.stock_units : 'Unknown'}</td><td>{r.usable_units}{r.unverified_units > 0 ? ` (${r.unverified_units} not batch-verified)` : ''}</td><td>{r.target_units}</td><td>{r.shortage}</td><td>{r.surplus_before_transfers}</td><td>{r.proposed_out} out / {r.received} in</td><td>{r.unmet}</td><td><span className="chip" style={{ color: statusColor(r) }}>{r.status}</span><br />{r.warnings.join(' ') || (r.status === 'Shortage' ? 'Usable stock is below the hospital target.' : r.status === 'Sufficient' ? 'Verified usable stock meets the saved target.' : 'Check stock and batch details.')}</td></tr>)}</tbody>
          </table></div>}
        </section>
        <section className="blood-entry-card">
          <div className="blood-card-heading"><div><h3>Recommended transfers</h3><p className="note">Each line shows the exact quantity, matching blood product, donor and receiving hospital.</p></div><button onClick={() => setScreen('transfers')}>See full transfer plan</button></div>
          {!transfers.length ? <p className="empty">No safe transfer is available for these filters. Check exact matching products, donor surplus, batch expiry/release, storage and transport verification. Use Reorders/contact the blood bank for remaining gaps.</p> : <div className="blood-table"><table><thead><tr><th>Product</th><th>From → To</th><th>Exact units</th><th>Batch / expiry</th><th>Why it is suggested</th><th>Route</th></tr></thead>
            <tbody>{transfers.map((t,i) => { const ix=routeTransfers.findIndex((x) => x.batch_id===t.batch_id && x.to===t.to); return <tr key={`${t.batch_id}-${i}`}><td><b>{t.blood_group}</b><br />{t.component}</td><td>{t.from_} → {t.to}</td><td><b>{t.units}</b></td><td>{t.batch_code}<br />{dateLabel(t.expires_at)}</td><td>{t.why}</td><td><button disabled={ix < 0} title={ix < 0 ? 'Add coordinates for both hospitals to map this route.' : ''} onClick={() => { if (ix >= 0) { setRouteSelection(String(ix)); setScreen('map') } }}>{ix < 0 ? 'Add map locations' : 'View route'}</button></td></tr> })}</tbody>
          </table></div>}
        </section>
      </>}

      {screen === 'map' && <section className="blood-entry-card">
        <h3>Hospital map</h3><p className="note">Add a hospital locality and coordinates in Add blood data to place it here. Select a proposed transfer to see the two locations and route.</p>
        <div className="blood-map-controls">
          <label>Proposed transfer<select className="fld" value={routeSelection} onChange={(e) => setRouteSelection(e.target.value)}><option value="">Choose a transfer</option>{routeTransfers.map((t, i) => <option key={`${t.batch_id}-${i}`} value={i}>{t.from_} to {t.to} · {t.blood_group} {t.component} · {t.units} units</option>)}</select></label>
          {routeTransfers.length > 0 && <button className="p" disabled={!selectedRoute} onClick={() => selectedRoute && showRoute(selectedRoute)}>Show transfer route</button>}
        </div>
        {route && <p className="why">{route.from} to {route.to}: <b>{route.km.toFixed(1)} km</b>{route.minutes != null ? `, about ${Math.round(route.minutes)} minutes by road` : ' direct-line estimate'}.</p>}
        {!facilities.length ? <p className="empty">No hospitals have map coordinates yet. Add locality, latitude and longitude to their records.</p> : <div className="blood-map">
          <MapContainer center={[28.65,77.35]} zoom={11} style={{ height:'100%', width:'100%' }}>
            {!offline && navigator.onLine && <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />}
            <FitBounds points={mapPoints} />
            {route && <Polyline positions={route.line} pathOptions={{ color:'#2869bf', weight:5 }} />}
            {facilities.map((f) => { const items=rows.filter((r) => r.facility===f.facility); const hasShort=items.some((r) => r.shortage>0); return <CircleMarker key={f.facility} center={[f.latitude,f.longitude]} radius={12} pathOptions={{ color:'#fff', weight:2, fillColor:hasShort?'#c93636':'#38814a', fillOpacity:.95 }}><Tooltip>{f.facility}</Tooltip><Popup><b>{f.facility}</b><br />{f.locality || 'Locality not added'}{items.map((r) => <span key={`${r.id}-${r.blood_group}-${r.component}`}><br />{r.blood_group} {r.component}: {r.stock_known ? r.stock_units : 'unknown'} reported, {r.usable_units} verified / {r.target_units} target</span>)}</Popup></CircleMarker> })}
          </MapContainer>
        </div>}
        <p className="note">{offline || !navigator.onLine ? 'Offline view: hospital positions and straight-line distance only; street maps and road directions need internet.' : 'Map background and shortest-road estimates use online OpenStreetMap and OSRM services.'}</p>
      </section>}

      {screen === 'transfers' && <section className="blood-entry-card">
        <div className="blood-card-heading"><div><h3>Suggested blood transfers</h3><p className="note">Only exact blood-group and component matches are proposed. Proposals do not dispatch stock automatically.</p></div>
          <label>Transit estimate (hours)<input className="fld" type="number" min="0.1" max="72" step="0.1" value={hours} onChange={(e) => setHours(Number(e.target.value))} /></label>
          <button className="p" disabled={busy} onClick={() => action(async () => {}, 'Transfer proposals refreshed.')}>{busy ? 'Updating…' : 'Refresh analysis'}</button></div>
        {!transfers.length ? <p className="empty">No eligible transfer for these filters. Check for a matching surplus batch, verified storage/transport, and an arrival before expiry.</p> : <div className="blood-table"><table><thead><tr><th>Blood product</th><th>From → To</th><th>Units</th><th>Batch / expiry</th><th>Why suggested</th><th>Map</th></tr></thead>
          <tbody>{transfers.map((t,i) => { const ix=routeTransfers.findIndex((x) => x.batch_id===t.batch_id && x.to===t.to); return <tr key={`${t.batch_id}-${i}`}><td><b>{t.blood_group}</b><br />{t.component}</td><td>{t.from_} → {t.to}<br />{t.transit_hours} hour estimate</td><td><b>{t.units}</b></td><td>{t.batch_code}<br />{dateLabel(t.expires_at)}</td><td>{t.why}</td><td><button disabled={ix < 0} title={ix < 0 ? 'Add coordinates for both hospitals to map this route.' : ''} onClick={() => { if (ix >= 0) { setRouteSelection(String(ix)); setScreen('map') } }}>{ix < 0 ? 'Add map locations' : 'View map'}</button></td></tr> })}</tbody>
        </table></div>}
      </section>}

      {screen === 'checks' && <section className="blood-entry-card">
        <h3>Blood data checks</h3><p className="note">Review these before relying on the alerts or proposing any shipment.</p>
        {!rows.length ? <p className="empty">No blood records match these filters.</p> : <div className="blood-table"><table><thead><tr><th>Hospital / product</th><th>Checks to review</th><th>Map location</th></tr></thead>
          <tbody>{rows.map((r) => { const checks=[...r.warnings]; if (!r.batches.length && r.stock_units == null) checks.push('No stock count or batches entered.'); if (r.latitude==null || r.longitude==null) checks.push('Map coordinates are missing.'); return <tr key={r.id}><td><b>{r.facility}</b><br />{r.blood_group} · {r.component}</td><td>{checks.length ? checks.join(' ') : 'No data checks are flagged.'}</td><td>{r.locality || 'Locality missing'}{r.latitude!=null&&r.longitude!=null?` (${r.latitude}, ${r.longitude})`:''}</td></tr> })}</tbody>
        </table></div>}
      </section>}
    </>}
  </section>
}
