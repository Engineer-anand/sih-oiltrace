import { useEffect, useState } from 'react'
import PageShell, { Empty, ErrorBox, Loading, ModeChip } from '../components/PageShell.jsx'
import { apiJson } from '../lib/api.js'

export function useSpills() {
  const [spills, setSpills] = useState(null)
  const [error, setError] = useState('')
  const load = async () => {
    setError('')
    try { setSpills(await apiJson('/api/v1/spills?limit=100')) }
    catch (e) { setError(e.message); setSpills([]) }
  }
  useEffect(() => { load() }, [])
  return { spills, error, reload: load }
}

export function SpillSelector({ spills, value, onChange, label }) {
  if (!spills?.length) return null
  return (
    <div className="input-group">
      <label className="input-label" htmlFor="spill-select">{label || 'Investigation'}</label>
      <select id="spill-select" className="ot-select" value={value || ''} onChange={(e) => onChange(e.target.value)}>
        <option value="">Select an investigation…</option>
        {spills.map((s) => (
          <option key={s.id} value={s.id}>{s.id} · {Number(s.area_km2 || 0).toFixed(2)} km² · {s.data_mode || 'unknown mode'}</option>
        ))}
      </select>
    </div>
  )
}

export default function Analysis() {
  const [health, setHealth] = useState(null)
  const [ready, setReady] = useState(null)
  const [metrics, setMetrics] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true); setError('')
    try {
      const [h, r, m] = await Promise.all([
        apiJson('/health'),
        apiJson('/api/v1/health/ready'),
        apiJson('/metrics'),
      ])
      setHealth(h); setReady(r); setMetrics(m)
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const deps = ready ? Object.entries(ready.dependencies || {}) : []
  const counters = metrics ? Object.entries(metrics.counters || {}) : []
  const lat = metrics ? Object.entries(metrics.latency_ms || {}) : []

  return (
    <PageShell title="System Analysis" crumb="Analysis">
      <p className="muted">Liveness, readiness per dependency, and operational metrics. Synthetic fallback usage in production must be zero.</p>
      <ErrorBox error={error} onRetry={load} />
      {loading ? <Loading label="Checking system…" /> : !health ? <Empty>No system data. The backend may be unreachable.</Empty> : (
        <>
          <div className="model-grid">
            <div><small className="muted">Liveness</small><br /><b>{health.status}</b></div>
            <div><small className="muted">Readiness</small><br /><b>{ready?.status || '—'}</b></div>
            <div><small className="muted">Application mode</small><br /><b>{health.app_mode || '—'}</b></div>
            <div><small className="muted">Model loaded</small><br /><b>{String(health.model_loaded)}</b></div>
            <div><small className="muted">Synthetic fallbacks</small><br /><b>{metrics?.synthetic_fallback_total ?? '—'}</b></div>
            <div><small className="muted">Detection mode</small><br /><ModeChip mode={health.app_mode === 'sih_demo' ? 'SCENARIO' : 'LIVE'} /></div>
          </div>
          <h3>Dependencies</h3>
          <div className="ot-table-wrap"><table className="ot-table">
            <thead><tr><th>Dependency</th><th>Status</th></tr></thead>
            <tbody>{deps.map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td>{v}</td></tr>)}</tbody>
          </table></div>
          <h3>Counters</h3>
          {counters.length ? (
            <div className="ot-table-wrap"><table className="ot-table">
              <thead><tr><th>Metric</th><th className="num">Value</th></tr></thead>
              <tbody>{counters.map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="num">{v}</td></tr>)}</tbody>
            </table></div>
          ) : <Empty>No counters recorded yet.</Empty>}
          <h3>Latency (ms)</h3>
          {lat.length ? (
            <div className="ot-table-wrap"><table className="ot-table">
              <thead><tr><th>Operation</th><th className="num">Count</th><th className="num">p50</th><th className="num">p95</th><th className="num">Max</th></tr></thead>
              <tbody>{lat.map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="num">{v.count}</td><td className="num">{v.p50}</td><td className="num">{v.p95}</td><td className="num">{v.max}</td></tr>)}</tbody>
            </table></div>
          ) : <Empty>No latency samples recorded yet.</Empty>}
        </>
      )}
    </PageShell>
  )
}
