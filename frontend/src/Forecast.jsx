import { useEffect, useState } from 'react'
import { ComposedChart, Line, Area, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer, ReferenceLine } from 'recharts'
import { api } from './api'

export default function Forecast({ rows, params }) {
  const ok = rows.filter((r) => !r.uncertain)
  const [sel, setSel] = useState(0)
  const [d, setD] = useState(null)
  const r = ok[Math.min(sel, ok.length - 1)]
  useEffect(() => { if (r) api.forecast(r.facility, r.medicine, params).then(setD).catch(() => setD(null)) }, [r?.facility, r?.medicine, params])
  if (!r) return <p className="empty">No pair has enough reliable data for a forecast.</p>
  const pts = d ? [...d.history.map((v, i) => ({ day: i - 27, actual: v })),
    ...d.forecast.map(([m, lo, hi], i) => ({ day: i + 1, median: m, band: [lo, hi] }))] : []
  const total = d ? d.forecast.reduce((s, x) => s + x[0], 0) : 0, high = d ? d.forecast.reduce((s, x) => s + x[2], 0) : 0
  return <>
    <label>Medicine at facility <select value={sel} onChange={(e) => setSel(+e.target.value)}>
      {ok.map((x, i) => <option key={i} value={i}>{x.medicine} - {x.facility}</option>)}</select></label>
    <div style={{ height: 300, minWidth: 520 }}><ResponsiveContainer>
      <ComposedChart data={pts}><CartesianGrid strokeDasharray="3 3" opacity={0.3} />
        <XAxis dataKey="day" type="number" domain={[-27, 14]} tickFormatter={(v) => (v > 0 ? `+${v}` : v)} />
        <YAxis /><Tooltip /><ReferenceLine x={0} stroke="gray" />
        <Area dataKey="band" fill="var(--acc)" stroke="none" fillOpacity={0.25} name="Likely range" />
        <Line dataKey="actual" stroke="var(--ink)" dot={false} strokeWidth={2} name="Actual use" />
        <Line dataKey="median" stroke="var(--acc)" dot={false} strokeWidth={2.5} strokeDasharray="6 4" name="Forecast" /></ComposedChart>
    </ResponsiveContainer></div>
    {d && <p className="why">Forecast = level of the last 14 days x weekday pattern. The shaded band is the likely range (80%) from past forecast errors.
      Expect about <b>{Math.ceil(total)}</b> units over 14 days; plan for up to <b>{Math.ceil(high)}</b> to stay safe.</p>}
  </>
}
