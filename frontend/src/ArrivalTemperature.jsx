import { useState } from 'react'
import { api } from './api'

const PRODUCTS = [
  ['Platelets', '20–24°C'],
  ['Red cells', '2–6°C'],
  ['Whole blood', '2–6°C'],
  ['Frozen plasma', '≤ −18°C'],
  ['Insulin', '2–8°C'],
  ['Vaccines', '2–8°C'],
]

const STATUS_LABELS = {
  optimal: 'Within configured range',
  mild_excursion: 'Mild excursion — expedited review',
  review_required: 'Review required',
  severe_breach_review: 'Severe breach — escalate for review',
}

export default function ArrivalTemperature() {
  const [product, setProduct] = useState('Platelets')
  const [temperature, setTemperature] = useState('')
  const [duration, setDuration] = useState('')
  const [assessment, setAssessment] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError(''); setAssessment(null)
    try {
      const result = await api.checkArrivalTemperature({
        product,
        arrival_temp_c: Number(temperature),
        excursion_duration_minutes: duration === '' ? null : Number(duration),
      })
      setAssessment(result)
    } catch (err) { setError(err.message || 'Temperature check failed.') }
    finally { setBusy(false) }
  }

  const selectedRange = PRODUCTS.find(([name]) => name === product)?.[1]
  return <section className="arrival-temperature">
    <h2>Arrival temperature check</h2>
    <p>Enter the delivery reading to get a temperature-range assessment and next-step guidance.</p>
    <p className="note">Configured ranges: Platelets 20–24°C; Red cells and Whole blood 2–6°C; Frozen plasma ≤ −18°C; Insulin and Vaccines 2–8°C. These are prototype settings and must be confirmed against your product labels and local procedures.</p>
    <form onSubmit={submit}>
      <div className="params">
        <label>Product
          <select className="fld" value={product} onChange={(e) => { setProduct(e.target.value); setAssessment(null) }}>
            {PRODUCTS.map(([name]) => <option key={name}>{name}</option>)}
          </select>
        </label>
        <label>Arrival temperature (°C)
          <input className="fld" type="number" step="0.1" required min="-100" max="60" value={temperature} onChange={(e) => setTemperature(e.target.value)} placeholder="e.g. 23.5" />
          <span className="note">Configured range: {selectedRange}</span>
        </label>
        <label>Known excursion duration (minutes, optional)
          <input className="fld" type="number" min="0" step="1" value={duration} onChange={(e) => setDuration(e.target.value)} placeholder="Enter if known" />
        </label>
      </div>
      <p><button className="p" disabled={busy}>{busy ? 'Checking…' : 'Assess arrival temperature'}</button></p>
    </form>
    {error && <p className="err" role="alert">{error}</p>}
    {assessment && <div className={`temperature-result ${assessment.status}`} role="status" aria-live="polite">
      <h3>{STATUS_LABELS[assessment.status]}</h3>
      <p><b>{assessment.product}:</b> {assessment.arrival_temp_c}°C (configured range {assessment.allowed_range_c})</p>
      {assessment.status !== 'optimal' && <p>Deviation from nearest limit: {assessment.deviation_c}°C</p>}
      <p>{assessment.recommended_action}</p>
      {assessment.excursion_duration_minutes == null && <p className="note">Excursion duration was not provided; temperature alone is not enough to determine product viability.</p>}
      <p className="note"><b>Clinical use authorized:</b> No. Follow product-specific guidance and local SOP; this tool does not make a release or disposal decision.</p>
    </div>}
  </section>
}
