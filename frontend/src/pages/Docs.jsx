import PageShell from '../components/PageShell.jsx'
import { API_BASE } from '../lib/api.js'

export default function Docs() {
  return (
    <PageShell title="Documentation" crumb="Documentation">
      <p className="muted">Operator documentation for the OilTrace research prototype. OilTrace is an independent research application — not a government service, and it makes no official-authority claims.</p>
      <h3>Pipeline</h3>
      <p>Sentinel-1 scene (archive search or file upload) → U-Net segmentation → polygon + EPSG:4326 geometry → OpenDrift backward drift → origin + uncertainty → AIS presence query in the release window → decomposable attribution scoring → evidence checklist → PDF/GeoJSON/CSV exports. Every stage persists to the database; jobs survive restarts.</p>
      <h3>Data modes</h3>
      <div className="ot-table-wrap"><table className="ot-table">
        <thead><tr><th>Mode</th><th>Meaning</th></tr></thead>
        <tbody>
          {[['LIVE', 'Real provider data within freshness thresholds.'], ['ARCHIVE', 'Real provider data served from cache; age is shown.'],
          ['FALLBACK', 'Substitute data after a real-source failure; development only.'], ['SIMULATED', 'Controlled illustrative data for walkthroughs; isolated from live records.'],
          ['DEGRADED', 'Real data used but a quality gate failed; warnings attached.'], ['FAILED', 'Required real data unavailable; no output fabricated.']].map(([m, d]) => (
            <tr key={m}><td className="mono">{m}</td><td>{d}</td></tr>
          ))}
        </tbody>
      </table></div>
      <h3>AIS capabilities</h3>
      <p>The Global Fishing Watch source provides <b>AIS presence</b>, not vessel tracks: positions without speed or heading. Trajectory and behavior factors are therefore <b>unavailable</b> (excluded from totals with renormalized weights), never neutral constants. Simulated and fallback positions are synthetic and labeled.</p>
      <h3>Attribution and limits</h3>
      <p>Scores express statistical association between vessels and the estimated origin. They are investigative evidence and do not establish responsibility. Detection confidence is model output, not certainty of oil. Environmental fields carry coverage, age, and source metadata; low-quality runs are marked degraded, not completed.</p>
      <h3>API reference</h3>
      <p>Interactive schema and per-route contracts are served by the backend: <a href={`${API_BASE}/docs`} target="_blank" rel="noopener noreferrer">Swagger UI</a>. Health: <a href={`${API_BASE}/health`} target="_blank" rel="noopener noreferrer">/health</a> (liveness), <code>/api/v1/health/ready</code> (readiness), <code>/metrics</code> (operations).</p>
      <h3>Walkthrough mode</h3>
      <p>Use the controlled walkthrough configuration for a deterministic run against a scratch database. Live and simulated records remain separated.</p>
    </PageShell>
  )
}
