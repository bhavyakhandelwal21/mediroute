import { useEffect, useState, useCallback } from 'react'
import { api } from './api'
import { Alerts, Transfers, Reorders, Quality, About } from './pages'
import Forecast from './Forecast'
import Evaluation from './Evaluation'
import DataEntry from './DataEntry'
import MapPage from './MapPage'

const TABS = [['data', 'Add my data'], ['alerts', 'Alerts'], ['map', 'Map'], ['transfers', 'Transfers'], ['reorders', 'Reorders'],
  ['forecast', 'Forecast'], ['quality', 'Data checks'], ['about', 'How it works']]
export default function App() {
  const [params, setParams] = useState({ spike: 1.5, safety: 3, buffer: 2, season: 1, min_qty: 10, transit: 1, excess_x: 1.5, outbreak_multiplier: 1, outbreak_category: '', outbreak_region: '' })
  const [data, setData] = useState(null)
  const [tab, setTab] = useState('alerts')
  const [error, setError] = useState('')
  const [pdfAlertRows, setPdfAlertRows] = useState(null)

  const refresh = useCallback(() => {
    api.analysis(params).then((d) => { setData(d); setError('') })
      .catch((e) => setError('Cannot reach the backend. Is it running on port 8000? (' + e.message + ')'))
  }, [params])
  const analyzeNewData = async () => {
    const d = await api.analysis(params)
    setData(d)
    setError('')
    setTab('map')
    setPdfAlertRows(d.rows)
  }
  useEffect(refresh, [refresh])

  const pdfAlerts = pdfAlertRows?.filter((r) => r.status !== 'OK') ?? []
  return (
    <>
      <header><h1>Medi<span>Route</span></h1></header>
      <main>
        {error && <div className="panel err">{error}</div>}
        <div className="tabs" role="tablist">
          {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>)}</div>
        <div className="panel wrap">
          {tab === 'data' && <DataEntry onChange={refresh} onDataAdded={analyzeNewData} onOutbreak={(r) => setParams((old) => ({ ...old, outbreak_multiplier: r.surge_multiplier, outbreak_category: r.drug_category, outbreak_region: r.region }))} />}
          {tab === 'alerts' && data && <Alerts rows={data.rows} />}
          {tab === 'map' && data && <MapPage rows={data.rows} refreshKey={data} recommendedTransfer={data.transfers[0]} />}
          {tab === 'transfers' && data && <Transfers items={data.transfers} />}
          {tab === 'reorders' && data && <Reorders rows={data.reorders} />}
          {tab === 'forecast' && data && <Forecast rows={data.rows} params={params} />}
          {tab === 'eval' && data && <Evaluation params={params} refreshKey={data} />}
          {tab === 'quality' && data && <Quality rows={data.rows} />}
          {tab === 'about' && <About />}
        </div>
      </main>
      {pdfAlertRows !== null && <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setPdfAlertRows(null) }}>
        <section className="pdf-modal" role="dialog" aria-modal="true" aria-labelledby="pdf-alert-title">
          <div className="modal-head">
            <div><h2 id="pdf-alert-title">Inventory analysis complete</h2>
              <p className="note">These results include all saved data, including the records you just added.</p></div>
            <button aria-label="Close alerts" onClick={() => setPdfAlertRows(null)}>Close</button>
          </div>
          {!pdfAlertRows.length ? <p className="empty">No medicine records were found to analyze.</p> : pdfAlerts.length ? <>
            <p className="note">{pdfAlerts.length} medicine-facility record{pdfAlerts.length === 1 ? '' : 's'} need attention.</p>
            <Alerts rows={pdfAlerts} />
          </> : <p className="empty">No stockout, expiry, or data verification alerts were found in this upload.</p>}
          <div className="row modal-actions">
            <button onClick={() => { setPdfAlertRows(null); setTab('map') }}>Continue to map</button>
            <button className="p" onClick={() => { setPdfAlertRows(null); setTab('alerts') }}>View alerts</button>
          </div>
        </section>
      </div>}
    </>
  )
}
