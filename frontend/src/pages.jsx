import { useEffect, useState } from 'react'
import { api } from './api'

export const Chip = ({ s }) => <span className={`chip ${s}`}>{s}</span>
const Why = ({ list }) => <ul className="why">{list.map((w, i) => <li key={i}>{w}</li>)}</ul>
const ORDER = { Critical: 0, High: 1, Medium: 2, Uncertain: 3, OK: 4 }

export function Alerts({ rows }) {
  const [fac, setFac] = useState('all')
  const facs = [...new Set(rows.map((r) => r.facility))]
  const list = rows.filter((r) => fac === 'all' || r.facility === fac)
    .sort((a, b) => ORDER[a.status] - ORDER[b.status] || (a.cover ?? 999) - (b.cover ?? 999))
  return <>
    <label>Facility <select value={fac} onChange={(e) => setFac(e.target.value)}>
      <option value="all">All</option>{facs.map((f) => <option key={f}>{f}</option>)}</select></label>
    <table><thead><tr><th>Facility / medicine</th><th>Status</th><th>Cover</th><th>Forecast</th><th>Why</th></tr></thead>
      <tbody>{list.map((r, i) => <tr key={i}>
        <td><b>{r.medicine}</b><br />{r.facility}</td><td><Chip s={r.status} /></td>
        <td>{r.cover != null ? `${r.cover} d` : '-'}</td><td>{r.fc}/day</td><td><Why list={r.why} /></td></tr>)}</tbody></table></>
}

export function Transfers({ items }) {
  const [memos, setMemos] = useState({})
  const [coldChain, setColdChain] = useState({})
  const [errors, setErrors] = useState({})
  const [requested, setRequested] = useState({})
  const [requestErrors, setRequestErrors] = useState({})
  const [emailForms, setEmailForms] = useState({})
  const [emails, setEmails] = useState({})
  if (items.length === 0) return <p className="empty">No feasible transfers with the current settings.</p>
  const makeMemo = async (t, i) => {
    try {
      const result = await api.transferJustification({ source_name: t.from_, target_name: t.to, item_name: t.medicine,
        qty: t.qty, source_expiry_days: t.source_expiry_days, target_stockout_days: t.target_stockout_days,
        donor_reserve_days: t.donor_reserve_days, transit_distance_km: t.transit_distance_km,
        is_cold_chain: Boolean(coldChain[i]) })
      setMemos((old) => ({ ...old, [i]: result.justification })); setErrors((old) => ({ ...old, [i]: '' }))
    } catch (e) { setErrors((old) => ({ ...old, [i]: e.message })) }
  }
  const placeOrder = async (t, i) => {
    try {
      await api.createTransferOrder({ medicine: t.medicine, source_facility: t.from_, target_facility: t.to, quantity: t.qty,
        donor_email: emails[i]?.donor || '', requester_email: emails[i]?.requester || '',
        unit_price: Number(emails[i]?.unit_price ?? 20), transit_days: t.transit })
      setRequested((old) => ({ ...old, [i]: true })); setRequestErrors((old) => ({ ...old, [i]: '' }))
    } catch (e) { setRequestErrors((old) => ({ ...old, [i]: e.message })) }
  }
  const setEmail = (i, key, value) => setEmails((old) => ({ ...old, [i]: { ...old[i], [key]: value } }))
  return <section className="transfer-recommendations">
    <div className="transfer-page-heading">
      <div><h2>Recommended medicine transfers</h2><p className="note">Review the suggested donor, quantity, and delivery estimate. Place an order when you’re ready.</p></div>
      <span className="transfer-count">{items.length} {items.length === 1 ? 'recommendation' : 'recommendations'}</span>
    </div>
    <div className="transfer-list">{items.map((t, i) => {
      const unitPrice = Number(emails[i]?.unit_price ?? 20)
      const totalPrice = unitPrice * t.qty
      return <article className="transfer-card" key={`${t.medicine}-${t.from_}-${t.to}-${i}`}>
        <div className="transfer-card-heading">
          <div><span className="transfer-kicker">Medicine transfer</span><h3>{t.medicine}</h3></div>
          <div className="transfer-quantity"><span>Recommended quantity</span><b>{t.qty.toLocaleString()} units</b></div>
        </div>

        <div className="transfer-route">
          <div className="transfer-route-stop"><span>Donor hospital</span><b>{t.from_}</b></div>
          <span className="transfer-route-arrow" aria-hidden="true">→</span>
          <div className="transfer-route-stop"><span>Requesting hospital</span><b>{t.to}</b></div>
          <span className="transit-badge">{t.transit} day{t.transit === 1 ? '' : 's'} estimated</span>
        </div>

        <div className="transfer-explanation"><b>Why this is recommended</b><p>{t.why}</p></div>
        <div className="transfer-facts">
          <div><span>Donor stock expires in</span><b>{t.source_expiry_days} days</b></div>
          <div><span>Requesting hospital cover</span><b>{t.target_stockout_days.toFixed(1)} days</b></div>
          <div><span>Donor reserve after transfer</span><b>≥ {t.donor_reserve_days.toFixed(1)} days</b></div>
          <div><span>Estimated distance</span><b>{t.transit_distance_km == null ? 'Not available' : `${t.transit_distance_km} km, straight-line`}</b></div>
        </div>

        {t.is_cold_chain ? <div className="cold-chain-confirmation">
          <p>Cold-chain handling is required. Donor transport and recipient storage are marked ready for 2–8°C; this is a recorded capability check, not live temperature monitoring.</p>
          <label><input type="checkbox" checked={Boolean(coldChain[i])} onChange={(e) => setColdChain((old) => ({ ...old, [i]: e.target.checked }))} /> I confirm cold-chain handling will be arranged</label>
        </div> : <p className="transfer-cold-note">Cold-chain handling is not required for this medicine.</p>}

        <div className="transfer-actions">
          <button type="button" disabled={t.transit_distance_km == null} onClick={() => makeMemo(t, i)}>Generate audit memo</button>
          {!emailForms[i] && !requested[i] && <button type="button" className="p" onClick={() => setEmailForms((old) => ({ ...old, [i]: true }))}>Place order</button>}
          {requested[i] && <span className="order-success" role="status">Order request sent</span>}
        </div>
        {errors[i] && <p className="err" role="alert">{errors[i]}</p>}
        {memos[i] && <p className="audit-memo" aria-live="polite"><b>Audit memo</b>{memos[i]}</p>}

        {emailForms[i] && !requested[i] && <form className="order-form" onSubmit={(e) => { e.preventDefault(); placeOrder(t, i) }}>
          <div className="order-form-heading"><div><h4>Send an order request</h4><p>Both hospitals will receive the request details by email.</p></div>
            <button type="button" className="order-cancel" onClick={() => setEmailForms((old) => ({ ...old, [i]: false }))}>Cancel</button></div>
          <div className="order-form-fields">
            <label>Donor’s email<input type="email" autoComplete="email" required value={emails[i]?.donor || ''} onChange={(e) => setEmail(i, 'donor', e.target.value)} placeholder="supplier@hospital.org" /></label>
            <label>Requesting hospital’s email<input type="email" autoComplete="email" required value={emails[i]?.requester || ''} onChange={(e) => setEmail(i, 'requester', e.target.value)} placeholder="requester@hospital.org" /></label>
            <label>Estimated price per unit (₹)<input type="number" min="0" step="0.01" required value={emails[i]?.unit_price ?? '20'} onChange={(e) => setEmail(i, 'unit_price', e.target.value)} /></label>
          </div>
          <div className="order-price-summary"><div><span>Estimated total</span><b>₹{Number.isFinite(totalPrice) ? totalPrice.toFixed(2) : '0.00'}</b><small>₹{unitPrice.toFixed(2)} × {t.qty.toLocaleString()} units</small></div>
            <button type="submit" className="p">Send order request</button></div>
          <p className="order-price-note">Price is an estimate. Confirm the final price with the donor before payment.</p>
          {requestErrors[i] && <p className="err" role="alert">{requestErrors[i]}</p>}
        </form>}
      </article>
    })}</div>
  </section>
}

