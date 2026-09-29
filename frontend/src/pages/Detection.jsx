import { useEffect, useState } from 'react'
import PageShell, { Empty, ErrorBox, Loading, ModeChip } from '../components/PageShell.jsx'
import { API_BASE, apiJson } from '../lib/api.js'
import { SpillSelector, useSpills } from './Analysis.jsx'

const ASSETS = [['normalized', 'Normalized image'], ['probability', 'Probability map'], ['mask', 'Binary mask'], ['overlay', 'Overlay']]

export default function Detection() {
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

  const model = detail?.spill?.model
  const det = detail?.spill

  return (
    <PageShell title="Detection" crumb="Detection" mode={det?.data_mode}>
      <p className="muted">U-Net segmentation output, model diagnostics, and diagnostic imagery for a saved investigation. Confidence is model output, not certainty of oil.</p>
      <ErrorBox error={listError || error} />
      {spills === null ? <Loading /> : !spills.length ? <Empty>No saved investigations yet. Run the pipeline from the Dashboard.</Empty> : (
        <SpillSelector spills={spills} value={selected} onChange={setSelected} />
      )}
      {loading ? <Loading label="Loading detection…" /> : det ? (
        <>
          <div style={{ margin: '8px 0' }}><ModeChip mode={det.data_mode} /></div>
          <div className="model-grid">
            <div><small className="muted">Spill detected</small><br /><b>{String(!!det.geometry)}</b></div>
            <div><small className="muted">Area</small><br /><b>{Number(det.area_km2 || 0).toFixed(3)} km²</b></div>
            <div><small className="muted">Centroid (lon, lat)</small><br /><b className="mono">{(det.centroid || []).join(', ')}</b></div>
            <div><small className="muted">Satellite</small><br /><b>{det.satellite || '—'}</b></div>
            <div><small className="muted">Acquired (UTC)</small><br /><b>{det.detected_at || '—'}</b></div>
            <div><small className="muted">Source</small><br /><b>{det.source_type || '—'}{det.aws_scene_id ? ` · ${det.aws_scene_id}` : ''}</b></div>
          </div>
          <h3>Model diagnostics</h3>
          {model ? (
            <div className="ot-table-wrap"><table className="ot-table">
              <tbody>
                {[['Model', model.model_name], ['Threshold', model.threshold], ['Positive pixels', model.positive_pixels],
                  ['Positive %', model.positive_percent], ['Probability mean', model.probability_mean],
                  ['Connected components', model.connected_components], ['Georeferenced', String(model.has_georeferencing)],
                  ['CRS', model.crs || '—']].map(([k, v]) => <tr key={k}><td>{k}</td><td className="mono">{String(v ?? '—')}</td></tr>)}
              </tbody>
            </table></div>
          ) : <Empty>No model diagnostics stored for this investigation.</Empty>}
          <h3>Diagnostic imagery</h3>
          <div className="asset-grid">
            {ASSETS.map(([name, label]) => (
              <figure key={name}>
                <img src={`${API_BASE}/api/v1/incidents/${det.id}/assets/${name}.png`} alt={label} loading="lazy"
                  onError={(e) => { e.currentTarget.style.display = 'none' }} />
                <figcaption>{label}</figcaption>
              </figure>
            ))}
          </div>
        </>
      ) : null}
    </PageShell>
  )
}
