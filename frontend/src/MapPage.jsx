import { useEffect, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker, Popup, Tooltip, Polyline, useMap } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import { api } from './api'

const COLORS = { red: '#d32f2f', green: '#2e7d32', yellow: '#f9a825', gray: '#8a8f94' }
const LABEL = { red: 'Shortage', green: 'Right amount', yellow: 'Excess', gray: 'No reliable data' }
const colorOf = (r) => (!r || r.uncertain ? 'gray' : r.level === 'short' ? 'red' : r.level === 'excess' ? 'yellow' : 'green')

const rad = Math.PI / 180
const dist = (a, b) => {   // straight-line distance in km (haversine)
  const h = Math.sin(((b.latitude - a.latitude) * rad) / 2) ** 2 +
    Math.cos(a.latitude * rad) * Math.cos(b.latitude * rad) * Math.sin(((b.longitude - a.longitude) * rad) / 2) ** 2
  return 12742 * Math.asin(Math.sqrt(h))
}

// Road route from the free OSRM demo server. We ask for alternatives and keep the one with the shortest distance.
async function roadRoute(a, b, offline) {
  try {
    if (offline || !navigator.onLine) throw new Error('Offline')
    const url = `https://router.project-osrm.org/route/v1/driving/${a.longitude},${a.latitude};${b.longitude},${b.latitude}?overview=full&geometries=geojson&alternatives=true`
    const j = await (await fetch(url, { signal: AbortSignal.timeout(5000) })).json()
    const best = [...j.routes].sort((x, y) => x.distance - y.distance)[0]
    return { line: best.geometry.coordinates.map(([x, y]) => [y, x]), km: best.distance / 1000, min: best.duration / 60, road: true }
  } catch {
    return { line: [[a.latitude, a.longitude], [b.latitude, b.longitude]], km: dist(a, b), min: null, road: false }
  }
}

function Fit({ pts }) {
  const map = useMap()
  useEffect(() => { if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 14 }) }, [JSON.stringify(pts)])
  return null
}

