import { useEffect, useState } from 'react'
import { api } from './api'

const pct = (a, b) => (a ? Math.round(((a - b) / a) * 100) : 0)
export default function Evaluation({ params, refreshKey }) {
  const [e, setE] = useState(null)
  useEffect(() => { api.evaluation(params).then(setE) }, [params, refreshKey])
  if (!e) return <p className="empty">Loading...</p>
  if (!e.pairs) return <p className="empty">Not enough reliable data to run the replay.</p>
  return <>
    <p className="note">Replay of the last 30 days for {e.pairs} medicine-facility pairs. Same usage and delivery times; only the ordering policy differs. Synthetic or typed data, so this shows the method, not real-world proof.</p>
    <table><thead><tr><th>Measure</th><th>Fixed reorder point</th><th>MediFlow rules</th><th>Change</th></tr></thead><tbody>
      <tr><td>Stockout days</td><td>{e.stockout_days[0]}</td><td>{e.stockout_days[1]}</td><td>{pct(...e.stockout_days)}% fewer</td></tr>
      <tr><td>Average units on shelf per pair</td><td>{e.avg_shelf_units[0]}</td><td>{e.avg_shelf_units[1]}</td><td>{-pct(...e.avg_shelf_units)}%</td></tr>
      <tr><td>Forecast error, 14 held-out days (MAE)</td><td>{e.mae[0]} (flat average)</td><td>{e.mae[1]} (weekday pattern)</td><td>{pct(...e.mae)}% lower</td></tr></tbody></table>
    <p className="note" style={{ marginTop: 14 }}>Today's snapshot, using the current stock and the recommended transfers:</p>
    <table><thead><tr><th>Measure</th><th>Before transfers</th><th>After transfers</th></tr></thead><tbody>
      <tr><td>Units at expiry risk</td><td>{e.expiry_risk_units[0]}</td><td>{e.expiry_risk_units[1]}</td></tr>
      <tr><td>Units short, to be covered by orders</td><td>{e.short_units[0]}</td><td>{e.short_units[1]}</td></tr>
      <tr><td>Spare units used by transfers</td><td colSpan={2}>{e.transfer_use[0]} of {e.transfer_use[1]}</td></tr></tbody></table>
    <p className="why">Shelf stock is a rough stand-in for waste risk. If it rises, the stockout reduction was bought with extra inventory.</p>
  </>
}
