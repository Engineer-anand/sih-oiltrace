import { useEffect, useState } from 'react'
import PageShell, { Empty, ErrorBox, Loading, ModeChip } from '../components/PageShell.jsx'
import { apiJson } from '../lib/api.js'
import { SpillSelector, useSpills } from './Analysis.jsx'

function FactorCell({ factors, name }) {
  const f = (factors || []).find((x) => x.factor === name)
  if (!f) return <td className="num">—</td>
  if (f.status === 'UNAVAILABLE') return <td className="num" title={f.note || 'unavailable'}>n/a</td>
  return <td className="num" title={f.status === 'DEGRADED' ? (f.note || 'degraded') : f.status}>{Number(f.raw_score).toFixed(0)}{f.status === 'DEGRADED' ? '*' : ''}</td>
}

export default function Candidates() {
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

  const vessels = detail?.vessels || []
  const mode = detail?.spill?.data_mode

  return (
    <PageShell title="AIS Candidates" crumb="AIS Candidates" mode={mode}>
      <p className="muted">Why each vessel is listed: decomposable component scores with availability status. Scores express statistical association, not responsibility. n/a = factor unavailable for the provider capability.</p>
      <ErrorBox error={listError || error} />
      {spills === null ? <Loading /> : !spills.length ? <Empty>No saved investigations yet. Run the pipeline from the Dashboard.</Empty> : (
        <SpillSelector spills={spills} value={selected} onChange={setSelected} />
      )}
      {loading ? <Loading label="Loading candidates…" /> : detail ? (
        <>
          <div style={{ margin: '8px 0' }}><ModeChip mode={mode} /></div>
          {vessels.length ? (
            <div className="ot-table-wrap"><table className="ot-table">
              <thead><tr><th>Rank</th><th>Vessel</th><th>Type</th><th>MMSI</th>
                <th className="num">Spatial</th><th className="num">Temporal</th><th className="num">Traj.</th>
                <th className="num">Drift</th><th className="num">Behavior</th><th className="num">Profile</th>
                <th className="num">Total</th><th>Confidence</th></tr></thead>
              <tbody>
                {vessels.map((v) => (
                  <tr key={v.vessel_id}>
                    <td className="num">#{v.rank}</td>
                    <td>{v.name || '—'}<br /><small className="muted">{v.vessel_id}</small></td>
                    <td>{v.vessel_type || '—'}</td>
                    <td className="mono">{v.mmsi || '—'}</td>
                    <FactorCell factors={v.factor_list || []} name="proximity" />
                    <FactorCell factors={v.factor_list || []} name="timing" />
                    <FactorCell factors={v.factor_list || []} name="trajectory" />
                    <FactorCell factors={v.factor_list || []} name="drift_overlap" />
                    <FactorCell factors={v.factor_list || []} name="behavior" />
                    <FactorCell factors={v.factor_list || []} name="vessel_type" />
                    <td className="num"><b>{Number(v.total_score).toFixed(1)}</b></td>
                    <td>{v.confidence || (v.unavailable?.length ? 'limited' : '—')}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          ) : <Empty>No candidate vessels for this investigation (provider returned no positions in the window).</Empty>}
          <p className="muted">* degraded factor (approximate). Unavailable factors are excluded from the total with weights renormalized.</p>
        </>
      ) : null}
    </PageShell>
  )
}
