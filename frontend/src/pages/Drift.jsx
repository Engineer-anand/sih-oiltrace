import { useEffect, useState } from 'react'
import PageShell, { Empty, ErrorBox, Loading, ModeChip } from '../components/PageShell.jsx'
import DriftAnimator from '../components/DriftAnimator.jsx'
import { apiJson } from '../lib/api.js'
import { SpillSelector, useSpills } from './Analysis.jsx'

export default function Drift() {
  const { spills, error: listError } = useSpills()
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!selected) { setDetail(null); return }
    setLoading(true); setError('')
    apiJson('/api/v1/spills/' + encodeURIComponent(selected))
      .then(setDetail)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [selected])

  const drift = detail?.drift
  const ellipse = drift?.uncertainty_ellipse

  return (
    <PageShell title="Backward Drift & Origin" crumb="Backward Drift" mode={drift?.env_mode}>
      <p className="muted">OpenDrift backward simulation from the detected slick centroid. Trajectory playback below is a replay of pre-computed model frames — not live data.</p>
      <ErrorBox error={listError || error} />
      {spills === null ? <Loading /> : !spills.length ? <Empty>No saved investigations yet. Run the pipeline from the Dashboard.</Empty> : (
        <SpillSelector spills={spills} value={selected} onChange={setSelected} />
      )}
      {loading ? <Loading label="Loading drift…" /> : drift ? (
        <>
          <div style={{ margin: '8px 0', display: 'flex', gap: 8 }}><ModeChip mode={drift.env_mode} /></div>
          <div className="model-grid">
            <div><small className="muted">Origin (lon, lat)</small><br /><b className="mono">{(drift.origin_centroid || []).join(', ')}</b></div>
            <div><small className="muted">Uncertainty radius</small><br /><b>{drift.uncertainty_radius_km} km</b></div>
            <div><small className="muted">Uncertainty ellipse</small><br /><b>{ellipse ? `${ellipse.semi_major_km} × ${ellipse.semi_minor_km} km @ ${ellipse.orientation_deg}°` : '—'}</b></div>
            <div><small className="muted">Origin probability</small><br /><b>{drift.origin_probability ?? '—'}</b></div>
            <div><small className="muted">Release window (UTC)</small><br /><b className="mono">{(drift.release_time_window || []).join(' → ')}</b></div>
            <div><small className="muted">Particles</small><br /><b>{drift.particle_count}</b></div>
            <div><small className="muted">Seed</small><br /><b className="mono">{drift.seed ?? '—'}</b></div>
            <div><small className="muted">Quality score</small><br /><b>{drift.quality_score ?? '—'}</b></div>
            <div><small className="muted">Environment mode</small><br /><b>{drift.env_mode || (drift.using_synthetic_environment ? 'synthetic' : 'live')}</b></div>
          </div>
          <h3>Trajectories (playback)</h3>
          <p className="muted">{(drift.trajectory_history || []).length} sampled particle tracks · {(drift.ensemble?.coordinates || []).length} final positions.</p>
          <DriftAnimator data={{ origin: { particle_trajectories: drift.trajectory_history || [] } }} />
          <h3>Warnings</h3>
          {(drift.warnings || []).length ? (
            <ul>{drift.warnings.map((w, i) => <li key={i}><small>{w}</small></li>)}</ul>
          ) : <Empty>No warnings recorded for this run.</Empty>}
        </>
      ) : null}
    </PageShell>
  )
}