export function TransferOrders() {
  const [items, setItems] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const refresh = () => api.transferOrders().then((data) => { setItems(data); setError('') }).catch((e) => setError(e.message))
  useEffect(() => { refresh() }, [])
  const update = async (id, status) => {
    setBusy(true)
    try { await api.updateTransferOrder(id, status); await refresh() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  if (error && !items.length) return <p className="err">{error}</p>
  return <><p className="note">Requests are saved locally for the donor hospital to review. Accepting a request does not move stock; update inventory after the transfer is actually completed.</p>
    {error && <p className="err">{error}</p>}
    {!items.length ? <p className="empty">No transfer requests yet. Choose “Place order” on a recommended transfer.</p> : <table><thead><tr><th>Medicine</th><th>Donor → Requesting hospital</th><th>Quantity</th><th>Price / Total</th><th>ETA</th><th>Donor email</th><th>Requester email</th><th>Status</th><th>Requested</th><th>Action</th></tr></thead>
      <tbody>{items.map((o) => <tr key={o.id}><td><b>{o.medicine}</b></td><td>{o.source_facility} → {o.target_facility}</td><td>{o.quantity}</td><td>₹{Number(o.unit_price).toFixed(2)} / unit<br /><b>₹{(Number(o.unit_price) * o.quantity).toFixed(2)} total</b></td><td>{o.transit_days} day(s)</td><td>{o.donor_email}</td><td>{o.requester_email}</td><td>{o.status}</td><td>{new Date(o.created_at + 'Z').toLocaleString()}</td><td>{o.status === 'Requested' && <><button disabled={busy} onClick={() => update(o.id, 'Accepted')}>Accept</button> <button disabled={busy} onClick={() => update(o.id, 'Declined')}>Decline</button> <button disabled={busy} onClick={() => update(o.id, 'Cancelled')}>Cancel</button></>}</td></tr>)}</tbody></table>}</>
}

export const Reorders = ({ rows }) => rows.length === 0 ? <p className="empty">Nothing to reorder right now.</p> :
  <table><thead><tr><th>Facility / medicine</th><th>Order qty</th><th>Why</th></tr></thead>
    <tbody>{rows.map((r, i) => <tr key={i}><td><b>{r.medicine}</b><br />{r.facility}</td><td><b>{r.order}</b></td>
      <td><Why list={r.why.filter((w) => /^(Stockout|Reorder|Transfer: receive)/.test(w))} /></td></tr>)}</tbody></table>

export function Quality({ rows }) {
  const bad = rows.filter((r) => r.uncertain || r.why.some((w) => /missing/.test(w)))
  if (!bad.length) return <p className="empty">No data issues found.</p>
  return <><p className="note">Critical gaps (stock, expiry) are flagged, never guessed. Gaps in daily usage are filled with the average.</p>
    <table><thead><tr><th>Facility / medicine</th><th>Status</th><th>Issue</th></tr></thead>
      <tbody>{bad.map((r, i) => <tr key={i}><td><b>{r.medicine}</b><br />{r.facility}</td>
        <td><Chip s={r.uncertain ? 'Uncertain' : 'OK'} /></td><td><Why list={r.why.filter((w) => /missing|verify|Fewer/.test(w))} /></td></tr>)}</tbody></table></>
}

const T = ({ head, rows }) => <table><thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
  <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table>

export const About = () => <>
  <h3>Problem</h3>
  <p className="why">Essential medicines run out at some facilities while excess stock at others expires unused. MediRoute estimates demand, detects stockout and expiry risks, recommends transfers and reorder quantities, and explains each recommendation. Its AI features include fuzzy medicine-name matching, missing-log imputation, a rule-based outbreak bulletin parser, and optional AI-assisted audit memo wording; core stock decisions remain constrained by explicit inventory rules.</p>
  <p className="why"><b>Goals covered:</b> SDG 3, SDG 9, SDG 12.</p>
  <h3>System workflow and where it lives in this project</h3>
  <T head={['Step', 'What it does', 'File']} rows={[
    ['1. Data collection', 'Stock, usage, batch expiry, incoming orders in SQLite', 'backend/app/db.py, seed.py'],
    ['2. Validation', 'Flag missing stock or expiry; fill only missing usage days', 'backend/app/risk.py, forecast.py'],
    ['3. Demand estimation', 'Recent average x weekday pattern, spike check, 80% range', 'backend/app/forecast.py'],
    ['4. Risk detection', 'Days of cover vs delivery time; first-expiry-first-out batches', 'backend/app/risk.py'],
    ['5. Redistribution', 'Greedy matching with safety checks', 'backend/app/transfer.py'],
    ['6. Reorder quantity', 'Order what is still missing after transfers', 'backend/app/transfer.py'],
    ['7. Dashboard', 'React + Recharts, served data by FastAPI', 'frontend/src/*, backend/app/main.py'],
    ['8. Arrival temperature', 'Compare delivery readings with configured product ranges and flag review actions', 'frontend/src/ArrivalTemperature.jsx, backend/app/temperature.py']]} />
  <h3>System decision logic (checked in this order)</h3>
  <T head={['#', 'Scenario', 'Condition', 'Action']} rows={[
    ['1', 'Incomplete data', 'Stock or expiry missing', 'Flag for verification, mark uncertain'],
    ['2', 'Demand spike', '7-day average above 1.5x the 30-day average', 'Alert and raise the forecast'],
    ['3', 'Stockout risk', 'Days of cover below delivery time + buffer', 'Flag by severity, check transfers'],
    ['4', 'Expiry risk', 'Batch quantity above expected use before expiry', 'Mark excess, find receivers'],
    ['5', 'Transfer', 'Donor surplus, receiver shortage, safe transit time', 'Recommend transfer after safety checks'],
    ['6', 'Reorder', 'Shortfall remains after incoming stock and transfers', 'Calculate order quantity'],
    ['7', 'Arrival temperature', 'Temperature is in range, mildly warm (+2 to +4°C), or outside configured limits', 'Show assessment and escalation guidance; staff decide release or disposal under local procedures']]} />
  <p className="note">Days of cover = (stock + incoming) / daily forecast. Thresholds are the sliders above.</p>
  <h3>Expected impact</h3>
  <p className="why">Intended outcomes: fewer shortages, less expiry wastage, better availability, better use of resources and transparent decisions. These still need to be tested. The Before / after tab only uses sample or typed data, so it shows the method, not real-world proof.</p>
</>
