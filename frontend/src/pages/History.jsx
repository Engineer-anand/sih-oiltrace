import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import PageShell, { Empty, ErrorBox, Loading, ModeChip } from '../components/PageShell.jsx'
import { apiJson } from '../lib/api.js'

const PAGE = 20

export default function History() {
  const [spills, setSpills] = useState(null)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = async (off) => {
    setLoading(true); setError('')
    try { setSpills(await apiJson(`/api/v1/spills?limit=${PAGE}&offset=${off}`)) }
    catch (e) { setError(e.message); setSpills([]) }
    finally { setLoading(false) }
  }
  useEffect(() => { load(offset) }, [offset])

  return (
    <PageShell title="Historical Data" crumb="Historical Data">
      <p className="muted">Saved investigations, newest first. Data mode separates live rows from simulated rows.</p>
      <ErrorBox error={error} onRetry={() => load(offset)} />
      {loading ? <Loading /> : !spills?.length ? (
        <Empty>{offset > 0 ? 'No further investigations on this page.' : 'No saved investigations yet. Run the pipeline from the Dashboard.'}</Empty>
      ) : (
        <>
          <div className="ot-table-wrap"><table className="ot-table">
            <thead><tr><th>ID</th><th>Detected (UTC)</th><th className="num">Area km²</th><th className="num">Confidence</th><th>Source</th><th>Mode</th></tr></thead>
            <tbody>
              {spills.map((s) => (
                <tr key={s.id}>
                  <td className="mono"><Link to="/reports">{s.id}</Link></td>
                  <td className="mono">{s.detected_at}</td>
                  <td className="num">{Number(s.area_km2 || 0).toFixed(3)}</td>
                  <td className="num">{s.confidence ?? '—'}</td>
                  <td>{s.source_type || '—'}{s.aws_scene_id ? ` · ${s.aws_scene_id}` : ''}</td>
                  <td><ModeChip mode={s.data_mode} /></td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button type="button" className="ot-row-btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Newer</button>
            <button type="button" className="ot-row-btn" disabled={spills.length < PAGE} onClick={() => setOffset(offset + PAGE)}>Older →</button>
          </div>
        </>
      )}
    </PageShell>
  )
}
