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
async function roadRoute(a, b) {
  try {
    const url = `https://router.project-osrm.org/route/v1/driving/${a.longitude},${a.latitude};${b.longitude},${b.latitude}?overview=full&geometries=geojson&alternatives=true`
    const j = await (await fetch(url)).json()
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

export default function MapPage({ rows, refreshKey }) {
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
    setBusy(true); setRoute({ ...(await roadRoute(a, b)), a: a.name, b: b.name }); setBusy(false)
  }
  const byName = (n) => located.find((f) => f.name === n)
  const nearestExcess = () => {
    const dest = byName(to); if (!dest) return
    const donors = located.filter((f) => f.name !== to && col(f) === 'yellow').sort((a, b) => dist(a, dest) - dist(b, dest))
    if (!donors.length) return alert('No hospital with excess of this medicine.')
    setFrom(donors[0].name); go(donors[0], dest)
  }

  if (!meds.length) return <p className="empty">Add data first, then the map shows each hospital.</p>
  return <>
    <div className="params">
      <label>Medicine<br /><select className="fld" value={m} onChange={(e) => setMed(e.target.value)}>{meds.map((x) => <option key={x}>{x}</option>)}</select></label>
      <label>Locality<br /><select className="fld" value={loc} onChange={(e) => setLoc(e.target.value)}>
        <option value="all">All localities</option>{localities.map((x) => <option key={x}>{x}</option>)}</select></label>
      <label>Route from<br /><select className="fld" value={from} onChange={(e) => setFrom(e.target.value)}>
        <option value="">Choose hospital</option>{located.map((f) => <option key={f.name}>{f.name}</option>)}</select></label>
      <label>Route to<br /><select className="fld" value={to} onChange={(e) => setTo(e.target.value)}>
        <option value="">Choose hospital</option>{located.map((f) => <option key={f.name}>{f.name}</option>)}</select></label>
    </div>
    <div className="row">
      <button className="p" disabled={busy || !from || !to || from === to} onClick={() => go(byName(from), byName(to))}>{busy ? 'Finding route...' : 'Show shortest route'}</button>
      <button disabled={busy || !to} onClick={nearestExcess}>Nearest hospital with excess, to "Route to"</button>
      {route && <button onClick={() => setRoute(null)}>Clear route</button>}
    </div>
    <div className="legend">
      {['red', 'green', 'yellow', 'gray'].map((c) => <span key={c}><i style={{ background: COLORS[c] }} />{LABEL[c]} ({count(c)})</span>)}
    </div>
    {route && <p className="why">{route.a} to {route.b}: <b>{route.km.toFixed(1)} km</b>{route.min != null && <>, about <b>{Math.round(route.min)} min</b> by road</>}.
      {!route.road && ' Road routing is unavailable (needs internet), so a straight line is shown.'}</p>}
    <div style={{ height: 460, minWidth: 320 }}>
      <MapContainer center={[28.65, 77.35]} zoom={11} style={{ height: '100%', borderRadius: 10 }}>
        <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <Fit pts={shown.map((f) => [f.latitude, f.longitude])} />
        {route && <Polyline positions={route.line} pathOptions={{ color: '#1565c0', weight: 5 }} />}
        {shown.map((f) => { const r = info(f), c = col(f); return (
          <CircleMarker key={f.name} center={[f.latitude, f.longitude]} radius={13}
            pathOptions={{ color: '#fff', weight: 2, fillColor: COLORS[c], fillOpacity: 0.95 }}>
            <Tooltip>{f.name}: {LABEL[c]}</Tooltip>
            <Popup><b>{f.name}</b><br />{f.locality || 'No locality'}<br /><b>{m}: {LABEL[c]}</b>
              {r && !r.uncertain && <><br />Stock {r.stock}{r.inc ? ` (+${r.inc} arriving)` : ''}; needed level {Math.round(r.target)}
                <br />{r.level === 'short' ? `Short by about ${Math.ceil(r.target - r.have)} units` : r.level === 'excess' ? `Surplus about ${Math.floor(r.have - r.target)} units${r.excess ? `, ${r.excess} may expire` : ''}` : 'Within the needed range'}
                <br />Use {r.fc}/day, {r.cover} days of cover</>}
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
