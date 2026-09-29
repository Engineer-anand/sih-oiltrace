import { useEffect, useState } from 'react'
import { apiJson } from '../lib/api.js'

/**
 * Live system telemetry.
 *
 * Everything shown here is read from the backend on a timer — /health
 * (liveness + model state), /api/v1/health/ready (per-dependency readiness)
 * and /metrics (counters + latency percentiles). Nothing is synthesised for
 * display: if a field is absent it renders as an em dash rather than a
 * placeholder that could be mistaken for a measurement.
 *
 * The visible UTC clock is deliberate. A ticking, unambiguous timestamp is
 * what makes a console read as a running system rather than a screenshot.
 */

function useUtcClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return now
}

function fmtUtc(date) {
  if (!date) return '—'
  return date.toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, ' UTC')
}

function fmtTime(date) {
  if (!date) return '—'
  return date.toISOString().slice(11, 19) + 'Z'
}

/** Human labels for known metric counters; unknown keys are prettified. */
const COUNTER_LABELS = {
  jobs_created: 'Jobs created',
  jobs_completed: 'Jobs completed',
  jobs_failed: 'Jobs failed',
  synthetic_fallback_total: 'Substitutions',
  provider_failure_OpenDrift: 'Drift provider failures',
}

function prettyKey(key) {
  if (COUNTER_LABELS[key]) return COUNTER_LABELS[key]
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function readinessTone(status) {
  if (status === 'ready') return 'ok'
  if (status === 'not-ready') return 'bad'
  return 'info'
}

export default function SystemTelemetry({ variant = 'strip' }) {
  const [health, setHealth] = useState(null)
  const [ready, setReady] = useState(null)
  const [metrics, setMetrics] = useState(null)
  const [error, setError] = useState(null)
  const [refreshedAt, setRefreshedAt] = useState(null)
  const now = useUtcClock()

  useEffect(() => {
    let cancelled = false

    async function load() {
      const [h, r, m] = await Promise.all([
        apiJson('/health').catch(() => null),
        apiJson('/api/v1/health/ready').catch(() => null),
        apiJson('/metrics').catch(() => null),
      ])
      if (cancelled) return
      setHealth(h)
      setReady(r)
      setMetrics(m)
      setError(h ? null : 'Backend unreachable')
      setRefreshedAt(new Date())
    }

    load()
    const id = setInterval(load, 10000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  const operational = health?.status === 'ok'
  const counters = metrics?.counters || {}
  const latency = metrics?.latency_ms || {}
  const p50 = latency['api_latency:/api/v1/pipeline/jobs']
  const p95 = p50?.p95 ?? latency['api_latency:/health']?.p95 ?? null

  const headline = error ? 'Unavailable' : operational ? 'Operational' : health ? 'Degraded' : 'Checking…'
  const tone = error ? 'bad' : operational ? 'ok' : 'info'

  // ---- compact strip: for the landing page -------------------------------
  if (variant === 'strip') {
    return (
      <div className="ot-telemetry" role="status" aria-live="polite">
        <div className="ot-telemetry-cell">
          <span className="ot-telemetry-label">System status</span>
          <span className={`ot-telemetry-value ot-tone-${tone}`}>
            <span className={`ot-dot ot-dot-${tone}`} aria-hidden="true" />
            {headline}
          </span>
        </div>
        <div className="ot-telemetry-cell">
          <span className="ot-telemetry-label">Application mode</span>
          <span className="ot-telemetry-value mono">{health?.app_mode || '—'}</span>
        </div>
        <div className="ot-telemetry-cell">
          <span className="ot-telemetry-label">Detection model</span>
          <span className="ot-telemetry-value">
            {health == null ? '—' : health.model_loaded ? 'Loaded' : 'Unavailable'}
          </span>
        </div>
        <div className="ot-telemetry-cell">
          <span className="ot-telemetry-label">Readiness</span>
          <span className={`ot-telemetry-value ot-tone-${readinessTone(ready?.status)}`}>
            {ready?.status || '—'}
          </span>
        </div>
        <div className="ot-telemetry-cell">
          <span className="ot-telemetry-label">UTC</span>
          <span className="ot-telemetry-value mono">{fmtTime(now)}</span>
        </div>
      </div>
    )
  }

  // ---- panel: for the dashboard / status page ---------------------------
  const topCounters = Object.entries(counters)
    .filter(([k]) => !k.startsWith('api_latency'))
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)

  const dependencyRows = Object.entries(ready?.dependencies || {})

  return (
    <div className="ot-telemetry-panel">
      <div className="ot-panel-head">
        <b>System telemetry</b>
        <span className="muted mono">
          {fmtUtc(now)} · refreshed {fmtTime(refreshedAt)}
        </span>
      </div>

      {error ? <div className="error-box" role="alert">{error}</div> : null}

      <div className="ot-grid-3">
        <div className="ot-metric">
          <span className="ot-telemetry-label">Application mode</span>
          <span className="mono">{health?.app_mode || '—'}</span>
        </div>
        <div className="ot-metric">
          <span className="ot-telemetry-label">Pipeline mode</span>
          <span className="mono">{health?.mode || '—'}</span>
        </div>
        <div className="ot-metric">
          <span className="ot-telemetry-label">Model resident</span>
          <span>{health == null ? '—' : health.model_loaded ? 'Yes' : 'No'}</span>
        </div>
      </div>

      <h4 className="ot-subhead">Dependencies</h4>
      {dependencyRows.length === 0 ? (
        <p className="muted">No readiness data reported.</p>
      ) : (
        <div className="ot-table-wrap">
          <table className="ot-table">
            <thead>
              <tr><th>Dependency</th><th>State</th></tr>
            </thead>
            <tbody>
              {dependencyRows.map(([name, state]) => (
                <tr key={name}>
                  <td className="mono">{name}</td>
                  <td className={state === 'ok' ? 'ot-tone-ok' : state === 'fail' ? 'ot-tone-bad' : 'ot-tone-info'}>
                    {state}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {ready?.provider_requirement === 'not-required-in-sih_demo' ? (
        <p className="muted">
          Provider credentials are intentionally absent in this mode. Scenario substitution applies to:{' '}
          <span className="mono">{(ready.scenario_substitution || []).join(', ') || '—'}</span>. Every affected
          result is labelled <span className="mono">SCENARIO</span> in the API, the database and the report.
        </p>
      ) : null}

      <h4 className="ot-subhead">Counters</h4>
      {topCounters.length === 0 ? (
        <p className="muted">No counters recorded yet this session.</p>
      ) : (
        <div className="ot-table-wrap">
          <table className="ot-table">
            <thead>
              <tr><th>Metric</th><th className="ot-num">Value</th></tr>
            </thead>
            <tbody>
              {topCounters.map(([key, value]) => (
                <tr key={key}>
                  <td>{prettyKey(key)}</td>
                  <td className="ot-num mono">{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h4 className="ot-subhead">API latency</h4>
      <div className="ot-grid-3">
        <div className="ot-metric">
          <span className="ot-telemetry-label">p50</span>
          <span className="mono">{p50?.p50 != null ? `${p50.p50} ms` : '—'}</span>
        </div>
        <div className="ot-metric">
          <span className="ot-telemetry-label">p95</span>
          <span className="mono">{p95 != null ? `${p95} ms` : '—'}</span>
        </div>
        <div className="ot-metric">
          <span className="ot-telemetry-label">Samples</span>
          <span className="mono">{p50?.count ?? '—'}</span>
        </div>
      </div>
    </div>
  )
}