export default function MapPage({ rows, refreshKey, recommendedTransfer, offline = true }) {
  const [facs, setFacs] = useState([])
  const [med, setMed] = useState('')
  const [loc, setLoc] = useState('all')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [route, setRoute] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.facilities().then(setFacs) }, [refreshKey])

  const meds = [...new Set(rows.map((r) => r.medicine))]
  const m = meds.includes(med) ? med : meds[0]
  const localities = [...new Set(facs.map((f) => f.locality).filter(Boolean))]
  const located = facs.filter((f) => f.latitude != null && f.longitude != null)
  const shown = located.filter((f) => loc === 'all' || f.locality === loc)
  const noLoc = facs.filter((f) => f.latitude == null || f.longitude == null)
  const info = (f) => rows.find((r) => r.facility === f.name && r.medicine === m)
  const col = (f) => colorOf(info(f))
  const count = (c) => shown.filter((f) => col(f) === c).length

  const go = async (a, b) => {
    if (!a || !b || a === b) return
    if ([a, b].some((f) => f.latitude == null || f.longitude == null)) {
      const missing = [a, b].filter((f) => f.latitude == null || f.longitude == null).map((f) => f.name)
      setRoute(null)
      return alert(`Add latitude and longitude for ${missing.join(' and ')} in Add my data (or include them in the PDF) before routing.`)
    }
    setBusy(true); setRoute({ ...(await roadRoute(a, b, offline)), a: a.name, b: b.name }); setBusy(false)
  }
  const byName = (n) => facs.find((f) => f.name === n)
  const nearestExcess = () => {
    const dest = located.find((f) => f.name === to); if (!dest) return
    const donors = located.filter((f) => f.name !== to && col(f) === 'yellow').sort((a, b) => dist(a, dest) - dist(b, dest))
    if (!donors.length) return alert('No hospital with excess of this medicine.')
    setFrom(donors[0].name); go(donors[0], dest)
  }

  // Build an honest supplier-facing case from the selected facilities' inventory.
  const routeRationale = (() => {
    if (!route) return null
    const source = rows.find((r) => r.facility === route.a && r.medicine === m)
    const target = rows.find((r) => r.facility === route.b && r.medicine === m)
    if (!source || !target || source.uncertain || target.uncertain) {
      return { type: 'neutral', title: 'Supplier decision', text: `A transfer case for ${m} cannot be confirmed because one facility has missing or unreliable inventory data. Verify the records before proposing a shipment.` }
    }
    if (source.cold_chain_required && (!source.cold_chain_transport || !target.cold_chain_storage)) {
      const missing = [!source.cold_chain_transport && `${route.a} has no verified cold-chain transport`, !target.cold_chain_storage && `${route.b} has no verified cold storage`].filter(Boolean).join(' and ')
      return { type: 'neutral', title: 'Cold-chain transfer blocked', text: `${m} is marked as requiring 2–8°C handling, but ${missing}. MediRoute will not recommend this transfer until both capabilities are marked ready.` }
    }
    const planned = recommendedTransfer?.medicine === m && recommendedTransfer.from_ === route.a && recommendedTransfer.to === route.b
      ? recommendedTransfer : null
    const possibleQty = Math.min(source.donor0 || 0, target.short0 || 0)
    if (!planned && possibleQty <= 0) {
      const reason = (source.donor0 || 0) <= 0
        ? `${route.a} has no verified shareable surplus after protecting its 21-day emergency reserve`
        : `${route.b} has no measured shortfall for this medicine`
      return { type: 'neutral', title: 'No transfer benefit shown', text: `This route is not currently a supported transfer recommendation: ${reason}. The map route may help with planning, but the inventory data does not justify moving stock.` }
    }
    const qty = planned?.qty ?? possibleQty
    const donorReserve = planned?.donor_reserve_days ?? ((source.stock + source.inc - qty) / (source.fc || 1))
    const urgency = target.cover != null && target.cover < target.lead
      ? `${route.b} has about ${target.cover} days of cover, shorter than its ${target.lead}-day delivery lead time, so this shipment addresses an impending shortage.`
      : `${route.b} needs up to ${qty} units to reach its planned stock level.`
    const savedExpiry = planned?.expiry_saved_units || 0
    const expiry = savedExpiry > 0
      ? ` The recommendation estimates that ${savedExpiry} units may otherwise be at expiry risk at the donor, helping prevent avoidable wastage.` : ''
    const routeBenefit = route.road
      ? ` The ${route.km.toFixed(1)} km, approximately ${Math.round(route.min)} minute road option is the shortest-distance route found, helping limit transport time and handling burden.`
      : ' Road-routing data is unavailable, so this display cannot confirm the shortest route or estimate transport time.'
    const validation = planned
      ? ` The donor retains about ${donorReserve.toFixed(1)} days of stock after sharing, above the 21-day reserve; the transfer planner also checked delivery time and shelf life.${planned.is_cold_chain ? ' Donor transport and receiver cold storage are marked ready for 2–8°C handling.' : ''}`
      : ` The donor would retain about ${donorReserve.toFixed(1)} days of stock after sharing; verify the delivery timing and batch expiry before dispatch.`
    return { type: planned ? 'positive' : 'neutral', title: planned ? 'Recommended supplier transfer' : 'Potential supplier case', text: `${route.a} can provide ${qty} units of ${m}. ${urgency}${expiry}${validation}${routeBenefit}` }
  })()

  useEffect(() => {
    if (!recommendedTransfer || !located.length) return
    const donor = located.find((f) => f.name === recommendedTransfer.from_)
    const receiver = located.find((f) => f.name === recommendedTransfer.to)
    if (!donor || !receiver) return
    setMed(recommendedTransfer.medicine)
    setLoc('all')
    setFrom(donor.name)
    setTo(receiver.name)
    setBusy(true)
    roadRoute(donor, receiver, offline)
      .then((result) => setRoute({ ...result, a: donor.name, b: receiver.name }))
      .finally(() => setBusy(false))
  }, [recommendedTransfer, facs, offline])

  if (!meds.length) return <p className="empty">Add data first, then the map shows each hospital.</p>
  return <>
    <div className="params">
      <label>Medicine<br /><select className="fld" value={m} onChange={(e) => setMed(e.target.value)}>{meds.map((x) => <option key={x}>{x}</option>)}</select></label>
      <label>Locality<br /><select className="fld" value={loc} onChange={(e) => setLoc(e.target.value)}>
        <option value="all">All localities</option>{localities.map((x) => <option key={x}>{x}</option>)}</select></label>
      <label>Route from<br /><select className="fld" value={from} onChange={(e) => setFrom(e.target.value)}>
        <option value="">Choose hospital</option>{facs.map((f) => <option key={f.name} value={f.name}>
          {f.name}{f.locality ? ` — ${f.locality}` : ''}{f.latitude == null || f.longitude == null ? ' (coordinates needed)' : ''}
        </option>)}</select></label>
      <label>Route to<br /><select className="fld" value={to} onChange={(e) => setTo(e.target.value)}>
        <option value="">Choose hospital</option>{facs.map((f) => <option key={f.name} value={f.name}>
          {f.name}{f.locality ? ` — ${f.locality}` : ''}{f.latitude == null || f.longitude == null ? ' (coordinates needed)' : ''}
        </option>)}</select></label>
    </div>
    <div className="row">
      <button className="p" disabled={busy || !from || !to || from === to} onClick={() => go(byName(from), byName(to))}>{busy ? 'Finding route...' : offline ? 'Show straight-line distance' : 'Show shortest route'}</button>
      <button disabled={busy || !to} onClick={nearestExcess}>Nearest hospital with excess, to "Route to"</button>
      {route && <button onClick={() => setRoute(null)}>Clear route</button>}
    </div>
    <div className="legend">
      {['red', 'green', 'yellow', 'gray'].map((c) => <span key={c}><i style={{ background: COLORS[c] }} />{LABEL[c]} ({count(c)})</span>)}
    </div>
    {route && <p className="why">{route.a} to {route.b}: <b>{route.km.toFixed(1)} km</b>{route.min != null && <>, about <b>{Math.round(route.min)} min</b> by road</>}.
      {!route.road && ' Road routing is unavailable (needs internet), so a straight line is shown.'}</p>}
    {routeRationale && <section className={`route-rationale ${routeRationale.type}`} aria-live="polite">
      <b>{routeRationale.title}</b><p>{routeRationale.text}</p>
      <small>Supplier value: place eligible stock where it is needed, protect the donor’s reserve, and reduce expiry or urgent replacement costs when the inventory data supports it. Confirm batch and handling conditions before dispatch.</small>
    </section>}
    <div style={{ height: 460, minWidth: 320 }}>
      <MapContainer center={[28.65, 77.35]} zoom={11} style={{ height: '100%', borderRadius: 10 }}>
        {!offline && <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />}
        <Fit pts={shown.map((f) => [f.latitude, f.longitude])} />
        {route && <Polyline positions={route.line} pathOptions={{ color: '#1565c0', weight: 5 }} />}
        {shown.map((f) => { const r = info(f), c = col(f); return (
          <CircleMarker key={f.name} center={[f.latitude, f.longitude]} radius={13}
            pathOptions={{ color: '#fff', weight: 2, fillColor: COLORS[c], fillOpacity: 0.95 }}>
            <Tooltip>{f.name}: {LABEL[c]}</Tooltip>
            <Popup><b>{f.name}</b><br />{f.locality || 'No locality'}<br /><b>{m}: {LABEL[c]}</b>
              {r && !r.uncertain && <><br />Stock {r.stock}{r.inc ? ` (+${r.inc} arriving)` : ''}; needed level {Math.round(r.target)}
                <br />{r.level === 'short' ? `Short by about ${Math.ceil(r.target - r.have)} units` : r.level === 'excess' ? `Surplus about ${Math.floor(r.have - r.target)} units${r.excess ? `, ${r.excess} may expire` : ''}` : 'Within the needed range'}
                <br />Use {r.fc}/day, {r.cover} days of cover
                {r.cold_chain_required && <><br />Cold chain required: storage {r.cold_chain_storage ? 'ready' : 'not marked'}; transport {r.cold_chain_transport ? 'ready' : 'not marked'}</>}</>}
              {r?.uncertain && <><br />{r.why[0]}</>}
              {!r && <><br />No record of this medicine here.</>}</Popup>
          </CircleMarker>) })}
      </MapContainer>
    </div>
    <p className="note">Red = stock below the level needed (delivery time + buffer + safety days of use). Yellow = stock above {'"Excess above"'} times that level, or units that may expire.
      Green = right amount. Grey = missing or too little data. Road routes come from the free OSRM demo server (internet needed) and are the shortest of the alternatives it offers.
      {noLoc.length > 0 && ` No location set for: ${noLoc.map((f) => f.name).join(', ')}. Add latitude and longitude in Add my data or in your PDF.`}</p>
  </>
}
