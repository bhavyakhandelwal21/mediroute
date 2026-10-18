import { useEffect, useState, useCallback } from 'react'
import { api } from './api'
import { Alerts, Transfers, Reorders, Quality, About } from './pages'
import Forecast from './Forecast'
import Evaluation from './Evaluation'
import DataEntry from './DataEntry'
import MapPage from './MapPage'

const TABS = [['data', 'Add my data'], ['alerts', 'Alerts'], ['map', 'Map'], ['transfers', 'Transfers'], ['reorders', 'Reorders'],
  ['forecast', 'Forecast'], ['quality', 'Data checks'], ['about', 'How it works']]
const SLIDERS = [['spike', 'Spike threshold (7-day vs 30-day)', 1.2, 2.5, 0.1, 'x'], ['safety', 'Safety stock (days)', 1, 7, 1, ' d'],
  ['excess_x', 'Excess above (x of needed)', 1.2, 3, 0.1, 'x']]

export default function App() {
  const [params, setParams] = useState({ spike: 1.5, safety: 3, buffer: 2, season: 1, min_qty: 10, transit: 1, excess_x: 1.5 })
  const [data, setData] = useState(null)
  const [tab, setTab] = useState('alerts')
  const [error, setError] = useState('')

  const refresh = useCallback(() => {
    api.analysis(params).then((d) => { setData(d); setError('') })
      .catch((e) => setError('Cannot reach the backend. Is it running on port 8000? (' + e.message + ')'))
  }, [params])
  useEffect(refresh, [refresh])

  const s = data?.summary
  return (
    <>
      <header><h1>Medi<span>Route</span></h1>
        <p className="sub">Medicine inventory optimization: forecasts, stockout and expiry alerts, transfers and reorders. Every recommendation shows its reason.</p></header>
      <main>
        <div className="panel">
          <div className="params">
            {SLIDERS.map(([k, label, min, max, step, unit]) => (
              <label key={k}>{label}: <b>{params[k]}{unit}</b>
                <input type="range" min={min} max={max} step={step} value={params[k]}
                  onChange={(e) => setParams({ ...params, [k]: +e.target.value })} /></label>))}
          </div>
          <p className="note">Sample data is synthetic. Records you add in the first tab are stored in the SQLite database (backend/mediflow.db).</p>
        </div>
        {error && <div className="panel err">{error}</div>}
        {s && <div className="stats">
          {[[s.high_risk, 'High-risk stockouts'], [s.expiry_units, 'Units at expiry risk'], [s.transfers, `Transfers (${s.transfer_units} units)`],
            [s.reorder_units, 'Units to reorder'], [s.to_verify, 'Records to verify']].map(([n, l]) =>
            <div className="stat" key={l}><b>{n}</b><small>{l}</small></div>)}</div>}
        <div className="tabs" role="tablist">
          {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>)}</div>
        <div className="panel wrap">
          {tab === 'data' && <DataEntry onChange={refresh} />}
          {tab === 'alerts' && data && <Alerts rows={data.rows} />}
          {tab === 'map' && data && <MapPage rows={data.rows} refreshKey={data} />}
          {tab === 'transfers' && data && <Transfers items={data.transfers} />}
          {tab === 'reorders' && data && <Reorders rows={data.reorders} />}
          {tab === 'forecast' && data && <Forecast rows={data.rows} params={params} />}
          {tab === 'eval' && data && <Evaluation params={params} refreshKey={data} />}
          {tab === 'quality' && data && <Quality rows={data.rows} />}
          {tab === 'about' && <About />}
        </div>
      </main>
    </>
  )
}
